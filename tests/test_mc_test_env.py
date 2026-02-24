"""Tests for toyshop.mc_test_env — MC test environment lifecycle."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from toyshop.mc_test_env import McTestEnvironment, McBuildResult, McServerStatus


class TestMcBuildResult:
    def test_success(self):
        r = McBuildResult(success=True, output="BUILD SUCCESSFUL", jar_path=Path("/a.jar"))
        assert r.success
        assert r.jar_path is not None

    def test_failure(self):
        r = McBuildResult(success=False, output="BUILD FAILED")
        assert not r.success
        assert r.jar_path is None


class TestMcServerStatus:
    def test_defaults(self):
        s = McServerStatus()
        assert not s.running
        assert not s.ready
        assert s.errors == []


class TestMcTestEnvironment:
    def test_init_defaults(self, tmp_path):
        env = McTestEnvironment(tmp_path)
        assert env.mod_id == "mymod"
        assert env.rcon_port == 25575
        assert env.rcon_password == "modtest"
        assert env.server_port == 25565
        assert env.startup_timeout == 120

    def test_init_custom(self, tmp_path):
        env = McTestEnvironment(
            tmp_path, mod_id="frostbow",
            rcon_port=25576, rcon_password="secret",
            server_port=25566, startup_timeout=60,
        )
        assert env.mod_id == "frostbow"
        assert env.rcon_port == 25576
        assert env.rcon_password == "secret"

    def test_context_manager(self, tmp_path):
        with McTestEnvironment(tmp_path) as env:
            assert env is not None
        # Should not raise even without server started

    def test_build_no_gradlew(self, tmp_path):
        """Build should fail gracefully when no gradlew exists."""
        env = McTestEnvironment(tmp_path)
        result = env.build()
        assert not result.success
        assert result.jar_path is None

    def test_build_timeout(self, tmp_path):
        """Build with very short timeout should handle gracefully."""
        env = McTestEnvironment(tmp_path)
        # No gradlew, so it will fail with FileNotFoundError, not timeout
        result = env.build(timeout=1)
        assert not result.success

    def test_find_mod_jar_no_dir(self, tmp_path):
        env = McTestEnvironment(tmp_path)
        assert env._find_mod_jar() is None

    def test_find_mod_jar_with_jar(self, tmp_path):
        libs = tmp_path / "build" / "libs"
        libs.mkdir(parents=True)
        jar = libs / "mymod-1.0.0.jar"
        jar.write_bytes(b"fake-jar")
        # Also create a sources jar that should be skipped
        (libs / "mymod-1.0.0-sources.jar").write_bytes(b"fake-sources")

        env = McTestEnvironment(tmp_path)
        found = env._find_mod_jar()
        assert found is not None
        assert found.name == "mymod-1.0.0.jar"

    def test_find_mod_jar_empty_libs(self, tmp_path):
        libs = tmp_path / "build" / "libs"
        libs.mkdir(parents=True)
        env = McTestEnvironment(tmp_path)
        assert env._find_mod_jar() is None

    def test_start_server_no_modfactory(self, tmp_path, monkeypatch):
        """Without modfactory SDK, start_server should return error."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("modfactory"):
                raise ImportError("no modfactory")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        env = McTestEnvironment(tmp_path)
        status = env.start_server()
        assert not status.running
        assert not status.ready
        assert any("not installed" in e for e in status.errors)

    def test_stop_server_noop_when_not_started(self, tmp_path):
        """stop_server should be safe to call even if server never started."""
        env = McTestEnvironment(tmp_path)
        env.stop_server()  # Should not raise

    def test_load_rcon_spec(self, tmp_path):
        spec = {"mod_id": "frostbow", "items": ["frost_bow"], "blocks": []}
        (tmp_path / "rcon_tests.json").write_text(json.dumps(spec))
        env = McTestEnvironment(tmp_path)
        loaded = env._load_rcon_spec()
        assert loaded is not None
        assert loaded["mod_id"] == "frostbow"

    def test_load_rcon_spec_missing(self, tmp_path):
        env = McTestEnvironment(tmp_path)
        assert env._load_rcon_spec() is None

    def test_load_rcon_spec_invalid_json(self, tmp_path):
        (tmp_path / "rcon_tests.json").write_text("not json{{{")
        env = McTestEnvironment(tmp_path)
        assert env._load_rcon_spec() is None

    def test_run_rcon_tests_no_spec(self, tmp_path):
        """Without spec, run_rcon_tests should return error."""
        env = McTestEnvironment(tmp_path)
        result = env.run_rcon_tests()
        assert not result.all_passed
        assert "No RCON test spec" in result.output

    def test_run_rcon_tests_no_modfactory(self, tmp_path, monkeypatch):
        """Without modfactory SDK, run_rcon_tests should return error."""
        spec = {"mod_id": "frostbow", "items": ["frost_bow"], "blocks": []}
        (tmp_path / "rcon_tests.json").write_text(json.dumps(spec))

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("modfactory"):
                raise ImportError("no modfactory")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        env = McTestEnvironment(tmp_path)
        result = env.run_rcon_tests(spec)
        assert not result.all_passed
        assert "not installed" in result.output

    def test_run_full_test_build_fails(self, tmp_path):
        """Full test should fail at build step when no gradlew."""
        env = McTestEnvironment(tmp_path)
        result = env.run_full_test()
        assert not result.all_passed
        assert "BUILD FAILED" in result.output
        assert len(result.per_test) >= 1
        assert result.per_test[0].test_id == "build:gradle"
        assert result.per_test[0].status == "failed"
