"""Tests for toyshop.mc_scaffold — Fabric mod scaffolding."""

import json
import pytest
from pathlib import Path

from toyshop.mc_scaffold import (
    generate_build_gradle,
    generate_gradle_properties,
    generate_settings_gradle,
    generate_fabric_mod_json,
    extract_mod_metadata,
    extract_registries,
    generate_rcon_spec,
    scaffold_fabric_mod,
    copy_gradle_wrapper,
)


class TestGenerateBuildGradle:
    def test_contains_fabric_loom(self):
        result = generate_build_gradle("frostbow")
        assert "fabric-loom" in result

    def test_contains_mod_id(self):
        result = generate_build_gradle("frostbow")
        assert '"frostbow"' in result

    def test_contains_fabric_api_dependency(self):
        result = generate_build_gradle("mymod")
        assert "fabric-api" in result

    def test_contains_mojang_mappings(self):
        result = generate_build_gradle("mymod")
        assert "officialMojangMappings" in result

    def test_java_21(self):
        result = generate_build_gradle("mymod")
        assert "21" in result


class TestGenerateGradleProperties:
    def test_contains_minecraft_version(self):
        result = generate_gradle_properties("mymod")
        assert "minecraft_version=1.21.1" in result

    def test_contains_mod_id_as_archive(self):
        result = generate_gradle_properties("frostbow")
        assert "archives_base_name=frostbow" in result

    def test_custom_maven_group(self):
        result = generate_gradle_properties("mymod", maven_group="net.example")
        assert "maven_group=net.example" in result

    def test_custom_version(self):
        result = generate_gradle_properties("mymod", mod_version="2.0.0")
        assert "mod_version=2.0.0" in result


class TestGenerateSettingsGradle:
    def test_contains_fabric_maven(self):
        result = generate_settings_gradle("mymod")
        assert "maven.fabricmc.net" in result

    def test_contains_project_name(self):
        result = generate_settings_gradle("frostbow")
        assert "frostbow" in result


class TestGenerateFabricModJson:
    def test_valid_json(self):
        result = generate_fabric_mod_json("mymod", "My Mod")
        data = json.loads(result)
        assert data["id"] == "mymod"
        assert data["name"] == "My Mod"

    def test_schema_version(self):
        data = json.loads(generate_fabric_mod_json("mymod", "My Mod"))
        assert data["schemaVersion"] == 1

    def test_depends_on_fabric(self):
        data = json.loads(generate_fabric_mod_json("mymod", "My Mod"))
        assert "fabricloader" in data["depends"]
        assert "fabric-api" in data["depends"]

    def test_explicit_main_class(self):
        data = json.loads(generate_fabric_mod_json(
            "mymod", "My Mod", main_class="com.example.MyMod"
        ))
        assert data["entrypoints"]["main"] == ["com.example.MyMod"]

    def test_explicit_client_class(self):
        data = json.loads(generate_fabric_mod_json(
            "mymod", "My Mod",
            main_class="com.example.MyMod",
            client_class="com.example.MyModClient",
        ))
        assert data["entrypoints"]["client"] == ["com.example.MyModClient"]

    def test_fallback_entrypoint(self):
        data = json.loads(generate_fabric_mod_json(
            "frost_bow", "Frost Bow", maven_group="com.example"
        ))
        # Should generate a guessed entrypoint
        assert "main" in data["entrypoints"]
        assert len(data["entrypoints"]["main"]) == 1


class TestExtractModMetadata:
    def test_extract_mod_id(self):
        design = 'public static final String MOD_ID = "frostbow";'
        meta = extract_mod_metadata(design)
        assert meta["mod_id"] == "frostbow"

    def test_extract_main_class(self):
        design = "public class FrostBowMod implements ModInitializer {"
        meta = extract_mod_metadata(design)
        assert meta["main_class"] == "FrostBowMod"

    def test_extract_client_class(self):
        design = "public class FrostBowClient implements ClientModInitializer {"
        meta = extract_mod_metadata(design)
        assert meta["client_class"] == "FrostBowClient"

    def test_extract_maven_group(self):
        design = "package com.example.frostbow;"
        meta = extract_mod_metadata(design)
        assert meta["maven_group"] == "com.example.frostbow"

    def test_extract_mod_name_from_heading(self):
        design = "# Frost Bow Mod\n\nA cool mod."
        meta = extract_mod_metadata(design)
        assert meta["mod_name"] == "Frost Bow Mod"

    def test_empty_design(self):
        meta = extract_mod_metadata("")
        assert meta["mod_id"] is None
        assert meta["maven_group"] == "com.example"


