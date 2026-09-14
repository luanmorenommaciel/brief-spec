#!/usr/bin/env python3
"""Verify that Brief-Spec's release surfaces describe one coherent artifact."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

PLUGIN_MANIFESTS = (
    Path(".codex-plugin/plugin.json"),
    Path(".claude-plugin/plugin.json"),
    Path("plugin.json"),
)
MARKETPLACE_MANIFESTS = (
    Path(".agents/plugins/marketplace.json"),
    Path(".claude-plugin/marketplace.json"),
    Path(".github/plugin/marketplace.json"),
)
SCHEMA_FILES = (
    Path("schemas/brief-spec-delivery.schema.json"),
    Path("schemas/brief-spec-bundle-manifest.schema.json"),
    Path("schemas/brief-spec-delivery-receipt.schema.json"),
    Path("schemas/brief-spec-evidence.schema.json"),
    Path("schemas/brief-spec-outcome-brief.schema.json"),
    Path("schemas/brief-spec-session-checkpoint.schema.json"),
    Path("schemas/brief-spec-event.schema.json"),
    Path("schemas/briefspec-delivery.schema.json"),
    Path("schemas/bundle-manifest.schema.json"),
    Path("schemas/delivery-receipt.schema.json"),
    Path("schemas/evidence.schema.json"),
    Path("schemas/outcome-brief.schema.json"),
    Path("schemas/session-checkpoint.schema.json"),
)
HOOK_FILES = (
    Path("hooks/hooks.json"),
    Path("hooks/copilot.json"),
)
REQUIRED_FILES = (
    Path("README.md"),
    Path("LICENSE"),
    Path("pyproject.toml"),
    Path("src/briefspec/__init__.py"),
    Path("src/briefspec/__main__.py"),
    Path("src/briefspec/cli.py"),
    Path("src/briefspec/resources.py"),
    Path("src/brief_spec/__init__.py"),
    Path("src/brief_spec/__main__.py"),
    Path(".github/dependabot.yml"),
    Path(".github/workflows/ci.yml"),
    Path(".github/workflows/release.yml"),
    Path("docs/compatibility.md"),
    Path("docs/delivery.md"),
    Path("docs/examples.md"),
    Path("docs/repository-layout.md"),
    Path("docs/verification.md"),
    Path("docs/verification-v0.1.0.md"),
    Path("scripts/verify-release.py"),
    *PLUGIN_MANIFESTS,
    *MARKETPLACE_MANIFESTS,
    *SCHEMA_FILES,
    *HOOK_FILES,
    Path("scripts/briefspec-hook"),
    Path("scripts/brief-spec-hook"),
    Path("scripts/build-release-manifest.py"),
    Path("scripts/build-release-authorization.py"),
    Path("scripts/build-live-e2e-evidence.py"),
    Path("scripts/build-schema-bundle.py"),
    Path("scripts/generate-verification.py"),
    Path("release/truth-boundary.json"),
    Path("release/live-e2e-evidence.json"),
    Path("scripts/check-pypi-artifacts.py"),
    Path("scripts/run-renderer-smoke.py"),
    Path("scripts/run-browser-e2e.py"),
    Path("scripts/run-live-e2e.py"),
    Path("scripts/run-chronicle-e2e.py"),
    Path("scripts/snapshot-installation.py"),
    Path("packages/brief-spec-renderer-pdf/pyproject.toml"),
    Path("packages/brief-spec-renderer-pdf/README.md"),
    Path("packages/brief-spec-renderer-pdf/src/briefspec_renderer_pdf/__init__.py"),
    Path("packages/brief-spec-renderer-audio/pyproject.toml"),
    Path("packages/brief-spec-renderer-audio/README.md"),
    Path("packages/brief-spec-renderer-audio/src/briefspec_renderer_audio/__init__.py"),
    Path("packages/brief-spec-chronicle/pyproject.toml"),
    Path("packages/brief-spec-chronicle/README.md"),
    Path("packages/brief-spec-chronicle/src/brief_spec_chronicle/__init__.py"),
    Path("packages/brief-spec-chronicle/src/brief_spec_chronicle/cli.py"),
    Path("packages/brief-spec-chronicle/src/brief_spec_chronicle/derive.py"),
    Path("packages/brief-spec-chronicle/src/brief_spec_chronicle/rendering.py"),
    Path("packages/brief-spec-chronicle/src/brief_spec_chronicle/sources.py"),
    Path("packages/brief-spec-chronicle/src/brief_spec_chronicle/storage.py"),
    Path("packages/brief-spec-chronicle/schemas/brief-spec-chronicle.schema.json"),
    Path("packages/brief-spec-chronicle/schemas/brief-spec-chronicle-receipt.schema.json"),
    Path("packages/brief-spec-chronicle/schemas/brief-spec-decision.schema.json"),
    Path("packages/brief-spec-chronicle/schemas/brief-spec-drift.schema.json"),
    Path("packages/brief-spec-chronicle/schemas/brief-spec-lesson-proposal.schema.json"),
    Path("packages/brief-spec-renderer-video/pyproject.toml"),
    Path("packages/brief-spec-renderer-video/README.md"),
    Path("packages/brief-spec-renderer-video/src/brief_spec_renderer_video/__init__.py"),
    Path("skills/outcome-brief/SKILL.md"),
    Path("skills/outcome-brief/agents/openai.yaml"),
    Path("skills/session-checkpoint/SKILL.md"),
    Path("skills/session-checkpoint/agents/openai.yaml"),
    Path("skills/brief-spec/SKILL.md"),
    Path("skills/brief-spec/agents/openai.yaml"),
    Path("integrations/copilot/settings.json.example"),
    Path("integrations/copilot/cloud/README.md"),
    Path("output/README.md"),
)
PACKAGE_PROJECTS = (
    Path("packages/brief-spec-renderer-pdf/pyproject.toml"),
    Path("packages/brief-spec-renderer-audio/pyproject.toml"),
)
EXTENSION_PROJECTS = (
    (Path("packages/brief-spec-chronicle/pyproject.toml"), "brief-spec-chronicle"),
    (Path("packages/brief-spec-renderer-video/pyproject.toml"), "brief-spec-renderer-video"),
)
CANONICAL_PACKAGE_DIRECTORIES = {
    "brief-spec-chronicle",
    "brief-spec-renderer-audio",
    "brief-spec-renderer-pdf",
    "brief-spec-renderer-video",
}
LEGACY_PACKAGE_DIRECTORIES = {
    "briefspec-renderer-audio",
    "briefspec-renderer-pdf",
}
EXPECTED_PROJECTIONS = {
    "skills": "briefspec/resources/skills",
    "hooks": "briefspec/resources/hooks",
    "schemas": "briefspec/resources/schemas",
    "integrations/copilot": "briefspec/resources/integrations/copilot",
    "plugin.json": "briefspec/resources/manifests/plugin.json",
    ".codex-plugin/plugin.json": "briefspec/resources/manifests/codex-plugin.json",
    ".claude-plugin/plugin.json": "briefspec/resources/manifests/claude-plugin.json",
}
NESTED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PreCompact",
    "Stop",
}
COPILOT_EVENTS = {
    "sessionStart",
    "userPromptSubmitted",
    "postToolUse",
    "preCompact",
    "agentStop",
}
PLACEHOLDER = re.compile(
    r"(?ix)"
    r"\b(?:TODO|FIXME|TBD)\b"
    r"|\{\{\s*[^{}]+\s*\}\}"
    r"|<\s*(?:owner|repo|path|plugin-name|version)\s*>"
)
ROOT_REFERENCE = re.compile(
    r"\$\{(?P<variable>CLAUDE_PLUGIN_ROOT|PLUGIN_ROOT|COPILOT_PLUGIN_ROOT)\}"
    r"/(?P<target>[A-Za-z0-9._/-]+)"
)
EVENT_ARGUMENT = re.compile(r"(?:^|\s)--event\s+(?P<event>[A-Za-z][A-Za-z0-9]*)")
README_VERSION_BADGE = re.compile(
    r"\[!\[Source candidate (?P<label>\d+\.\d+\.\d+)\]"
    r"\(https://img\.shields\.io/badge/source_candidate-(?P<badge>\d+\.\d+\.\d+)-"
)
VERIFICATION_MARKER = re.compile(
    r"<!-- briefspec:verification:v1 version=(?P<version>\d+\.\d+\.\d+) -->"
)
ACTION_REFERENCE = re.compile(
    r"^\s*(?:-\s*)?uses:\s+(?P<action>[^@\s]+)@(?P<revision>[^\s#]+)",
    re.MULTILINE,
)


class _ReadmeBadgeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.badges: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        attributes = dict(attrs)
        source = attributes.get("src") or ""
        prefix = "https://img.shields.io/badge/source_candidate-"
        if source.startswith(prefix):
            self.badges.append(
                (attributes.get("alt") or "", source.removeprefix(prefix).partition("-")[0])
            )


class Verifier:
    def __init__(self) -> None:
        self.checks = 0
        self.errors: list[str] = []
        self.notes: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.errors.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


def load_json(path: Path, verifier: Verifier) -> dict[str, Any]:
    try:
        value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        verifier.require(False, f"{path}: invalid or unreadable JSON: {exc}")
        return {}
    verifier.require(isinstance(value, dict), f"{path}: top-level JSON value must be an object")
    return value if isinstance(value, dict) else {}


def load_pyproject(verifier: Verifier) -> dict[str, Any]:
    try:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        verifier.require(False, f"pyproject.toml: invalid or unreadable TOML: {exc}")
        return {}
    verifier.require(isinstance(value, dict), "pyproject.toml: top-level value must be a table")
    return value


def python_package_version(verifier: Verifier) -> str:
    path = ROOT / "src/briefspec/__init__.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        verifier.require(False, f"src/briefspec/__init__.py: cannot read version: {exc}")
        return ""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    verifier.require(False, "src/briefspec/__init__.py: literal __version__ assignment is required")
    return ""


def check_required_files(verifier: Verifier) -> None:
    for relative in REQUIRED_FILES:
        verifier.require(
            (ROOT / relative).is_file(),
            f"required release file is missing: {relative}",
        )


def check_repository_layout(verifier: Verifier) -> None:
    """Keep canonical source locations aligned with their distribution names."""

    packages_root = ROOT / "packages"
    observed = {
        path.name
        for path in packages_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    verifier.require(
        observed == CANONICAL_PACKAGE_DIRECTORIES,
        "packages/: expected only canonical distribution directories; "
        f"observed {sorted(observed)!r}",
    )
    for legacy_name in sorted(LEGACY_PACKAGE_DIRECTORIES):
        verifier.require(
            not (packages_root / legacy_name).exists(),
            f"packages/: legacy source directory must not exist: {legacy_name}",
        )
    for package_name in sorted(CANONICAL_PACKAGE_DIRECTORIES):
        relative = Path("packages") / package_name / "pyproject.toml"
        try:
            with (ROOT / relative).open("rb") as handle:
                project = tomllib.load(handle).get("project", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            verifier.require(False, f"{relative}: invalid package metadata: {exc}")
            continue
        verifier.require(
            project.get("name") == package_name,
            f"{relative}: project.name must match its canonical directory {package_name!r}",
        )

    verifier.require(
        (ROOT / "src/brief_spec").is_dir(),
        "src/brief_spec/: canonical Python package is required",
    )
    verifier.require(
        (ROOT / "src/briefspec").is_dir(),
        "src/briefspec/: 0.x compatibility package is required",
    )


def check_versions_and_manifests(
    verifier: Verifier,
    pyproject: dict[str, Any],
    documents: dict[Path, dict[str, Any]],
) -> None:
    project = pyproject.get("project", {})
    project_name = project.get("name")
    version = project.get("version")
    verifier.require(
        project_name == "brief-spec",
        "pyproject.toml: project.name must be brief-spec",
    )
    verifier.require(
        isinstance(version, str) and bool(version),
        "pyproject.toml: version is required",
    )
    verifier.require(
        project.get("readme") == "README.md",
        "pyproject.toml: project.readme must point to README.md",
    )

    versions: dict[str, Any] = {"src/briefspec/__init__.py": python_package_version(verifier)}
    for relative in PACKAGE_PROJECTS:
        try:
            with (ROOT / relative).open("rb") as handle:
                package = tomllib.load(handle).get("project", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            verifier.require(False, f"{relative}: invalid package metadata: {exc}")
            continue
        versions[str(relative)] = package.get("version")
        verifier.require(
            str(package.get("name", "")).startswith("brief-spec-renderer-"),
            f"{relative}: optional package name must start with brief-spec-renderer-",
        )
    for relative, expected_name in EXTENSION_PROJECTS:
        try:
            with (ROOT / relative).open("rb") as handle:
                package = tomllib.load(handle).get("project", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            verifier.require(False, f"{relative}: invalid extension metadata: {exc}")
            continue
        verifier.require(
            package.get("name") == expected_name,
            f"{relative}: extension package name must be {expected_name}",
        )
        verifier.require(
            isinstance(package.get("version"), str) and bool(package.get("version")),
            f"{relative}: extension package version is required",
        )
        verifier.require(
            package.get("requires-python") == ">=3.11",
            f"{relative}: extension package must support Python 3.11+",
        )
    for relative in PLUGIN_MANIFESTS:
        manifest = documents[relative]
        versions[str(relative)] = manifest.get("version")
        verifier.require(
            manifest.get("name") == project_name,
            f"{relative}: name must match pyproject project.name",
        )
        required_fields = (
            ("$schema", "name", "version", "description", "license")
            if relative == Path("plugin.json")
            else ("name", "version", "description", "license", "skills")
        )
        for field in required_fields:
            verifier.require(
                bool(manifest.get(field)),
                f"{relative}: required field {field!r} is missing",
            )

    for label, observed in versions.items():
        verifier.require(observed == version, f"{label}: version {observed!r} != {version!r}")

    codex_skills = documents[Path(".codex-plugin/plugin.json")].get("skills")
    claude_skills = documents[Path(".claude-plugin/plugin.json")].get("skills")
    verifier.require(
        codex_skills == "./skills/",
        ".codex-plugin/plugin.json: skills must be ./skills/",
    )
    verifier.require(
        claude_skills == "./skills/",
        ".claude-plugin/plugin.json: skills must be ./skills/",
    )
    root_manifest = documents[Path("plugin.json")]
    verifier.require(
        root_manifest.get("$schema")
        == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "plugin.json: Agent Plugins schema must be explicit",
    )
    verifier.require(
        set(root_manifest)
        <= {
            "$schema",
            "name",
            "version",
            "description",
            "author",
            "homepage",
            "repository",
            "license",
            "keywords",
            "extensions",
        },
        "plugin.json: Agent Plugins manifest is closed",
    )


def check_versioned_release_evidence(verifier: Verifier, version: str) -> None:
    try:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        verification = (ROOT / "docs/verification.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError as exc:
        verifier.require(False, f"cannot read versioned release evidence: {exc}")
        return

    parser = _ReadmeBadgeParser()
    parser.feed(readme)
    parser.badges.extend(
        (f"Source candidate {badge.group('label')}", badge.group("badge"))
        for badge in README_VERSION_BADGE.finditer(readme)
    )
    verifier.require(bool(parser.badges), "README.md: version badge is missing or malformed")
    for label, badge_version in parser.badges:
        verifier.require(
            label == f"Source candidate {version}" and badge_version == version,
            f"README.md: version badge must match {version}",
        )
    verifier.require(
        "Public release:" in readme and "Source candidate:" in readme,
        "README.md: publication and source candidate boundaries must be explicit",
    )

    marker = VERIFICATION_MARKER.search(verification)
    verifier.require(
        marker is not None and marker.group("version") == version,
        f"docs/verification.md: verification marker must match {version}",
    )
    verifier.require(
        f"## [{version}]" in changelog,
        f"CHANGELOG.md: release section for {version} is missing",
    )


def check_workflow_action_pins(verifier: Verifier) -> None:
    for relative in sorted(Path(".github/workflows").glob("*.yml")):
        try:
            text = (ROOT / relative).read_text(encoding="utf-8")
        except OSError as exc:
            verifier.require(False, f"cannot read {relative}: {exc}")
            continue
        references = list(ACTION_REFERENCE.finditer(text))
        verifier.require(bool(references), f"{relative}: no action references found")
        for match in references:
            action = match.group("action")
            revision = match.group("revision")
            verifier.require(
                bool(re.fullmatch(r"[0-9a-f]{40}", revision)),
                f"{relative}: {action} must be pinned to a full commit SHA",
            )


def marketplace_source(entry: dict[str, Any]) -> str | None:
    source = entry.get("source")
    if isinstance(source, str):
        return source
    if isinstance(source, dict) and source.get("source") == "local":
        path = source.get("path")
        return path if isinstance(path, str) else None
    return None


def check_marketplaces(
    verifier: Verifier,
    version: str,
    documents: dict[Path, dict[str, Any]],
) -> None:
    for relative in MARKETPLACE_MANIFESTS:
        marketplace = documents[relative]
        verifier.require(
            marketplace.get("name") == "brief-spec",
            f"{relative}: marketplace name must be brief-spec",
        )
        plugins = marketplace.get("plugins")
        verifier.require(
            isinstance(plugins, list) and len(plugins) == 1,
            f"{relative}: exactly one plugin entry is required",
        )
        if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
            continue
        entry = plugins[0]
        verifier.require(entry.get("name") == "brief-spec", f"{relative}: plugin name mismatch")
        source = marketplace_source(entry)
        verifier.require(source == ".", f"{relative}: plugin source must be the repository root")
        if relative == Path(".agents/plugins/marketplace.json"):
            policy = entry.get("policy")
            verifier.require(
                policy
                == {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                f"{relative}: Codex installation policy drifted",
            )
            verifier.require(
                entry.get("category") == "Productivity",
                f"{relative}: Codex category must be Productivity",
            )
        else:
            verifier.require(
                entry.get("version") == version,
                f"{relative}: plugin version must match pyproject",
            )
            metadata = marketplace.get("metadata")
            metadata_version = metadata.get("version") if isinstance(metadata, dict) else None
            verifier.require(
                metadata_version == version,
                f"{relative}: metadata.version must match pyproject",
            )

    claude_marketplace = documents[Path(".claude-plugin/marketplace.json")]
    copilot_marketplace = documents[Path(".github/plugin/marketplace.json")]
    verifier.require(
        claude_marketplace == copilot_marketplace,
        "Claude and GitHub marketplace manifests must remain byte-semantically equivalent",
    )

    settings = documents[Path("integrations/copilot/settings.json.example")]
    marketplaces = settings.get("extraKnownMarketplaces")
    brief_spec = marketplaces.get("brief-spec") if isinstance(marketplaces, dict) else None
    source = brief_spec.get("source") if isinstance(brief_spec, dict) else None
    verifier.require(
        isinstance(source, dict) and source.get("ref") == f"v{version}",
        "integrations/copilot/settings.json.example: marketplace ref must match release version",
    )
    enabled = settings.get("enabledPlugins")
    verifier.require(
        isinstance(enabled, dict) and enabled.get("brief-spec@brief-spec") is True,
        "integrations/copilot/settings.json.example: brief-spec plugin must be enabled",
    )


def walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def check_schemas(verifier: Verifier, documents: dict[Path, dict[str, Any]]) -> None:
    identifiers: set[str] = set()
    for relative in SCHEMA_FILES:
        schema = documents[relative]
        verifier.require(
            schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
            f"{relative}: must declare JSON Schema draft 2020-12",
        )
        identifier = schema.get("$id")
        verifier.require(
            isinstance(identifier, str) and bool(identifier),
            f"{relative}: $id is required",
        )
        if isinstance(identifier, str):
            verifier.require(identifier not in identifiers, f"{relative}: duplicate schema $id")
            identifiers.add(identifier)
        verifier.require(schema.get("type") == "object", f"{relative}: root type must be object")
        verifier.require(
            isinstance(schema.get("properties"), dict),
            f"{relative}: object schema must declare properties",
        )
        for node in walk_json(schema):
            reference = node.get("$ref")
            if not isinstance(reference, str) or "://" in reference or reference.startswith("#"):
                continue
            target_name = reference.split("#", maxsplit=1)[0]
            target = (ROOT / relative).parent / target_name
            verifier.require(
                target.is_file(),
                f"{relative}: local schema reference does not exist: {reference}",
            )

        try:
            from jsonschema.validators import validator_for
        except ImportError:
            verifier.note("jsonschema is unavailable; structural schema checks ran")
        else:
            try:
                validator_for(schema).check_schema(schema)
            except Exception as exc:
                verifier.require(False, f"{relative}: invalid JSON Schema: {exc}")
            else:
                verifier.require(True, f"{relative}: JSON Schema validation")


def shipping_text_files() -> Iterable[Path]:
    yield from PLUGIN_MANIFESTS
    yield from MARKETPLACE_MANIFESTS
    for path in sorted((ROOT / "skills").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path.relative_to(ROOT)


def check_placeholders(verifier: Verifier) -> None:
    for relative in shipping_text_files():
        try:
            text = (ROOT / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            verifier.require(False, f"{relative}: cannot inspect shipping text: {exc}")
            continue
        match = PLACEHOLDER.search(text)
        verifier.require(
            match is None,
            f"{relative}: unresolved shipping placeholder {match.group(0)!r}"
            if match
            else f"{relative}: placeholder scan",
        )


def command_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"command", "bash", "powershell"} and isinstance(child, str):
                yield child
            else:
                yield from command_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from command_strings(child)


def check_hook_file(
    verifier: Verifier,
    relative: Path,
    document: dict[str, Any],
    expected_events: set[str],
) -> None:
    hooks = document.get("hooks")
    verifier.require(isinstance(hooks, dict), f"{relative}: hooks must be an object")
    if not isinstance(hooks, dict):
        return
    verifier.require(set(hooks) == expected_events, f"{relative}: lifecycle event set drifted")
    command_count = 0
    for event, configuration in hooks.items():
        commands = list(command_strings(configuration))
        verifier.require(bool(commands), f"{relative}: {event} has no command handler")
        for command in commands:
            command_count += 1
            references = list(ROOT_REFERENCE.finditer(command))
            verifier.require(
                len(references) == 1,
                f"{relative}: {event} command must contain exactly one "
                f"plugin-root target: {command}",
            )
            if references:
                target = ROOT / references[0].group("target")
                verifier.require(target.is_file(), f"{relative}: hook target is missing: {target}")
                verifier.require(
                    os.access(target, os.X_OK),
                    f"{relative}: hook target must be executable: {target.relative_to(ROOT)}",
                )
            event_argument = EVENT_ARGUMENT.search(command)
            verifier.require(
                event_argument is not None and event_argument.group("event") == event,
                f"{relative}: command event argument must match {event}: {command}",
            )
    verifier.require(command_count > 0, f"{relative}: at least one hook command is required")


def check_hooks(verifier: Verifier, documents: dict[Path, dict[str, Any]]) -> None:
    check_hook_file(
        verifier,
        Path("hooks/hooks.json"),
        documents[Path("hooks/hooks.json")],
        NESTED_EVENTS,
    )
    copilot = documents[Path("hooks/copilot.json")]
    verifier.require(copilot.get("version") == 1, "hooks/copilot.json: version must be 1")
    check_hook_file(verifier, Path("hooks/copilot.json"), copilot, COPILOT_EVENTS)


def expected_wheel_members(source: Path, destination: str) -> Iterable[tuple[Path, str]]:
    if source.is_file():
        yield source, destination
        return
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        member = f"{destination.rstrip('/')}/{path.relative_to(source).as_posix()}"
        yield path, member


def check_resource_projections(
    verifier: Verifier,
    pyproject: dict[str, Any],
    wheel: Path | None,
) -> None:
    tool = pyproject.get("tool", {})
    hatch = tool.get("hatch", {}) if isinstance(tool, dict) else {}
    build = hatch.get("build", {}) if isinstance(hatch, dict) else {}
    targets = build.get("targets", {}) if isinstance(build, dict) else {}
    wheel_config = targets.get("wheel", {}) if isinstance(targets, dict) else {}
    projections = wheel_config.get("force-include", {}) if isinstance(wheel_config, dict) else {}
    verifier.require(
        isinstance(projections, dict),
        "pyproject.toml: wheel.force-include is required",
    )
    if not isinstance(projections, dict):
        return
    for source, destination in EXPECTED_PROJECTIONS.items():
        verifier.require(
            projections.get(source) == destination,
            f"pyproject.toml: missing resource projection {source!r} -> {destination!r}",
        )
        verifier.require(
            (ROOT / source).exists(),
            f"resource projection source is missing: {source}",
        )

    destinations = list(projections.values())
    verifier.require(
        len(destinations) == len(set(destinations)),
        "pyproject.toml: wheel resource destinations must be unique",
    )
    for source, destination in projections.items():
        verifier.require(
            isinstance(source, str) and not Path(source).is_absolute(),
            f"pyproject.toml: resource source must be relative: {source!r}",
        )
        verifier.require(
            isinstance(destination, str) and destination.startswith("briefspec/resources/"),
            f"pyproject.toml: resource destination escapes package resources: {destination!r}",
        )

    if wheel is None:
        return
    verifier.require(wheel.is_file(), f"wheel artifact does not exist: {wheel}")
    if not wheel.is_file():
        return
    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        verifier.require(False, f"cannot inspect wheel {wheel}: {exc}")
        return
    with archive:
        members = set(archive.namelist())
        for source_text, destination in EXPECTED_PROJECTIONS.items():
            for source, member in expected_wheel_members(ROOT / source_text, destination):
                verifier.require(
                    member in members,
                    f"{wheel.name}: projected resource missing: {member}",
                )
                if member in members:
                    verifier.require(
                        archive.read(member) == source.read_bytes(),
                        f"{wheel.name}: projected resource differs from source: {member}",
                    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel",
        type=Path,
        help="also verify the built wheel contains byte-identical projected resources",
    )
    parser.add_argument(
        "--truth-boundary",
        action="store_true",
        help="also require generated public evidence and exact-SHA release authorization",
    )
    return parser.parse_args()


def check_truth_boundary(verifier: Verifier, version: str) -> None:
    process = subprocess.run(
        [sys.executable, "scripts/generate-verification.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    verifier.require(process.returncode == 0, process.stderr.strip() or process.stdout.strip())
    live_process = subprocess.run(
        [sys.executable, "scripts/build-live-e2e-evidence.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    verifier.require(
        live_process.returncode == 0,
        live_process.stderr.strip() or live_process.stdout.strip(),
    )
    evidence = load_json(Path("release/truth-boundary.json"), verifier)
    verifier.require(
        evidence.get("version") == version, "truth boundary version must match package"
    )
    verifier.require(
        evidence.get("repository") == "luanmorenommaciel/brief-spec",
        "truth boundary must use the canonical repository",
    )
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    verifier.require(
        "brief-spec-candidate-$GITHUB_SHA" in release_workflow,
        "release workflow must consume the exact-SHA CI candidate",
    )
    verifier.require(
        "python -m build" not in release_workflow,
        "tag-triggered release workflow must not rebuild distributions",
    )
    canonical_schemas = (
        "brief-spec-delivery.schema.json",
        "brief-spec-bundle-manifest.schema.json",
        "brief-spec-delivery-receipt.schema.json",
        "brief-spec-evidence.schema.json",
        "brief-spec-outcome-brief.schema.json",
        "brief-spec-session-checkpoint.schema.json",
    )
    expected_prefix = "https://github.com/luanmorenommaciel/brief-spec/releases/download/v0.5.0/"
    for name in canonical_schemas:
        schema = load_json(Path("schemas") / name, verifier)
        verifier.require(
            str(schema.get("$id", "")).startswith(expected_prefix),
            f"{name}: canonical schema ID must use immutable release assets",
        )


def main() -> int:
    args = parse_args()
    verifier = Verifier()
    check_required_files(verifier)
    check_repository_layout(verifier)
    pyproject = load_pyproject(verifier)

    json_files = {
        *PLUGIN_MANIFESTS,
        *MARKETPLACE_MANIFESTS,
        *SCHEMA_FILES,
        *HOOK_FILES,
        Path("integrations/copilot/settings.json.example"),
    }
    documents = {path: load_json(path, verifier) for path in sorted(json_files)}
    version = pyproject.get("project", {}).get("version", "")

    check_versions_and_manifests(verifier, pyproject, documents)
    check_versioned_release_evidence(verifier, version)
    check_workflow_action_pins(verifier)
    check_marketplaces(verifier, version, documents)
    check_schemas(verifier, documents)
    check_placeholders(verifier)
    check_hooks(verifier, documents)
    check_resource_projections(verifier, pyproject, args.wheel)
    if args.truth_boundary:
        check_truth_boundary(verifier, version)

    if verifier.errors:
        print(
            f"Brief-Spec release verification failed "
            f"({len(verifier.errors)} error(s), {verifier.checks} checks):",
            file=sys.stderr,
        )
        for error in verifier.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Brief-Spec release verification passed ({verifier.checks} checks).")
    for note in sorted(set(verifier.notes)):
        print(f"NOTE: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
