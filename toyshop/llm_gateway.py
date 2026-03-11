"""Gateway compatibility helpers for LLM Responses API."""

from __future__ import annotations

from typing import Any

_DROP_PARAMS = {"temperature", "top_p", "presence_penalty", "frequency_penalty"}


def ensure_function_call_outputs(input_items: list[Any] | Any) -> list[Any] | Any:
    """Ensure every function_call has a matching function_call_output."""
    if not isinstance(input_items, list):
        return input_items

    output_ids = set()
    for item in input_items:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            output_ids.add(item.get("call_id"))

    result: list[Any] = []
    for item in input_items:
        result.append(item)
        if isinstance(item, dict) and item.get("type") == "function_call":
            call_id = item.get("call_id") or item.get("id")
            if call_id and call_id not in output_ids:
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": "Tool executed successfully.",
                    }
                )
                output_ids.add(call_id)
    return result


def strip_unsupported_responses_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop parameters that certain gateway proxies reject."""
    filtered = dict(kwargs)
    for p in _DROP_PARAMS:
        filtered.pop(p, None)
    if "input" in filtered:
        filtered["input"] = ensure_function_call_outputs(filtered["input"])
    return filtered


def apply_gateway_compat_patch() -> None:
    """Patch litellm responses call path for proxy compatibility."""
    import litellm as _litellm
    import openhands.sdk.llm.llm as _sdk_llm_module

    if getattr(_litellm, "_toyshop_responses_patched", False):
        return

    _original_responses = _sdk_llm_module.litellm_responses

    def _filtered_responses(*args, **kwargs):
        filtered_kwargs = strip_unsupported_responses_params(kwargs)
        return _original_responses(*args, **filtered_kwargs)

    _sdk_llm_module.litellm_responses = _filtered_responses
    _litellm.responses = _filtered_responses
    _litellm._toyshop_responses_patched = True
