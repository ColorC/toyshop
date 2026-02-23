"""Language support abstractions for ToyShop TDD pipeline."""

from toyshop.lang.base import LanguageSupport, get_language_support, register_language_support

# Auto-register language implementations (side-effect imports)
import toyshop.lang.python_lang  # noqa: F401
import toyshop.lang.java_lang  # noqa: F401

__all__ = [
    "LanguageSupport",
    "get_language_support",
    "register_language_support",
]
