"""Shared fixtures and hooks for toyshop tests."""

import pytest


# LLM service error patterns — convert these to skip instead of fail
_LLM_ERROR_PATTERNS = [
    "ServiceUnavailableError",
    "No available accounts",
    "APIConnectionError",
    "AuthenticationError",
    "RateLimitError",
    "Timeout",
    "BadGatewayError",
    "Connection refused",
    "503",
    "502",
]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Convert LLM service errors in E2E tests to skip instead of fail."""
    if call.when == "call" and call.excinfo is not None:
        # Only apply to e2e/slow marked tests or files with _e2e in name
        is_e2e = (
            "e2e" in str(item.fspath)
            or any(m.name in ("e2e", "slow") for m in item.iter_markers())
        )
        if is_e2e:
            error_str = str(call.excinfo.value)
            for pattern in _LLM_ERROR_PATTERNS:
                if pattern in error_str:
                    pytest.skip(f"LLM service unavailable: {error_str[:200]}")
