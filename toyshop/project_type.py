"""Project type configuration and registry.

Defines project types (Python, Java, Minecraft Mod, etc.) with their
build/test/lint toolchains and default artifact layouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Project artifacts — path configuration
# ---------------------------------------------------------------------------


@dataclass
class ProjectArtifacts:
    """Project artifact path configuration (relative to workspace)."""

    src: list[str]  # Source directories, e.g. ["calculator/"]
    test: list[str]  # Test directories, e.g. ["tests/"]
    script: list[str] = field(default_factory=list)  # One-off scripts, cleaned before tests
    doc: str = "doc/"  # Architecture/spec docs directory

    def validate(self, workspace: Path) -> list[str]:
        """Validate paths: no nesting, common parent.

        Returns list of error messages (empty = valid).
        """
        errors: list[str] = []
        all_paths = [*self.src, *self.test, *self.script]
        if self.doc:
            all_paths.append(self.doc)

        # Normalize
        normalized = [p.rstrip("/") for p in all_paths if p]

        # Check nesting
        for i, a in enumerate(normalized):
            for j, b in enumerate(normalized):
                if i != j and (b.startswith(a + "/") or a.startswith(b + "/")):
                    errors.append(f"Paths nest: '{a}' and '{b}'")

        # Check src and test are non-empty
        if not self.src:
            errors.append("src must have at least one path")
        if not self.test:
            errors.append("test must have at least one path")

        return errors


# ---------------------------------------------------------------------------
# Project type — full configuration
# ---------------------------------------------------------------------------


@dataclass
class ProjectType:
    """Complete project type configuration."""

    id: str  # "python" | "java" | "java-minecraft" | "json-minecraft"
    language: str  # "python" | "java" | "json"
    display_name: str

    # Default artifact layout
    default_artifacts: ProjectArtifacts

    # Build
    build_command: str | None = None  # None = no build step
    static_check_command: str | None = None  # Compilation / lint

    # Test
    test_framework: str = "pytest"  # "pytest" | "junit" | "rcon" | "json-schema"
    test_command: str = "python3 -m pytest tests/ -v"
    test_file_pattern: str = "test_*.py"

    # File extensions
    source_extensions: list[str] = field(default_factory=lambda: [".py"])

    # Agent prompt hints
    language_hint: str = "Python"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ProjectType] = {}


def register_project_type(pt: ProjectType) -> None:
    """Register a project type."""
    _REGISTRY[pt.id] = pt


def get_project_type(type_id: str) -> ProjectType:
    """Get a project type by ID. Raises KeyError if not found."""
    if type_id not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(f"Unknown project type '{type_id}'. Available: {available}")
    return _REGISTRY[type_id]


def list_project_types() -> list[ProjectType]:
    """List all registered project types."""
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Built-in types
# ---------------------------------------------------------------------------

register_project_type(ProjectType(
    id="python",
    language="python",
    display_name="Python Application",
    default_artifacts=ProjectArtifacts(
        src=["src/"],
        test=["tests/"],
    ),
    build_command=None,
    static_check_command="python3 -m py_compile",
    test_framework="pytest",
    test_command="python3 -m pytest tests/ -v --tb=long",
    test_file_pattern="test_*.py",
    source_extensions=[".py"],
    language_hint="Python",
))

register_project_type(ProjectType(
    id="java",
    language="java",
    display_name="Java Application",
    default_artifacts=ProjectArtifacts(
        src=["src/main/java/"],
        test=["src/test/java/"],
    ),
    build_command="./gradlew build",
    static_check_command="./gradlew build",
    test_framework="junit",
    test_command="./gradlew test",
    test_file_pattern="*Test.java",
    source_extensions=[".java"],
    language_hint="Java",
))

register_project_type(ProjectType(
    id="java-minecraft",
    language="java",
    display_name="Java Minecraft Mod",
    default_artifacts=ProjectArtifacts(
        src=["src/main/java/", "src/client/java/"],
        test=["src/test/java/"],
    ),
    build_command="./gradlew build",
    static_check_command="./gradlew build",
    test_framework="rcon",
    test_command="./gradlew build",  # Build is the primary test for mods
    test_file_pattern="*Test.java",
    source_extensions=[".java", ".json"],
    language_hint="Java (Fabric Minecraft Mod)",
))

register_project_type(ProjectType(
    id="json-minecraft",
    language="json",
    display_name="JSON Minecraft Config",
    default_artifacts=ProjectArtifacts(
        src=["src/main/resources/"],
        test=["tests/"],
    ),
    build_command=None,
    static_check_command=None,
    test_framework="json-schema",
    test_command="python3 -m pytest tests/ -v",
    test_file_pattern="test_*.py",
    source_extensions=[".json", ".mcmeta"],
    language_hint="JSON (Minecraft resource/data pack)",
))
