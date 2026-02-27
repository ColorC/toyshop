"""LLM Port — abstracts LLM interactions."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMPort(Protocol):
    """Port for LLM interactions.

    Abstracts chat completion, tool calling, and availability checks.
    """

    @property
    def model(self) -> str:
        """The model identifier string."""
        ...

    def chat_with_tool(
        self,
        system_prompt: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        tool_parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Single-turn tool-calling.

        Returns parsed tool args or None if no tool call.
        """
        ...

    def probe(self, timeout: int = 15) -> tuple[bool, str]:
        """Fast availability check.

        Returns (ok, error_message).
        """
        ...

    def complete(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        """Simple completion without tool calling.

        Returns the response text.
        """
        ...
