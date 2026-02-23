"""Shared fixtures and hooks for toyshop tests."""

import pytest
from _pytest.runner import pytest_runtest_makereport as _orig_makereport


# LLM service error patterns — convert these to skip instead of fail
_LLM_ERROR_PATTERNS = [
    "ServiceUnavailableError",
    "No available accounts",
    "APIConnectionError",
    "AuthenticationError",
    "RateLimitError",
    "BadGatewayError",
    "Upstream request failed",
    "Connection refused",
    "Timeout Error",
]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert LLM service errors in E2E tests to skip instead of fail."""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        is_e2e = (
            "e2e" in str(item.fspath)
            or any(m.name in ("e2e", "slow") for m in item.iter_markers())
        )
        if is_e2e:
            error_str = str(report.longreprtext) if hasattr(report, "longreprtext") else str(call.excinfo.value) if call.excinfo else ""
            for pattern in _LLM_ERROR_PATTERNS:
                if pattern in error_str:
                    report.outcome = "skipped"
                    report.wasxfail = f"LLM service unavailable: {pattern}"
                    break
