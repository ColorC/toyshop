"""Research Agent Port — abstracts external research capabilities."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ResearchAgentPort(Protocol):
    """Port for external research capabilities."""

    def generate_plan(
        self,
        user_input: str,
        trigger_type: str = "kickoff_mvp_sota",
    ) -> Any:
        """Generate a structured research plan.

        Args:
            user_input: The problem statement or requirements
            trigger_type: "kickoff_mvp_sota" or "deadlock_resolution"

        Returns:
            ResearchPlan-like object.
        """
        ...

    def run_research(
        self,
        plan: Any,
    ) -> Any:
        """Execute research and return results.

        Returns:
            ResearchResult-like object with mvp_option, sota_option, etc.
        """
        ...
