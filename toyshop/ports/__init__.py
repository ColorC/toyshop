"""Port definitions for ToyShop framework.

All ports are typing.Protocol classes — no implementation, no dependencies.
Existing code satisfies ports via structural subtyping (duck typing).
"""

from toyshop.ports.llm import LLMPort
from toyshop.ports.coding import CodingAgentPort
from toyshop.ports.spec import SpecPort
from toyshop.ports.version import CodeVersionPort
from toyshop.ports.storage import StoragePort
from toyshop.ports.wiki import WikiPort
from toyshop.ports.research import ResearchAgentPort
from toyshop.ports.scope import ScopeControlPort
from toyshop.ports.reporting import ReportingPort

__all__ = [
    "LLMPort",
    "CodingAgentPort",
    "SpecPort",
    "CodeVersionPort",
    "StoragePort",
    "WikiPort",
    "ResearchAgentPort",
    "ScopeControlPort",
    "ReportingPort",
]
