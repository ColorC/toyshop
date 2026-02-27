"""Adapters for ToyShop framework.

Thin wrappers that delegate to existing code to thin wrappers via ports.
"""

from toyshop.adapters.llm import SDKLLMAdapter
from toyshop.adapters.coding import OpenHandsCodingAdapter
from toyshop.adapters.spec import OpenSpecAdapter
from toyshop.adapters.version import ASTCodeVersionAdapter
from toyshop.adapters.storage import SQLiteStorageAdapter
from toyshop.adapters.wiki import SQLiteWikiAdapter
from toyshop.adapters.research import GPTResearcherAdapter

from toyshop.adapters.scope import ScopeControlAdapter
