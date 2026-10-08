#!/usr/bin/env python3
"""Build or verify sanitized live-host evidence bound to release-relevant source bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release" / "live-e2e-evidence.json"
REQUIRED_HOSTS = ("codex", "claude", "omp", "grok", "kimi")
FINGERPRINT_ROOTS = (
    ".github",
    ".claude-plugin",
    ".codex-plugin",
    "hooks",
    "integrations",
    "packages",
    "schemas",
    "scripts",
    "skills",
    "src",
)
FINGERPRINT_FILES = ("plugin.json", "pyproject.toml")


def _version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = [ROOT / name for name in FINGERPRINT_FILES]
    for root_name in FINGERPRINT_ROOTS:
        paths.extend(path for path in (ROOT / root_name).rglob("*") if path.is_file())
    for path in sorted(set(paths)):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(ROOT).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _host_version(host: str) -> str | None:
    try:
        result = subprocess.run(
            [host, "--version"], text=True, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else None


def _failure_category(error: str) -> str:
    if "tool_output_error" in error:
        return "host-tool-output-error"
    if "did not emit a complete" in error:
        return "incomplete-typed-region"
    return "scenario-failed"


def _scenario_record(result: dict[str, Any]) -> dict[str, Any]:
    bundle = Path(result["rendered"]["target"])
    canonical_sha256 = None
    bundle_sha256 = None
    if bundle.is_file():
        bundle_sha256 = _sha256(bundle)
        with ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        canonical_sha256 = manifest.get("canonical_sha256")
    return {
        "work_type": result.get("work_type"),
        "subject": result.get("subject"),
        "mode": result.get("mode"),
        "status": result.get("status"),
        "hook_observed": result.get("hook_observed"),
        "classification_matches": result.get("classification_matches"),
        "strict_validation": result.get("strict_validation", {}).get("status"),
        "resolved": result.get("resolved", {}).get("status"),
        "rendered": result.get("rendered", {}).get("status"),
        "delivered": result.get("delivered", {}).get("status"),
        "authorized_changes_only": result.get("authorized_changes_only"),
        "models": sorted(set(result.get("models", []))),
        "cost_usd": result.get("cost_usd"),
        "session_ref_sha256": sorted(
            hashlib.sha256(str(value).encode()).hexdigest()
            for value in result.get("session_refs", [])
        ),
        "canonical_sha256": canonical_sha256,
        "bundle_sha256": bundle_sha256,
    }


def _host_record(host: str, summary_path: Path) -> dict[str, Any]:
    value = json.loads(summary_path.read_text(encoding="utf-8"))
    scenarios = sorted(
        (_scenario_record(result) for result in value.get("results", [])),
        key=lambda item: (str(item["work_type"]), str(item["mode"])),
    )
    failures = sorted(
        {
            (
                str(item.get("work_type")),
                str(item.get("mode")),
                _failure_category(str(item.get("error", ""))),
            )
            for item in value.get("execution_failures", [])
        }
    )
    required = int(value.get("expected_scenarios", 0))
    passed = sum(item["status"] == "PASS" for item in scenarios)
    return {
        "host": host,
        "host_version": _host_version(host),
        "required": required,
        "passed": passed,
        "status": "PASS" if required and passed == required and not failures else "HOLD",
        "summary_sha256": _sha256(summary_path),
        "scenarios": scenarios,
        "failures": [
            {"work_type": work_type, "mode": mode, "category": category}
            for work_type, mode, category in failures
        ],
    }


def _validate(value: dict[str, Any], *, require_authorized: bool) -> None:
    if value.get("source_worktree_fingerprint") != source_fingerprint():
        raise SystemExit("Live E2E evidence does not match the release-relevant source bytes")
    hosts = value.get("hosts", {})
    if sorted(hosts) != sorted(REQUIRED_HOSTS):
        raise SystemExit("Live E2E evidence does not cover every required host")
    expected_status = (
        "authorized"
        if all(hosts[name].get("status") == "PASS" for name in REQUIRED_HOSTS)
        else "blocked"
    )
    if value.get("status") != expected_status:
        raise SystemExit("Live E2E evidence status is inconsistent with its host records")
    if require_authorized and expected_status != "authorized":
        held = ", ".join(name for name in REQUIRED_HOSTS if hosts[name].get("status") != "PASS")
        raise SystemExit(f"Live E2E release authorization is blocked by: {held}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="append", default=[], metavar="HOST=PATH")
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-authorized", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.check:
        value = json.loads(output.read_text(encoding="utf-8"))
        _validate(value, require_authorized=args.require_authorized)
        print(output)
        return 0

    summaries: dict[str, Path] = {}
    for specification in args.summary:
        host, separator, path = specification.partition("=")
        if not separator or host not in REQUIRED_HOSTS:
            raise SystemExit(f"Invalid --summary value: {specification}")
        summaries[host] = Path(path).resolve()
    if sorted(summaries) != sorted(REQUIRED_HOSTS):
        raise SystemExit("Provide exactly one --summary for every required host")
    hosts = {host: _host_record(host, summaries[host]) for host in REQUIRED_HOSTS}
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    artifact_manifest = None
    if args.dist:
        manifest = args.dist.resolve() / "release-manifest.json"
        artifact_manifest = {
            "path": manifest.name,
            "sha256": _sha256(manifest),
            "core_wheel_sha256": next(
                item["sha256"]
                for item in json.loads(manifest.read_text(encoding="utf-8"))["files"]
                if item["filename"] == f"brief_spec-{_version()}-py3-none-any.whl"
            ),
        }
    value = {
        "schema_version": "1.0",
        "kind": "brief-spec-live-e2e-evidence",
        "version": _version(),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_revision_base": revision,
        "source_state": "dirty-worktree-candidate",
        "source_worktree_fingerprint": source_fingerprint(),
        "required_hosts": list(REQUIRED_HOSTS),
        "status": (
            "authorized"
            if all(record["status"] == "PASS" for record in hosts.values())
            else "blocked"
        ),
        "hosts": hosts,
        "candidate_artifacts": artifact_manifest,
        "privacy": (
            "Sanitized decisions, hashes, and opaque-reference hashes only; no credentials or "
            "raw transcripts."
        ),
    }
    _validate(value, require_authorized=args.require_authorized)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
