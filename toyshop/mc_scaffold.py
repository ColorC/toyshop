"""Fabric mod scaffold for java-minecraft projects.

Generates a complete Fabric mod project skeleton:
- build.gradle + gradle.properties + settings.gradle (Fabric Loom)
- Gradle wrapper (copied from template mod)
- fabric.mod.json (from design.md metadata)
- rcon_tests.json (from design.md registries)

Called by TDD pipeline after Phase 1 (Signature Extraction) to ensure
Code Agent works within a real Fabric project, not a bare Java project.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration — where to find the template Gradle wrapper
# ---------------------------------------------------------------------------

# Preferred template mod locations (tried in order)
_TEMPLATE_CANDIDATES = [
    Path("/home/dministrator/work/custom-block-mod"),
    Path("/home/dministrator/work/modfactory/harness"),
]

# Fabric defaults for MC 1.21.1
_DEFAULTS = {
    "minecraft_version": "1.21.1",
    "loader_version": "0.18.2",
    "loom_version": "1.15.4",
    "fabric_version": "0.116.8+1.21.1",
    "java_version": "21",
}


def _find_template_mod() -> Path | None:
    """Find a template mod with Gradle wrapper files."""
    for candidate in _TEMPLATE_CANDIDATES:
        if (candidate / "gradlew").exists() and (candidate / "gradle" / "wrapper").exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Gradle wrapper
# ---------------------------------------------------------------------------


def copy_gradle_wrapper(workspace: Path, template: Path | None = None) -> bool:
    """Copy gradlew, gradlew.bat, gradle/wrapper/ from template mod."""
    template = template or _find_template_mod()
    if not template:
        return False

    # gradlew + gradlew.bat
    for name in ("gradlew", "gradlew.bat"):
        src = template / name
        if src.exists():
            shutil.copy2(src, workspace / name)
            if name == "gradlew":
                (workspace / name).chmod(0o755)

    # gradle/wrapper/
    wrapper_src = template / "gradle" / "wrapper"
    wrapper_dst = workspace / "gradle" / "wrapper"
    if wrapper_src.exists():
        wrapper_dst.mkdir(parents=True, exist_ok=True)
        for f in wrapper_src.iterdir():
            if f.is_file():
                shutil.copy2(f, wrapper_dst / f.name)

    return (workspace / "gradlew").exists()


# ---------------------------------------------------------------------------
# build.gradle + gradle.properties + settings.gradle
# ---------------------------------------------------------------------------


def generate_build_gradle(mod_id: str) -> str:
    """Generate Fabric Loom build.gradle."""
    return f"""\
plugins {{
\tid 'fabric-loom' version "${{loom_version}}"
}}

version = project.mod_version
group = project.maven_group

base {{
\tarchivesName = project.archives_base_name
}}

loom {{
\tsplitEnvironmentSourceSets()

\tmods {{
\t\t"{mod_id}" {{
\t\t\tsourceSet sourceSets.main
\t\t\tsourceSet sourceSets.client
\t\t}}
\t}}
}}

dependencies {{
\tminecraft "com.mojang:minecraft:${{project.minecraft_version}}"
\tmappings loom.officialMojangMappings()
\tmodImplementation "net.fabricmc:fabric-loader:${{project.loader_version}}"
\tmodImplementation "net.fabricmc.fabric-api:fabric-api:${{project.fabric_version}}"
}}

processResources {{
\tinputs.property "version", project.version
\tfilesMatching("fabric.mod.json") {{
\t\texpand "version": inputs.properties.version
\t}}
}}

tasks.withType(JavaCompile).configureEach {{
\tit.options.release = {_DEFAULTS['java_version']}
}}

java {{
\twithSourcesJar()
\tsourceCompatibility = JavaVersion.VERSION_{_DEFAULTS['java_version']}
\ttargetCompatibility = JavaVersion.VERSION_{_DEFAULTS['java_version']}
}}
"""


def generate_gradle_properties(
    mod_id: str,
    mod_version: str = "1.0.0",
    maven_group: str = "com.example",
) -> str:
    """Generate gradle.properties for Fabric mod."""
    return f"""\
org.gradle.jvmargs=-Xmx1G
org.gradle.parallel=true
org.gradle.configuration-cache=false

# Fabric Properties
minecraft_version={_DEFAULTS['minecraft_version']}
loader_version={_DEFAULTS['loader_version']}
loom_version={_DEFAULTS['loom_version']}

