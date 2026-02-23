"""LLM wrapper using openhands-sdk.

Provides helper functions for tool-calling workflows.
Uses openhands-sdk's LLM for config/auth and litellm for the actual calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import litellm
from pydantic import SecretStr

from openhands.sdk import LLM, Message, TextContent
from openhands.sdk.llm.llm_response import LLMResponse

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

# ccman local proxy — same endpoint Claude Code uses
_CCMAN_BASE_URL = "http://127.0.0.1:15721"
_CCMAN_DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"


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

    Priority: explicit args > ccman local proxy > openhands config.toml.
    """
    cfg = _read_config_toml()

    # If no explicit base_url, prefer ccman proxy (same as Claude Code)
    if not base_url and not model and _ccman_available():
        resolved_model = _CCMAN_DEFAULT_MODEL
        resolved_base_url = _CCMAN_BASE_URL
        resolved_key = "proxy-managed"
        logger.info("Using ccman proxy at %s", _CCMAN_BASE_URL)
    else:
        resolved_model = model or cfg.get("model", "openai/gpt-5.3-codex")
        resolved_base_url = base_url or cfg.get("base_url")
        resolved_key = api_key or cfg.get("api_key", "")

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
        # Gateway proxies don't support these OpenAI-specific params
        reasoning_effort=None,
        prompt_cache_retention=None,
    )
    # Strip params that gateways may reject from Responses API calls
    _patch_responses_drop_params(llm)
    return llm


def _patch_responses_drop_params(llm: LLM) -> None:
    """Strip unsupported params and fix input for Responses API requests.

    The aistock.tech gateway:
    1. Returns 502 on `temperature`, `top_p`, etc.
    2. Returns 502 if `function_call` items lack matching `function_call_output`.

    We intercept at the litellm responses level to fix both issues.
    """
    import litellm as _litellm
    import openhands.sdk.llm.llm as _sdk_llm_module

    if getattr(_litellm, '_toyshop_responses_patched', False):
        return

    _original_responses = _sdk_llm_module.litellm_responses

    _DROP_PARAMS = {'temperature', 'top_p', 'presence_penalty', 'frequency_penalty'}

    def _ensure_function_call_outputs(input_items: list) -> list:
        """Ensure every function_call has a matching function_call_output."""
        if not isinstance(input_items, list):
            return input_items

        # Collect call_ids that have outputs
        output_ids = set()
        for item in input_items:
            if isinstance(item, dict) and item.get('type') == 'function_call_output':
                output_ids.add(item.get('call_id'))

        # Find function_calls missing outputs and inject placeholder
        result = []
        for item in input_items:
            result.append(item)
            if isinstance(item, dict) and item.get('type') == 'function_call':
                call_id = item.get('call_id') or item.get('id')
                if call_id and call_id not in output_ids:
                    result.append({
                        'type': 'function_call_output',
                        'call_id': call_id,
                        'output': 'Tool executed successfully.',
                    })
                    output_ids.add(call_id)
        return result

    def _filtered_responses(*args, **kwargs):
        for p in _DROP_PARAMS:
            kwargs.pop(p, None)
        # Fix missing function_call_output
        if 'input' in kwargs:
            kwargs['input'] = _ensure_function_call_outputs(kwargs['input'])
        return _original_responses(*args, **kwargs)

    _sdk_llm_module.litellm_responses = _filtered_responses
    _litellm.responses = _filtered_responses
    _litellm._toyshop_responses_patched = True


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
    )

    # Strip "anthropic/" prefix for native client
    model_id = llm.model.removeprefix("anthropic/")

    response = client.messages.create(
        model=model_id,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        tools=[tool_def],
        tool_choice={"type": "tool", "name": tool_name},
        max_tokens=4096,
    )

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
    """Tool-calling via OpenAI Responses API (for aistock gateway)."""
    tool = _make_responses_tool(tool_name, tool_description, tool_parameters)

    # Embed system prompt in user content since gateway overrides instructions
    combined_input = f"{system_prompt}\n\n{user_content}"

    response = litellm.responses(
        model=llm.model,
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
