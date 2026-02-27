"""Research Adapter — wraps toyshop.research_agent into ResearchAgentPort."""

from __future__ import annotations

from typing import Any

from toyshop import research_agent
from toyshop.ports.research import ResearchAgentPort


class GPTResearcherAdapter:
    """Wraps existing research_agent into ResearchAgentPort interface."""

    def generate_plan(self, user_input: str, trigger_type: str = "kickoff_mvp_sota") -> Any:
        if trigger_type == "kickoff_mvp_sota":
            return research_agent.generate_kickoff_plan(user_input)
        return research_agent.default_research_plan()

    def run_research(self, plan: Any) -> Any:
        # The actual research execution is delegated to external service
        # This adapter provides a thin wrapper for future integration
        return research_agent.run_research_plan(plan)
