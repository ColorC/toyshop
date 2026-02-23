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
    """Create an LLM instance, defaulting to openhands config.toml values."""
    cfg = _read_config_toml()
    llm = LLM(
        model=model or cfg.get("model", "openai/gpt-5.3-codex"),
        api_key=SecretStr(api_key or cfg.get("api_key", "")),
        base_url=base_url or cfg.get("base_url"),
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
    # Force Chat Completions API — aistock.tech gateway returns 502 on /v1/responses
    _force_chat_completions(llm)
    # Flatten role:tool messages — gateway returns 502 on tool_calls/role:tool
    _patch_message_flattening(llm)
    return llm


def _force_chat_completions(llm: LLM) -> None:
    """Override uses_responses_api to always return False.

    The aistock.tech gateway only supports Chat Completions (/v1/chat/completions),
    not the Responses API (/v1/responses). Without this, openhands-sdk detects
    gpt-5.3-codex as supporting Responses API and sends requests to the wrong endpoint.
    """
    # Bypass pydantic's __setattr__ which rejects non-field attributes
    object.__setattr__(llm, 'uses_responses_api', lambda: False)


def _content_to_str(content: Any) -> str:
    """Extract plain text from content (str, list of dicts, or None)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _compress_tool_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress multi-turn tool-calling history for gateways that don't support it.

    The aistock.tech gateway returns 502 when messages contain `role: tool`
    or `tool_calls` in assistant messages. Instead of flattening (which confuses
    the LLM's tool-calling behavior), we compress the history:

    - Extract the system prompt and first user message
    - Collect all completed tool calls into a summary
    - Inject the summary into the system prompt
    - Return a fresh [system, user] message pair

    This preserves the LLM's ability to make new tool calls while avoiding
    the unsupported message formats.
    """
    if not any(m.get("role") == "tool" for m in messages):
        return messages

    # Extract components
    system_prompt = ""
    user_message = ""
    tool_history: list[tuple[str, str, str]] = []  # (name, args, result)
    last_assistant_text = ""

    for msg in messages:
        role = msg.get("role")
        content = _content_to_str(msg.get("content"))

        if role == "system":
            system_prompt = content
        elif role == "user" and not user_message:
            # Capture the first (original) user message
            user_message = content
        elif role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if "function" in tc:
                    tool_history.append((
                        tc["function"]["name"],
                        tc["function"].get("arguments", "{}"),
                        "",  # result filled by next tool message
                    ))
            last_assistant_text = content
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            tool_result = content
            # Update the last tool_history entry with the result
            if tool_history and not tool_history[-1][2]:
                name, args, _ = tool_history[-1]
                tool_history[-1] = (name, args, tool_result)
            else:
                tool_history.append((tool_name, "{}", tool_result))
        elif role == "assistant":
            last_assistant_text = content

    # Build compressed system prompt
    if tool_history:
        completed_names = [h[0] for h in tool_history]
        history_lines = []
        for name, args, result in tool_history:
            # Truncate long args/results to keep context manageable
            args_short = args[:200] + ("..." if len(args) > 200 else "")
            result_short = result[:200] + ("..." if len(result) > 200 else "")
            history_lines.append(
                f"  - {name}({args_short}) → {result_short}"
            )
        history_section = (
            "\n\nTools already executed (do NOT call these again):\n"
            + "\n".join(history_lines)
        )

        # Find available tools from the original system prompt context
        # and tell the LLM what to do next
        history_section += (
            "\n\nContinue with the next tool that hasn't been called yet. "
            "Call exactly ONE tool."
        )

        system_prompt = system_prompt + history_section

    result = [{"role": "system", "content": system_prompt}]
    if user_message:
        result.append({"role": "user", "content": user_message})
    # If there was a non-tool-call assistant message at the end, include it
    # to maintain conversation flow
    if last_assistant_text and not tool_history:
        result.append({"role": "assistant", "content": last_assistant_text})

    return result


def _patch_message_flattening(llm: LLM) -> None:
    """Monkey-patch litellm.completion to flatten tool messages before sending.

    This intercepts at the lowest level — right before the HTTP call — so that
    openhands-sdk's agent loop sees normal tool_calls/role:tool messages
    internally, but the gateway only receives flattened user/assistant messages.

    We patch both litellm.completion AND the SDK's cached reference to it.
    """
    import litellm as _litellm
    import openhands.sdk.llm.llm as _sdk_llm_module

    # Only patch once
    if getattr(_litellm, '_toyshop_patched', False):
        return

    _original_completion = _sdk_llm_module.litellm_completion

    def _patched_completion(*args, **kwargs):
        messages = kwargs.get('messages') or (args[1] if len(args) > 1 else None)
        if messages and any(
            isinstance(m, dict) and m.get('role') == 'tool' for m in messages
        ):
            kwargs['messages'] = _compress_tool_history(messages)
        return _original_completion(*args, **kwargs)

    # Patch both the litellm module and the SDK's cached reference
    _litellm.completion = _patched_completion
    _sdk_llm_module.litellm_completion = _patched_completion
    _litellm._toyshop_patched = True


# ---------------------------------------------------------------------------
# Tool-calling helpers
# ---------------------------------------------------------------------------

def _make_tool_schema(
    name: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """Build an OpenAI-style function tool dict."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def chat_with_tool(
    llm: LLM,
    system_prompt: str,
    user_content: str,
    tool_name: str,
    tool_description: str,
    tool_parameters: dict[str, Any],
) -> dict[str, Any] | None:
    """Single-turn tool-calling: system + user → LLM → extract tool args.

    Uses litellm.completion directly (same backend as openhands-sdk LLM)
    with the LLM instance's config for model/api_key/base_url.

    Returns parsed tool arguments dict, or None if no tool call was made.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    tool = _make_tool_schema(tool_name, tool_description, tool_parameters)

    response = litellm.completion(
        model=llm.model,
        messages=messages,
        tools=[tool],
        tool_choice="auto",
        api_key=llm.api_key.get_secret_value() if llm.api_key else None,
        base_url=llm.base_url,
        timeout=llm.timeout,
        drop_params=True,
    )

    # Extract tool call arguments
    choice = response.choices[0]
    msg = choice.message
    if msg.tool_calls:
        for tc in msg.tool_calls:
            if tc.function.name == tool_name:
                return _parse_arguments(tc.function.arguments)
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
