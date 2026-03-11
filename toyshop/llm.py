"""LLM wrapper using openhands-sdk.

Provides helper functions for tool-calling workflows.
Uses openhands-sdk's LLM for config/auth and litellm for the actual calls.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import litellm
from pydantic import SecretStr

from openhands.sdk import LLM, Message, TextContent
from openhands.sdk.llm.llm_response import LLMResponse

from toyshop.llm_gateway import apply_gateway_compat_patch

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

_CONFIG_SEARCH_PATHS = [
    # From python/toyshop/llm.py -> extensions/toyshop -> extensions -> openclaw -> work -> openhands
    Path(__file__).resolve().parents[5] / "openhands" / "config.toml.local",
    Path(__file__).resolve().parents[5] / "openhands" / "config.toml",
    Path.home() / "work" / "openhands" / "config.toml.local",
    Path.home() / "work" / "openhands" / "config.toml",
    Path("/home/dministrator/work/openhands/config.toml.local"),
    Path("/home/dministrator/work/openhands/config.toml"),
]

# Claude Code proxy — reads from ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN env vars
_CLAUDE_CODE_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")
_CLAUDE_CODE_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
_CLAUDE_CODE_DEFAULT_MODEL = "anthropic/claude-opus-4-6"

# Legacy ccman local proxy (deprecated — use Claude Code env vars instead)
_CCMAN_BASE_URL = "http://127.0.0.1:15721"
_CCMAN_DEFAULT_MODEL = "anthropic/claude-opus-4-6"


def _ccman_available() -> bool:
    """Check if ccman local proxy is reachable."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 15721), timeout=1):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _read_config_toml() -> dict[str, str]:
    """Read model/api_key/base_url from the first openhands config.toml found."""
    for p in _CONFIG_SEARCH_PATHS:
        if p.exists():
            text = p.read_text()
            result: dict[str, str] = {}
            for key in ("model", "api_key", "base_url"):
                m = re.search(rf'{key}\s*=\s*"([^"]+)"', text)
                if m:
                    result[key] = m.group(1)
            if result:
                return result
    return {}


def create_llm(
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.3,
    timeout: int = 180,
) -> LLM:
    """Create an LLM instance.

    Priority: explicit args > ANTHROPIC_BASE_URL env (Claude Code proxy) > ccman > config.toml.
    """
    cfg = _read_config_toml()

    # 1. Explicit args take precedence
    if base_url or model:
        resolved_model = model or cfg.get("model", "openai/gpt-5.3-codex")
        resolved_base_url = base_url or cfg.get("base_url")
        resolved_key = api_key or cfg.get("api_key", "")
    # 2. Claude Code proxy (ANTHROPIC_BASE_URL env var)
    elif _CLAUDE_CODE_BASE_URL:
        resolved_model = _CLAUDE_CODE_DEFAULT_MODEL
        resolved_base_url = _CLAUDE_CODE_BASE_URL
        resolved_key = _CLAUDE_CODE_AUTH_TOKEN or "PROXY_MANAGED"
        logger.info("Using Claude Code proxy at %s", resolved_base_url)
    # 3. Legacy ccman proxy
    elif _ccman_available():
        resolved_model = _CCMAN_DEFAULT_MODEL
        resolved_base_url = _CCMAN_BASE_URL
        resolved_key = "proxy-managed"
        logger.info("Using ccman proxy at %s", _CCMAN_BASE_URL)
    # 4. config.toml fallback
    elif cfg:
        resolved_model = cfg.get("model", "openai/gpt-5.3-codex")
        resolved_base_url = cfg.get("base_url")
        resolved_key = api_key or cfg.get("api_key", "")
    # 5. Bare fallback
    else:
        resolved_model = "openai/gpt-5.3-codex"
        resolved_base_url = None
        resolved_key = ""

    llm = LLM(
        model=resolved_model,
        api_key=SecretStr(resolved_key),
        base_url=resolved_base_url,
        temperature=temperature,
        timeout=timeout,
        usage_id="toyshop",
        drop_params=True,
        native_tool_calling=True,
        num_retries=5,
        retry_min_wait=3,
        retry_max_wait=30,
        stream=True,  # Keep connection alive, avoid proxy timeout on long requests
        # Gateway proxies don't support these OpenAI-specific params
        reasoning_effort=None,
        prompt_cache_retention=None,
    )
    # Strip params that gateways may reject from Responses API calls
    apply_gateway_compat_patch()
    return llm



# ---------------------------------------------------------------------------
# Tool-calling helpers
# ---------------------------------------------------------------------------

def _make_responses_tool(
    name: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """Build a Responses API function tool dict."""
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
        "strict": False,
    }


