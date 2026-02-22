"""Tests for the Versioned Software Wiki storage layer."""

import json
import tempfile
import shutil
from pathlib import Path

import pytest

from toyshop.storage.database import init_database, close_database, create_project, save_architecture_from_design
from toyshop.storage.wiki import (
    WikiVersion,
    WikiTestSuite,
    create_version,
    bind_git_commit,
    save_test_suite,
    get_version,
    get_version_by_commit,
    get_version_by_number,
    get_latest_version,
    list_versions,
    get_test_suite,
    diff_versions,
    log_event,
    get_changelog,
    extract_test_metadata,
)


@pytest.fixture(autouse=True)
def wiki_db(tmp_path):
    """Initialize an in-memory-like SQLite DB for each test."""
    db_path = tmp_path / "test.db"
    init_database(db_path)
    yield db_path
    close_database()


@pytest.fixture
def project_id():
    """Create a test project and return its ID."""
    proj = create_project("test-project", "/tmp/test")
    return proj["id"]

@pytest.fixture
def snapshot_id(project_id):
    """Create a test snapshot and return its ID."""
    snap = save_architecture_from_design(
        project_id,
        modules=[{"name": "core", "filePath": "app/core.py", "responsibilities": ["compute"]}],
        interfaces=[{"name": "Calculator", "type": "class", "signature": "class Calculator:", "module": "core"}],
    )
    return snap["id"]


class TestWikiVersion:
    """Tests for wiki version CRUD."""

    def test_create_first_version(self, project_id, snapshot_id):
        v = create_version(
            project_id=project_id,
            snapshot_id=snapshot_id,
            change_type="create",
            change_summary="Initial version",
        )
        assert isinstance(v, WikiVersion)
        assert v.version_number == 1
        assert v.parent_version_id is None
        assert v.change_type == "create"
        assert v.git_commit_hash is None

    def test_version_number_increments(self, project_id, snapshot_id):
        v1 = create_version(project_id, snapshot_id, "create", "v1")
        v2 = create_version(project_id, snapshot_id, "modify", "v2")
        assert v1.version_number == 1
        assert v2.version_number == 2
        assert v2.parent_version_id == v1.id

    def test_get_version(self, project_id, snapshot_id):
        v = create_version(project_id, snapshot_id, "create", "test")
        fetched = get_version(v.id)
        assert fetched is not None
        assert fetched.id == v.id
        assert fetched.change_summary == "test"

    def test_get_version_not_found(self):
        assert get_version("nonexistent") is None

    def test_get_latest_version(self, project_id, snapshot_id):
        create_version(project_id, snapshot_id, "create", "v1")
        v2 = create_version(project_id, snapshot_id, "modify", "v2")
        latest = get_latest_version(project_id)
        assert latest is not None
        assert latest.id == v2.id
        assert latest.version_number == 2

    def test_get_latest_version_empty(self, project_id):
        assert get_latest_version(project_id) is None

    def test_get_version_by_number(self, project_id, snapshot_id):
        create_version(project_id, snapshot_id, "create", "v1")
        v2 = create_version(project_id, snapshot_id, "modify", "v2")
        fetched = get_version_by_number(project_id, 2)
        assert fetched is not None
        assert fetched.id == v2.id

    def test_list_versions(self, project_id, snapshot_id):
        for i in range(5):
            create_version(project_id, snapshot_id, "create", f"v{i+1}")
        versions = list_versions(project_id, limit=3)
        assert len(versions) == 3
        # Newest first
        assert versions[0].version_number == 5
        assert versions[2].version_number == 3

    def test_openspec_frozen(self, project_id, snapshot_id, tmp_path):
        openspec_dir = tmp_path / "openspec"
        openspec_dir.mkdir()
        (openspec_dir / "proposal.md").write_text("# Proposal")
        (openspec_dir / "design.md").write_text("# Design")

        v = create_version(
            project_id, snapshot_id, "create", "with docs",
            openspec_dir=openspec_dir,
        )
        fetched = get_version(v.id)
        assert fetched.proposal_md == "# Proposal"
        assert fetched.design_md == "# Design"
        assert fetched.tasks_md is None  # not created

class TestGitBinding:
    """Tests for git commit binding."""

    def test_bind_git_commit(self, project_id, snapshot_id):
        v = create_version(project_id, snapshot_id, "create", "test")
        assert v.git_commit_hash is None

        bind_git_commit(v.id, "abc123def456")
        fetched = get_version(v.id)
        assert fetched.git_commit_hash == "abc123def456"

    def test_get_version_by_commit(self, project_id, snapshot_id):
        v = create_version(project_id, snapshot_id, "create", "test")
        bind_git_commit(v.id, "deadbeef1234")

        fetched = get_version_by_commit("deadbeef1234")
        assert fetched is not None
        assert fetched.id == v.id

    def test_get_version_by_commit_not_found(self):
        assert get_version_by_commit("nonexistent") is None

    def test_bind_logs_event(self, project_id, snapshot_id):
        v = create_version(project_id, snapshot_id, "create", "test")
        bind_git_commit(v.id, "abc123")

        log = get_changelog(project_id, limit=5)
        git_events = [e for e in log if e["event_type"] == "git_bound"]
        assert len(git_events) == 1
        assert "abc123" in git_events[0]["event_detail"]


