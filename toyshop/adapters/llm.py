"""LLM Adapter — wraps toyshop.llm into LLMPort."""

from __future__ import annotations

from typing import Any

from toyshop.llm import LLM
from toyshop.ports.llm import LLMPort


class SDKLLMAdapter:
    """Wraps the existing LLM class into LLMPort interface."""

    def __init__(self, llm: LLM):
        self._llm = llm

    @property
    def model(self) -> str:
        return self._llm.model

    def chat_with_tool(
        self,
        system_prompt: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        tool_parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._llm.chat_with_tool(
            system_prompt=system_prompt,
            user_content=user_content,
            tool_name=tool_name,
            tool_description=tool_description,
            tool_parameters=tool_parameters,
        )

    def probe(self, timeout: int = 15) -> tuple[bool, str]:
        return self._llm.probe(timeout=timeout)

    def complete(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        return self._llm.complete(messages, **kwargs)
