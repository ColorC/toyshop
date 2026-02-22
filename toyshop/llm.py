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
    return LLM(
        model=model or cfg.get("model", "openai/glm-5"),
        api_key=SecretStr(api_key or cfg.get("api_key", "")),
        base_url=base_url or cfg.get("base_url"),
        temperature=temperature,
        timeout=timeout,
        usage_id="toyshop",
        drop_params=True,
        native_tool_calling=True,
        num_retries=2,
        retry_min_wait=2,
        retry_max_wait=10,
    )


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
        temperature=llm.temperature,
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
