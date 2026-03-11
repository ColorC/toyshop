from __future__ import annotations

from toyshop.llm_gateway import (
    ensure_function_call_outputs,
    strip_unsupported_responses_params,
)


def test_ensure_function_call_outputs_injects_missing_output():
    items = [{"type": "function_call", "call_id": "c1", "name": "tool", "arguments": "{}"}]
    out = ensure_function_call_outputs(items)

    assert isinstance(out, list)
    assert len(out) == 2
    assert out[1]["type"] == "function_call_output"
    assert out[1]["call_id"] == "c1"


def test_strip_unsupported_responses_params_drops_and_fixes_input():
    kwargs = {
        "model": "x",
        "temperature": 0.3,
        "top_p": 0.9,
        "presence_penalty": 0.1,
        "frequency_penalty": 0.1,
        "input": [{"type": "function_call", "call_id": "c2", "name": "tool", "arguments": "{}"}],
    }

    filtered = strip_unsupported_responses_params(kwargs)

    assert "temperature" not in filtered
    assert "top_p" not in filtered
    assert "presence_penalty" not in filtered
    assert "frequency_penalty" not in filtered
    assert len(filtered["input"]) == 2
    assert filtered["input"][1]["type"] == "function_call_output"
