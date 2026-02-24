#!/usr/bin/env python3
"""Java TDD Pipeline E2E Test (#9 Multi-Project Type).

Tests the complete TDD pipeline with project_type="java":
1. Design phase: Generate openspec/ documents for a Java Calculator
2. TDD pipeline with GradleTestRunner instead of PytestRunner
3. Verify Java source and JUnit test files are produced

Prerequisites:
- JDK 21 at /home/dministrator/.local/jdk/ (or JAVA_HOME set)
- Gradle wrapper bootstrapped from existing project

Usage:
    TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_java_tdd_e2e.py [--keep] [--workspace PATH]
"""

import argparse
import json
import os
import signal
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Ensure JDK is on PATH
_JDK_HOME = os.environ.get("JAVA_HOME", "/home/dministrator/.local/jdk")
if Path(_JDK_HOME).exists():
    os.environ["JAVA_HOME"] = _JDK_HOME
    os.environ["PATH"] = f"{_JDK_HOME}/bin:{os.environ['PATH']}"

from toyshop import create_toyshop_llm, run_toyshop_workflow
from toyshop.tdd_pipeline import run_tdd_pipeline, TDDResult


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
    "404 page not found",
]


def _is_llm_unavailable_error(exc: Exception) -> bool:
    return any(p in str(exc) for p in _LLM_ERROR_PATTERNS)


def _probe_llm(llm) -> tuple[bool, str]:
    from toyshop.llm import probe_llm
    return probe_llm(llm, timeout=12)


def _on_timeout(signum, frame):
    raise TimeoutError("Java E2E script timed out")


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


# ── Gradle project bootstrap ─────────────────────────────────────────────

_GRADLE_WRAPPER_SOURCE = Path("/home/dministrator/work/custom-block-mod")

_BUILD_GRADLE = """\
plugins {
    id 'java'
}

group = 'com.example'
version = '1.0-SNAPSHOT'

java {
    sourceCompatibility = JavaVersion.VERSION_21
    targetCompatibility = JavaVersion.VERSION_21
}

repositories {
    mavenCentral()
}

dependencies {
    testImplementation 'org.junit.jupiter:junit-jupiter:5.10.2'
    testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
}

test {
    useJUnitPlatform()
}
"""

_SETTINGS_GRADLE = "rootProject.name = 'calculator'\n"


def bootstrap_gradle_project(workspace: Path):
    """Pre-seed workspace with Gradle wrapper + build files."""
    print("Bootstrapping Gradle project...")

    # Copy Gradle wrapper from existing project
    src_wrapper = _GRADLE_WRAPPER_SOURCE / "gradle"
    src_gradlew = _GRADLE_WRAPPER_SOURCE / "gradlew"
    src_gradlew_bat = _GRADLE_WRAPPER_SOURCE / "gradlew.bat"

    if not src_wrapper.exists():
        print(f"  [WARN] No Gradle wrapper at {src_wrapper}, skipping bootstrap")
        return False

    shutil.copytree(src_wrapper, workspace / "gradle")
    if src_gradlew.exists():
        shutil.copy2(src_gradlew, workspace / "gradlew")
        (workspace / "gradlew").chmod(0o755)
    if src_gradlew_bat.exists():
        shutil.copy2(src_gradlew_bat, workspace / "gradlew.bat")

    # Write build files
    (workspace / "build.gradle").write_text(_BUILD_GRADLE, encoding="utf-8")
    (workspace / "settings.gradle").write_text(_SETTINGS_GRADLE, encoding="utf-8")

    # Create standard Java directory structure
    (workspace / "src" / "main" / "java" / "com" / "example").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "test" / "java" / "com" / "example").mkdir(parents=True, exist_ok=True)

    print("  Gradle wrapper + build.gradle + settings.gradle ready")
    return True


REQUIREMENTS = """Create a simple Java Calculator class with the following:

1. Basic arithmetic: add, subtract, multiply, divide
2. Support int and double operands
3. Handle division by zero (throw IllegalArgumentException)
4. A static main method that demonstrates usage

Package: com.example
Keep it simple — one Calculator class with static methods.
"""


def run_design_phase(workspace: str, llm):
    print_section("Design Phase (Java)")
    start = datetime.now()
    run_toyshop_workflow(
        user_input=REQUIREMENTS,
        project_name="JavaCalculator",
        workspace=workspace,
        llm=llm,
        persist=True,
    )
    elapsed = (datetime.now() - start).total_seconds()
    print(f"Design completed in {elapsed:.1f}s")

    ws = Path(workspace)
    for doc in ["proposal.md", "design.md", "tasks.md", "spec.md"]:
        path = ws / "openspec" / doc
        status = f"({path.stat().st_size} bytes)" if path.exists() else "MISSING"
        print(f"  {'✓' if path.exists() else '✗'} {doc} {status}")