class TestExtractRegistries:
    def test_extract_items(self):
        design = 'ITEMS.register("frost_bow", () -> new FrostBowItem());'
        result = extract_registries(design)
        assert "frost_bow" in result["items"]

    def test_extract_blocks(self):
        design = 'BLOCKS.register("ice_block", () -> new IceBlock());'
        result = extract_registries(design)
        assert "ice_block" in result["blocks"]

    def test_no_duplicates(self):
        design = (
            'ITEMS.register("frost_bow", () -> new FrostBowItem());\n'
            'Items.register("frost_bow", () -> new FrostBowItem());'
        )
        result = extract_registries(design)
        assert result["items"].count("frost_bow") == 1

    def test_empty_design(self):
        result = extract_registries("")
        assert result["items"] == []
        assert result["blocks"] == []


class TestGenerateRconSpec:
    def test_creates_file(self, tmp_path):
        design = 'ITEMS.register("frost_bow", () -> new FrostBowItem());'
        path = generate_rcon_spec(tmp_path, design, "frostbow")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["mod_id"] == "frostbow"
        assert "frost_bow" in data["items"]

    def test_empty_registries(self, tmp_path):
        path = generate_rcon_spec(tmp_path, "", "mymod")
        data = json.loads(path.read_text())
        assert data["items"] == []
        assert data["blocks"] == []


class TestScaffoldFabricMod:
    def test_creates_build_files(self, tmp_path):
        result = scaffold_fabric_mod(tmp_path, mod_id="testmod", design_md="")
        assert "build.gradle" in result["files_created"]
        assert "gradle.properties" in result["files_created"]
        assert "settings.gradle" in result["files_created"]
        assert (tmp_path / "build.gradle").exists()
        assert (tmp_path / "gradle.properties").exists()
        assert (tmp_path / "settings.gradle").exists()

    def test_creates_fabric_mod_json(self, tmp_path):
        result = scaffold_fabric_mod(tmp_path, mod_id="testmod", design_md="")
        fmj = tmp_path / "src" / "main" / "resources" / "fabric.mod.json"
        assert fmj.exists()
        data = json.loads(fmj.read_text())
        assert data["id"] == "testmod"

    def test_creates_client_source_dir(self, tmp_path):
        scaffold_fabric_mod(tmp_path, mod_id="testmod", design_md="")
        assert (tmp_path / "src" / "client" / "java").is_dir()

    def test_creates_rcon_tests_json(self, tmp_path):
        design = 'ITEMS.register("magic_wand", () -> new MagicWandItem());'
        scaffold_fabric_mod(tmp_path, mod_id="testmod", design_md=design)
        rcon = tmp_path / "rcon_tests.json"
        assert rcon.exists()
        data = json.loads(rcon.read_text())
        assert "magic_wand" in data["items"]

    def test_does_not_overwrite_existing(self, tmp_path):
        (tmp_path / "build.gradle").write_text("custom content")
        result = scaffold_fabric_mod(tmp_path, mod_id="testmod", design_md="")
        assert "build.gradle" not in result["files_created"]
        assert (tmp_path / "build.gradle").read_text() == "custom content"

    def test_extracts_metadata_from_design(self, tmp_path):
        design = (
            "# Frost Bow\n"
            'public static final String MOD_ID = "frostbow";\n'
            "package com.example.frostbow;\n"
            "public class FrostBowMod implements ModInitializer {\n"
        )
        result = scaffold_fabric_mod(tmp_path, design_md=design)
        assert result["mod_id"] == "frostbow"
        assert result["mod_name"] == "Frost Bow"
        assert result["maven_group"] == "com.example.frostbow"

    def test_returns_metadata(self, tmp_path):
        result = scaffold_fabric_mod(tmp_path, mod_id="testmod", design_md="")
        assert result["mod_id"] == "testmod"
        assert "files_created" in result


class TestCopyGradleWrapper:
    def test_no_template_returns_false(self, tmp_path):
        result = copy_gradle_wrapper(tmp_path, template=tmp_path / "nonexistent")
        assert result is False

    def test_copies_from_template(self, tmp_path):
        # Create a fake template
        template = tmp_path / "template"
        template.mkdir()
        (template / "gradlew").write_text("#!/bin/sh\necho gradle")
        (template / "gradlew.bat").write_text("@echo gradle")
        wrapper_dir = template / "gradle" / "wrapper"
        wrapper_dir.mkdir(parents=True)
        (wrapper_dir / "gradle-wrapper.jar").write_bytes(b"fake-jar")
        (wrapper_dir / "gradle-wrapper.properties").write_text("dist=url")

        target = tmp_path / "project"
        target.mkdir()
        result = copy_gradle_wrapper(target, template=template)
        assert result is True
        assert (target / "gradlew").exists()
        assert (target / "gradle" / "wrapper" / "gradle-wrapper.jar").exists()