def chat_with_tool(
    llm: LLM,
    system_prompt: str,
    user_content: str,
    tool_name: str,
    tool_description: str,
    tool_parameters: dict[str, Any],
) -> dict[str, Any] | None:
    """Single-turn tool-calling — auto-selects Messages or Responses API.

    Returns parsed tool arguments dict, or None if no tool call was made.
    """
    # Claude Code proxy and direct Anthropic endpoints use Messages API.
    # Only use Responses API for non-Anthropic providers (aistock gateway, etc.)
    if llm.model.startswith("anthropic/"):
        return _chat_with_tool_messages(
            llm, system_prompt, user_content,
            tool_name, tool_description, tool_parameters,
        )
    return _chat_with_tool_responses(
        llm, system_prompt, user_content,
        tool_name, tool_description, tool_parameters,
    )


def _chat_with_tool_messages(
    llm: LLM,
    system_prompt: str,
    user_content: str,
    tool_name: str,
    tool_description: str,
    tool_parameters: dict[str, Any],
) -> dict[str, Any] | None:
    """Tool-calling via Anthropic Messages API (for ccman proxy)."""
    import anthropic as _anthropic

    # Build Anthropic-native tool definition
    tool_def = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": tool_parameters,
    }

    client = _anthropic.Anthropic(
        api_key=llm.api_key.get_secret_value() if llm.api_key else "proxy-managed",
        base_url=llm.base_url,
        timeout=llm.timeout,
        max_retries=5,
    )

    # Strip "anthropic/" prefix for native client
    model_id = llm.model.removeprefix("anthropic/")

    # Use streaming to match Claude Code's behavior — keeps connection alive
    # and avoids proxy upstream disconnecting during long non-streaming waits.
    # Force tool use to prevent the model from responding with plain text.
    with client.messages.stream(
        model=model_id,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        tools=[tool_def],
        tool_choice={"type": "tool", "name": tool_name},
        max_tokens=8192,
    ) as stream:
        response = stream.get_final_message()

    # Extract tool use from response
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input if isinstance(block.input, dict) else None
    return None


def _chat_with_tool_responses(
    llm: LLM,
    system_prompt: str,
    user_content: str,
    tool_name: str,
    tool_description: str,
    tool_parameters: dict[str, Any],
) -> dict[str, Any] | None:
    """Tool-calling via OpenAI Responses API (for aistock gateway / ccman proxy)."""
    tool = _make_responses_tool(tool_name, tool_description, tool_parameters)

    # Embed system prompt in user content since gateway overrides instructions
    combined_input = f"{system_prompt}\n\n{user_content}"

    # ccman proxy only speaks Responses API — never route to Messages API.
    # Also strip "anthropic/" prefix so litellm doesn't internally convert
    # Responses API calls back to Anthropic Messages API.
    model = llm.model
    if llm.base_url == _CCMAN_BASE_URL and model.startswith("anthropic/"):
        model = model.removeprefix("anthropic/")

    response = litellm.responses(
        model=model,
        input=[{"role": "user", "content": combined_input}],
        tools=[tool],
        tool_choice="required",
        api_key=llm.api_key.get_secret_value() if llm.api_key else None,
        api_base=llm.base_url,
        timeout=llm.timeout,
    )

    # Extract tool call from Responses API output
    for item in response.output:
        if getattr(item, "type", None) == "function_call":
            if item.name == tool_name:
                return _parse_arguments(item.arguments)
    return None


def probe_llm(llm: LLM, timeout: int = 15) -> tuple[bool, str]:
    """Fast LLM availability check — auto-selects protocol like chat_with_tool.

    Returns (ok, error_message).
    """
    if llm.model.startswith("anthropic/"):
        import anthropic as _anthropic
        try:
            client = _anthropic.Anthropic(
                api_key=llm.api_key.get_secret_value() if llm.api_key else "proxy-managed",
                base_url=llm.base_url,
                timeout=timeout,
            )
            with client.messages.stream(
                model=llm.model.removeprefix("anthropic/"),
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
            ) as stream:
                stream.get_final_message()
            return True, ""
        except Exception as e:
            return False, str(e)
    else:
        try:
            litellm.responses(
                model=llm.model,
                input=[{"role": "user", "content": "ping"}],
                api_key=llm.api_key.get_secret_value() if llm.api_key else None,
                api_base=llm.base_url,
                timeout=timeout,
                num_retries=0,
                max_output_tokens=8,
            )
            return True, ""
        except Exception as e:
            return False, str(e)


def _parse_arguments(raw: str) -> dict[str, Any] | None:
    """Parse JSON arguments with repair fallback."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r"\}[^}]*$", "}", raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None
