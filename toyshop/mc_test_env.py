"""Minecraft mod test environment — build, deploy, start server, RCON verify, stop.

Wraps modfactory's ServerManager and RCONVerifier to provide a complete
test lifecycle for java-minecraft projects in the TDD pipeline.

Usage:
    with McTestEnvironment(workspace, mod_id="frostbow") as env:
        build_ok = env.build()
        if build_ok:
            server_ok = env.start_server()
            if server_ok:
                result = env.run_rcon_tests({"mod_id": "frostbow", "items": ["frost_bow"]})
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from toyshop.test_runner import TestRunResult, PerTestResult

# JDK location
JAVA_HOME = Path("/home/dministrator/.local/jdk")


@dataclass
class McBuildResult:
    success: bool
    output: str
    jar_path: Path | None = None


@dataclass
class McServerStatus:
    running: bool = False
    ready: bool = False
    errors: list[str] = field(default_factory=list)


class McTestEnvironment:
    """MC mod test environment: build → deploy → start server → RCON → stop."""

    def __init__(
        self,
        workspace: Path,
        mod_id: str = "mymod",
        rcon_port: int = 25575,
        rcon_password: str = "modtest",
        server_port: int = 25565,
        startup_timeout: int = 120,
    ):
        self.workspace = Path(workspace)
        self.mod_id = mod_id
        self.rcon_port = rcon_port
        self.rcon_password = rcon_password
        self.server_port = server_port
        self.startup_timeout = startup_timeout
        self._server_manager = None
        self._server_started = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop_server()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, timeout: int = 300) -> McBuildResult:
        """Run ./gradlew build with JAVA_HOME set."""
        env = os.environ.copy()
        env["JAVA_HOME"] = str(JAVA_HOME)
        env["PATH"] = f"{JAVA_HOME}/bin:{env.get('PATH', '')}"

        try:
            result = subprocess.run(
                ["./gradlew", "build", "--no-daemon"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            output = result.stdout + "\n" + result.stderr
            success = result.returncode == 0

            jar_path = None
            if success:
                jar_path = self._find_mod_jar()

            return McBuildResult(success=success, output=output, jar_path=jar_path)

        except subprocess.TimeoutExpired:
            return McBuildResult(success=False, output=f"Build timed out after {timeout}s")
        except Exception as e:
            return McBuildResult(success=False, output=f"Build error: {e}")

    def _find_mod_jar(self) -> Path | None:
        """Find the built mod jar in build/libs/."""
        libs_dir = self.workspace / "build" / "libs"
        if not libs_dir.exists():
            return None
        jars = [j for j in libs_dir.glob("*.jar") if "-sources" not in j.name]
        return jars[0] if jars else None

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start_server(self) -> McServerStatus:
        """Setup, deploy jar, start MC server, wait until ready."""
        try:
            from modfactory.server_manager import ServerConfig, ServerManager
        except ImportError:
            return McServerStatus(
                running=False, ready=False,
                errors=["modfactory SDK not installed — cannot start MC server"],
            )

        config = ServerConfig(
            mod_project=self.workspace,
            rcon_port=self.rcon_port,
            rcon_password=self.rcon_password,
            server_port=self.server_port,
            java_home=JAVA_HOME,
            startup_timeout=self.startup_timeout,
        )

        self._server_manager = ServerManager(config)
        self._server_manager.setup()

        # Deploy the built jar
        jar = self._server_manager.deploy_jar()
        if not jar:
            return McServerStatus(
                running=False, ready=False,
                errors=["No mod jar found in build/libs/ — build first"],
            )

        # Start server
        self._server_manager.start()
        self._server_started = True

        # Wait for ready
        status = self._server_manager.wait_until_ready()
        if not status.ready:
            self.stop_server()
            return McServerStatus(
                running=False, ready=False,
                errors=status.errors or ["Server failed to start"],
            )

        # Give server a moment to finish loading mods
        time.sleep(2)

        return McServerStatus(running=True, ready=True)

    def stop_server(self):
        """Gracefully stop the MC server."""
        if self._server_manager and self._server_started:
            try:
                self._server_manager.stop()
            except Exception:
                pass
            self._server_started = False

    # ------------------------------------------------------------------
    # RCON tests
    # ------------------------------------------------------------------

    def run_rcon_tests(self, spec: dict | None = None) -> TestRunResult:
        """Run RCON verification against the running server.

        Args:
            spec: {"mod_id": "...", "items": [...], "blocks": [...]}
                  If None, loads from workspace/rcon_tests.json.
        """
        if spec is None:
            spec = self._load_rcon_spec()
        if not spec:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="No RCON test spec (rcon_tests.json) found",
            )

        try:
            from modfactory.verify_rcon import RCONVerifier
        except ImportError:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output="modfactory SDK not installed — cannot run RCON tests",
            )

        try:
            verifier = RCONVerifier(
                host="localhost",
                port=self.rcon_port,
                password=self.rcon_password,
            )
            report = verifier.verify_mod(
                mod_id=spec.get("mod_id", self.mod_id),
                blocks=spec.get("blocks", []),
                items=spec.get("items", []),
            )
        except Exception as e:
            return TestRunResult(
                all_passed=False, total=0, passed=0, failed=0, errors=1,
                output=f"RCON verification failed: {e}",
            )

        passed = sum(1 for r in report.results if r.passed)
        failed = sum(1 for r in report.results if not r.passed)
        per_test = [
            PerTestResult(
                test_id=r.name,
                status="passed" if r.passed else "failed",
                failure_message=r.reason if not r.passed else "",
            )
            for r in report.results
        ]

        return TestRunResult(
            all_passed=report.all_passed,
            total=len(report.results),
            passed=passed,
            failed=failed,
            errors=0,
            output=report.summary(),
            per_test=per_test,
        )

    def _load_rcon_spec(self) -> dict | None:
        """Load rcon_tests.json from workspace."""
        path = self.workspace / "rcon_tests.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return None

    # ------------------------------------------------------------------
    # Full test cycle
    # ------------------------------------------------------------------

    def run_full_test(self) -> TestRunResult:
        """Run complete test cycle: build → start → RCON → stop.

        Returns combined TestRunResult.
        """
        # Step 1: Build
        build_result = self.build()
        if not build_result.success:
            # Extract useful error info
            output = build_result.output
            # Truncate to last 2000 chars for readability
            if len(output) > 2000:
                output = "...\n" + output[-2000:]
            return TestRunResult(
                all_passed=False, total=1, passed=0, failed=1, errors=0,
                output=f"BUILD FAILED:\n{output}",
                per_test=[PerTestResult(
                    test_id="build:gradle",
                    status="failed",
                    failure_message=output[-500:],
                )],
            )

        # Step 2: Start server
        server_status = self.start_server()
        if not server_status.ready:
            return TestRunResult(
                all_passed=False, total=1, passed=0, failed=0, errors=1,
                output=f"Server failed to start: {'; '.join(server_status.errors)}",
                per_test=[PerTestResult(
                    test_id="server:startup",
                    status="error",
                    failure_message="; ".join(server_status.errors),
                )],
            )

        # Step 3: RCON tests
        try:
            rcon_result = self.run_rcon_tests()

            # Prepend build success as a test
            build_test = PerTestResult(test_id="build:gradle", status="passed")
            server_test = PerTestResult(test_id="server:startup", status="passed")
            all_tests = [build_test, server_test] + rcon_result.per_test

            return TestRunResult(
                all_passed=rcon_result.all_passed,
                total=len(all_tests),
                passed=rcon_result.passed + 2,  # build + server
                failed=rcon_result.failed,
                errors=rcon_result.errors,
                output=f"BUILD SUCCESSFUL\nServer started\n{rcon_result.output}",
                per_test=all_tests,
            )
        finally:
            self.stop_server()
