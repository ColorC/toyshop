"""Prompt context for parameterizing agent prompts across project types."""

from __future__ import annotations

from dataclasses import dataclass

from toyshop.project_type import ProjectType


@dataclass
class PromptContext:
    """Language/framework-specific values injected into agent prompts.

    Replaces hardcoded "pytest", "python3", etc. in prompt templates.
    """

    language: str              # "Python" | "Java"
    test_command: str          # "python3 -m pytest tests/ -v" | "./gradlew test"
    test_framework: str        # "pytest" | "JUnit 5"
    run_single_test: str       # "python3 -m pytest {test_id} -v" | "./gradlew test --tests {test_id}"
    source_ext: str            # ".py" | ".java"
    import_style: str          # "from calculator.core import Calculator" | "import com.example.Calculator"
    build_command: str | None  # None | "./gradlew build"
    static_check: str | None   # "python3 -m py_compile" | "./gradlew build"

    @classmethod
    def from_project_type(cls, pt: ProjectType) -> PromptContext:
        """Build a PromptContext from a ProjectType configuration."""
        if pt.language == "python":
            return cls(
                language="Python",
                test_command=pt.test_command,
                test_framework="pytest",
                run_single_test="python3 -m pytest {test_id} -v --tb=long",
                source_ext=".py",
                import_style="from {module} import {name}",
                build_command=pt.build_command,
                static_check=pt.static_check_command,
            )
        elif pt.language == "java":
            return cls(
                language="Java",
                test_command=pt.test_command,
                test_framework="JUnit 5",
                run_single_test="./gradlew test --tests {test_id}",
                source_ext=".java",
                import_style="import {module}.{name};",
                build_command=pt.build_command,
                static_check=pt.static_check_command,
            )
        elif pt.language == "json":
            return cls(
                language="JSON",
                test_command=pt.test_command,
                test_framework="json-schema",
                run_single_test="python3 -m pytest {test_id} -v",
                source_ext=".json",
                import_style="",
                build_command=pt.build_command,
                static_check=pt.static_check_command,
            )
        else:
            # Fallback: use project type values directly
            return cls(
                language=pt.language_hint,
                test_command=pt.test_command,
                test_framework=pt.test_framework,
                run_single_test=f"{pt.test_command} {{test_id}}",
                source_ext=pt.source_extensions[0] if pt.source_extensions else "",
                import_style="",
                build_command=pt.build_command,
                static_check=pt.static_check_command,
            )