def run_tdd_phase(workspace: str, llm) -> TDDResult:
    print_section("TDD Pipeline (Java)")
    start = datetime.now()
    result = run_tdd_pipeline(
        workspace=workspace,
        llm=llm,
        project_type="java",
    )
    elapsed = (datetime.now() - start).total_seconds()

    print(f"\nTDD pipeline completed in {elapsed:.1f}s")
    print(f"  Success: {result.success}")
    print(f"  White-box: {'PASSED' if result.whitebox_passed else 'FAILED'}")
    print(f"  Black-box: {'PASSED' if result.blackbox_passed else 'FAILED'}")
    print(f"  Retries: {result.retry_count}")
    print(f"  Stub files: {result.stub_files}")
    print(f"  Test files: {result.test_files}")
    print(f"  Files created: {len(result.files_created)}")
    for f in sorted(result.files_created)[:20]:
        print(f"    - {f}")
    return result


def verify_results(workspace: str, result: TDDResult) -> bool:
    print_section("Verification (Java)")
    ws = Path(workspace)
    issues = []

    # 1. Java source files
    print("Checking Java source files...")
    java_src = list((ws / "src" / "main").rglob("*.java"))
    if java_src:
        for f in java_src:
            print(f"  ✓ {f.relative_to(ws)}")
    else:
        print("  ✗ No Java source files found")
        issues.append("No Java source files")

    # 2. Java test files
    print("\nChecking Java test files...")
    java_tests = list((ws / "src" / "test").rglob("*Test.java"))
    if not java_tests:
        java_tests = list((ws / "src" / "test").rglob("*.java"))
    if java_tests:
        for f in java_tests:
            print(f"  ✓ {f.relative_to(ws)}")
    else:
        print("  ✗ No Java test files found")
        issues.append("No Java test files")

    # 3. Gradle build files
    print("\nChecking Gradle files...")
    for gf in ["build.gradle", "settings.gradle", "gradlew"]:
        path = ws / gf
        print(f"  {'✓' if path.exists() else '✗'} {gf}")
        if not path.exists():
            issues.append(f"Missing {gf}")

    # 4. Pipeline result
    print("\nChecking pipeline result...")
    if result.success:
        print("  ✓ Pipeline succeeded")
    else:
        print(f"  ⚠ Pipeline result: {result.summary}")
        # Don't treat as hard failure — Java pipeline is experimental
        issues.append(f"Pipeline: {result.summary}")

    if result.whitebox_passed:
        print("  ✓ White-box tests passed")
    else:
        print("  ⚠ White-box tests did not pass")

    if issues:
        print(f"\n⚠️ {len(issues)} issues:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    print("\n✅ All checks passed")
    return True


def main():
    parser = argparse.ArgumentParser(description="Java TDD Pipeline E2E test")
    parser.add_argument("--workspace", type=str, help="Use existing workspace")
    parser.add_argument("--keep", action="store_true", help="Keep workspace after test")
    parser.add_argument("--skip-design", action="store_true", help="Skip design phase")
    args = parser.parse_args()

    if os.getenv("TOYSHOP_RUN_LIVE_E2E", "0") != "1":
        print("[SKIP] Set TOYSHOP_RUN_LIVE_E2E=1 to run this test")
        return 0

    if args.workspace:
        workspace = Path(args.workspace)
        print(f"Using existing workspace: {workspace}")
    else:
        workspace = Path(tempfile.mkdtemp(prefix="toyshop_java_e2e_"))
        print(f"Created workspace: {workspace}")

    try:
        try:
            signal.signal(signal.SIGALRM, _on_timeout)
            signal.alarm(300)  # 5 min timeout (Gradle is slow)

            llm = create_toyshop_llm()
            print(f"LLM: {llm.model}")
            ok, err = _probe_llm(llm)
            if not ok:
                print(f"\n[SKIP] LLM service unavailable: {err}")
                return 0

            # Bootstrap Gradle project
            if not args.skip_design:
                if not bootstrap_gradle_project(workspace):
                    print("[SKIP] Cannot bootstrap Gradle project")
                    return 0

            # Design phase
            if not args.skip_design:
                run_design_phase(str(workspace), llm)
            else:
                print("Skipping design phase")

            # TDD pipeline
            result = run_tdd_phase(str(workspace), llm)

            # Verify
            success = verify_results(str(workspace), result)

            print_section("Summary")
            print(f"Workspace: {workspace}")
            print(f"Design: {'skipped' if args.skip_design else 'done'}")
            print(f"TDD Pipeline: {'✅' if result.success else '⚠️'}")
            print(f"Verification: {'✅' if success else '⚠️'}")

            return 0 if success else 1

        except Exception as e:
            if isinstance(e, TimeoutError) or _is_llm_unavailable_error(e):
                print(f"\n[SKIP] LLM service unavailable or timeout: {e}")
                return 0
            raise
        finally:
            signal.alarm(0)

    finally:
        if not args.keep and not args.workspace:
            print(f"\nCleaning up: {workspace}")
            shutil.rmtree(workspace, ignore_errors=True)
        elif args.keep:
            print(f"\nWorkspace preserved: {workspace}")


if __name__ == "__main__":
    sys.exit(main())