# Mod Properties
mod_version={mod_version}
maven_group={maven_group}
archives_base_name={mod_id}

# Dependencies
fabric_version={_DEFAULTS['fabric_version']}
"""


def generate_settings_gradle(mod_id: str) -> str:
    """Generate settings.gradle."""
    return f"""\
pluginManagement {{
\trepositories {{
\t\tgradlePluginPortal()
\t\tmaven {{ url = 'https://maven.fabricmc.net/' }}
\t}}
}}

rootProject.name = '{mod_id}'
"""


# ---------------------------------------------------------------------------
# fabric.mod.json
# ---------------------------------------------------------------------------


def generate_fabric_mod_json(
    mod_id: str,
    mod_name: str,
    description: str = "",
    main_class: str | None = None,
    client_class: str | None = None,
    maven_group: str = "com.example",
) -> str:
    """Generate fabric.mod.json."""
    entrypoints: dict[str, list[str]] = {}
    if main_class:
        entrypoints["main"] = [main_class]
    if client_class:
        entrypoints["client"] = [client_class]

    # Fallback: guess from maven_group + mod_id
    if not entrypoints:
        pkg = f"{maven_group}.{mod_id.replace('-', '')}"
        # CamelCase mod name
        camel = "".join(w.capitalize() for w in mod_id.replace("-", "_").split("_"))
        entrypoints["main"] = [f"{pkg}.{camel}Mod"]

    data = {
        "schemaVersion": 1,
        "id": mod_id,
        "version": "${version}",
        "name": mod_name or mod_id,
        "description": description,
        "authors": ["ModFactory"],
        "license": "MIT",
        "environment": "*",
        "entrypoints": entrypoints,
        "depends": {
            "fabricloader": f">={_DEFAULTS['loader_version']}",
            "minecraft": f"~{_DEFAULTS['minecraft_version']}",
            "java": f">={_DEFAULTS['java_version']}",
            "fabric-api": "*",
        },
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Extract mod metadata from design.md
# ---------------------------------------------------------------------------


def extract_mod_metadata(design_md: str) -> dict:
    """Extract mod_id, mod_name, main_class, client_class, maven_group from design.md."""
    result: dict[str, str | None] = {
        "mod_id": None,
        "mod_name": None,
        "main_class": None,
        "client_class": None,
        "maven_group": "com.example",
    }

    # mod_id: look for MOD_ID = "xxx" or @Mod("xxx") or id = "xxx"
    m = re.search(r'MOD_ID\s*=\s*["\']([a-z_]+)["\']', design_md)
    if m:
        result["mod_id"] = m.group(1)
    else:
        m = re.search(r'@Mod\(["\']([a-z_]+)["\']\)', design_md)
        if m:
            result["mod_id"] = m.group(1)

    # main_class: look for "implements ModInitializer" or entrypoint patterns
    for pattern in [
        r'class\s+(\w+)\s+implements\s+ModInitializer',
        r'entrypoints.*?main.*?["\']([a-zA-Z_.]+)["\']',
    ]:
        m = re.search(pattern, design_md, re.DOTALL)
        if m:
            result["main_class"] = m.group(1)
            break

    # client_class
    for pattern in [
        r'class\s+(\w+)\s+implements\s+ClientModInitializer',
        r'entrypoints.*?client.*?["\']([a-zA-Z_.]+)["\']',
    ]:
        m = re.search(pattern, design_md, re.DOTALL)
        if m:
            result["client_class"] = m.group(1)
            break

    # maven_group: from package declarations
    m = re.search(r'package\s+([\w.]+)', design_md)
    if m:
        pkg = m.group(1)
        # Take first 2-3 segments as group
        parts = pkg.split(".")
        if len(parts) >= 2:
            result["maven_group"] = ".".join(parts[:min(3, len(parts))])

    # mod_name: from title or first heading
    m = re.search(r'^#\s+(.+)', design_md, re.MULTILINE)
    if m:
        result["mod_name"] = m.group(1).strip()

    return result


# ---------------------------------------------------------------------------
# Extract items/blocks for rcon_tests.json
# ---------------------------------------------------------------------------


def extract_registries(design_md: str, stub_files: list[str] | None = None) -> dict:
    """Extract mod_id, items, blocks from design.md for rcon_tests.json.

    Looks for registry patterns like:
    - Items.register("frost_bow", ...)
    - ITEMS.register("frost_bow", ...)
    - RegistryObject<Item> FROST_BOW = ...register("frost_bow", ...)
    - Registry.register(Registries.ITEM, ..., "frost_bow", ...)
    """
    items: list[str] = []
    blocks: list[str] = []

    # Item registrations
    for m in re.finditer(
        r'(?:ITEMS?|Items?|Registries\.ITEM).*?register\w*\(\s*["\']([a-z_]+)["\']',
        design_md,
    ):
        name = m.group(1)
        if name not in items:
            items.append(name)

    # Block registrations
    for m in re.finditer(
        r'(?:BLOCKS?|Blocks?|Registries\.BLOCK).*?register\w*\(\s*["\']([a-z_]+)["\']',
        design_md,
    ):
        name = m.group(1)
        if name not in blocks:
            blocks.append(name)

    # Also check for ENTITY_TYPE registrations → items (for spawn eggs etc.)
    for m in re.finditer(
        r'(?:ENTITY_TYPES?|EntityTypes?).*?register\w*\(\s*["\']([a-z_]+)["\']',
        design_md,
    ):
        # Entity types aren't items/blocks, skip for now
        pass

    return {"items": items, "blocks": blocks}


def generate_rcon_spec(workspace: Path, design_md: str, mod_id: str) -> Path:
    """Generate rcon_tests.json from design.md registries."""
    registries = extract_registries(design_md)
    spec = {
        "mod_id": mod_id,
        "blocks": registries["blocks"],
        "items": registries["items"],
    }
    path = workspace / "rcon_tests.json"
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main scaffold function
# ---------------------------------------------------------------------------


def scaffold_fabric_mod(
    workspace: Path,
    mod_id: str | None = None,
    design_md: str | None = None,
    stub_files: list[str] | None = None,
) -> dict:
    """Scaffold a complete Fabric mod project in workspace.

    Returns dict with scaffold metadata (mod_id, files_created, etc.).
    """
    # Read design.md if not provided
    if design_md is None:
        design_path = workspace / "openspec" / "design.md"
        if design_path.exists():
            design_md = design_path.read_text(encoding="utf-8")
        else:
            design_md = ""

    # Extract metadata
    meta = extract_mod_metadata(design_md)
    mod_id = mod_id or meta.get("mod_id") or "mymod"
    mod_name = meta.get("mod_name") or mod_id
    maven_group = meta.get("maven_group") or "com.example"
    main_class = meta.get("main_class")
    client_class = meta.get("client_class")

    files_created: list[str] = []

    # 1. Gradle wrapper
    if not (workspace / "gradlew").exists():
        if copy_gradle_wrapper(workspace):
            files_created.extend(["gradlew", "gradlew.bat", "gradle/wrapper/"])

    # 2. build.gradle (only if not exists — don't overwrite agent's work)
    if not (workspace / "build.gradle").exists():
        (workspace / "build.gradle").write_text(
            generate_build_gradle(mod_id), encoding="utf-8"
        )
        files_created.append("build.gradle")

    # 3. gradle.properties
    if not (workspace / "gradle.properties").exists():
        (workspace / "gradle.properties").write_text(
            generate_gradle_properties(mod_id, maven_group=maven_group),
            encoding="utf-8",
        )
        files_created.append("gradle.properties")

    # 4. settings.gradle
    if not (workspace / "settings.gradle").exists():
        (workspace / "settings.gradle").write_text(
            generate_settings_gradle(mod_id), encoding="utf-8"
        )
        files_created.append("settings.gradle")

    # 5. fabric.mod.json
    resources = workspace / "src" / "main" / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    fmj_path = resources / "fabric.mod.json"
    if not fmj_path.exists():
        fmj_path.write_text(
            generate_fabric_mod_json(
                mod_id=mod_id,
                mod_name=mod_name,
                main_class=main_class,
                client_class=client_class,
                maven_group=maven_group,
            ),
            encoding="utf-8",
        )
        files_created.append("src/main/resources/fabric.mod.json")

    # 6. Client source set directory (Fabric Loom splitEnvironmentSourceSets)
    (workspace / "src" / "client" / "java").mkdir(parents=True, exist_ok=True)

    # 7. rcon_tests.json
    if not (workspace / "rcon_tests.json").exists():
        generate_rcon_spec(workspace, design_md, mod_id)
        files_created.append("rcon_tests.json")

    return {
        "mod_id": mod_id,
        "mod_name": mod_name,
        "maven_group": maven_group,
        "main_class": main_class,
        "client_class": client_class,
        "files_created": files_created,
    }