class TestTestSuite:
    """Tests for test suite tracking."""

    def test_save_and_get_test_suite(self, project_id, snapshot_id):
        v = create_version(project_id, snapshot_id, "create", "test")
        ts = save_test_suite(
            version_id=v.id,
            test_files=["tests/test_core.py", "tests/test_api.py"],
            test_cases=[
                {"id": "tests/test_core.py::test_add", "name": "test_add",
                 "file": "tests/test_core.py", "class_name": ""},
            ],
            total_tests=1,
            passed=1,
            failed=0,
        )
        assert isinstance(ts, WikiTestSuite)
        assert ts.total_tests == 1

        fetched = get_test_suite(v.id)
        assert fetched is not None
        assert fetched.test_files == ["tests/test_core.py", "tests/test_api.py"]
        assert len(fetched.test_cases) == 1

    def test_get_test_suite_not_found(self, project_id, snapshot_id):
        v = create_version(project_id, snapshot_id, "create", "test")
        assert get_test_suite(v.id) is None


class TestExtractTestMetadata:
    """Tests for AST-based test metadata extraction."""

    def test_extract_functions_and_classes(self, tmp_path):
        workspace = tmp_path / "workspace"
        test_dir = workspace / "tests"
        test_dir.mkdir(parents=True)

        (test_dir / "test_core.py").write_text(
            "import pytest\n\n"
            "def test_standalone():\n    pass\n\n"
            "class TestCalculator:\n"
            "    def test_add(self):\n        pass\n"
            "    def test_subtract(self):\n        pass\n"
            "    def helper(self):\n        pass\n"  # not a test
        )

        files, cases = extract_test_metadata(workspace)
        assert files == ["tests/test_core.py"]
        assert len(cases) == 3  # standalone + add + subtract
        names = {c["name"] for c in cases}
        assert names == {"test_standalone", "test_add", "test_subtract"}

        # Check class_name
        standalone = [c for c in cases if c["name"] == "test_standalone"][0]
        assert standalone["class_name"] == ""
        add = [c for c in cases if c["name"] == "test_add"][0]
        assert add["class_name"] == "TestCalculator"

    def test_empty_workspace(self, tmp_path):
        files, cases = extract_test_metadata(tmp_path)
        assert files == []
        assert cases == []

    def test_syntax_error_skipped(self, tmp_path):
        workspace = tmp_path / "workspace"
        test_dir = workspace / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_broken.py").write_text("def test_x(:\n")  # syntax error
        (test_dir / "test_good.py").write_text("def test_ok():\n    pass\n")

        files, cases = extract_test_metadata(workspace)
        assert len(files) == 2  # both listed
        assert len(cases) == 1  # only good one parsed
        assert cases[0]["name"] == "test_ok"

class TestVersionDiff:
    """Tests for version comparison."""

    def test_diff_modules_and_interfaces(self, project_id):
        # Version 1: core module with Calculator
        snap1 = save_architecture_from_design(
            project_id,
            modules=[{"name": "core", "filePath": "app/core.py", "responsibilities": "compute"}],
            interfaces=[{"name": "Calculator", "type": "class", "signature": "class Calculator:"}],
        )
        v1 = create_version(project_id, snap1["id"], "create", "initial")
        save_test_suite(v1.id, ["tests/test_core.py"],
                        [{"id": "t1", "name": "test_add", "file": "t", "class_name": ""}],
                        1, 1, 0)

        # Version 2: add memory module, modify Calculator signature
        snap2 = save_architecture_from_design(
            project_id,
            modules=[
                {"name": "core", "filePath": "app/core.py", "responsibilities": "compute+chain"},
                {"name": "memory", "filePath": "app/memory.py", "responsibilities": "store"},
            ],
            interfaces=[
                {"name": "Calculator", "type": "class", "signature": "class Calculator(Base):"},
                {"name": "MemoryManager", "type": "class", "signature": "class MemoryManager:"},
            ],
        )
        v2 = create_version(project_id, snap2["id"], "modify", "add memory")
        save_test_suite(v2.id, ["tests/test_core.py", "tests/test_memory.py"],
                        [{"id": "t1", "name": "test_add", "file": "t", "class_name": ""},
                         {"id": "t2", "name": "test_store", "file": "t", "class_name": ""}],
                        2, 2, 0)

        diff = diff_versions(project_id, 1, 2)
        assert "memory" in diff.modules_added
        assert "core" in diff.modules_modified  # responsibilities changed
        assert "MemoryManager" in diff.interfaces_added
        assert "Calculator" in diff.interfaces_modified  # signature changed
        assert diff.tests_added == ["t2"]
        assert diff.tests_removed == []

    def test_diff_nonexistent_version(self, project_id, snapshot_id):
        create_version(project_id, snapshot_id, "create", "v1")
        with pytest.raises(ValueError):
            diff_versions(project_id, 1, 99)


class TestChangelog:
    """Tests for the audit changelog."""

    def test_log_and_get(self, project_id):
        log_event(project_id, "version_created", "v1 created")
        log_event(project_id, "test_updated", "re-ran tests")

        entries = get_changelog(project_id, limit=10)
        assert len(entries) == 2
        # Newest first
        assert entries[0]["event_type"] == "test_updated"
        assert entries[1]["event_type"] == "version_created"

    def test_log_with_data(self, project_id):
        log_event(project_id, "custom", "detail",
                  event_data={"key": "value"})
        entries = get_changelog(project_id)
        assert json.loads(entries[0]["event_data_json"]) == {"key": "value"}
