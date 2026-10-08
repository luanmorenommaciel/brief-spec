#!/usr/bin/env python3
"""Run bounded Brief-Spec acceptance scenarios in disposable live host CLIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.bundle import build_delivery_bundle, deliver_bundle
from briefspec.config import briefspec_home
from briefspec.delivery import export_core, load_delivery
from briefspec.markdown import TYPED_PATTERN, parse_typed
from briefspec.models import Runtime, VerificationLevel
from briefspec.state import list_sessions, session_path
from briefspec.verification import verify_target
from briefspec.work_types import type_profile

MODES = ("outcome", "orient", "teach", "spoken")
WORK_TYPES = (
    "general",
    "exploration",
    "review",
    "implementation",
    "debugging",
    "planning",
    "research",
    "operations",
)
HOSTS = ("codex", "claude", "omp", "grok", "kimi")
CORE_HOSTS = ("codex", "claude")
SECONDARY_HOSTS = ("omp", "grok", "kimi")

_TASKS = {
    "general": (
        "general",
        "Brief-Spec is requested. Read evidence.txt and answer what single fact it establishes and "
        "why that answer is trustworthy. Do not change any file.",
    ),
    "exploration": (
        "codebase",
        "Explore and map how this codebase repository is structured. Trace its entry points and "
        "flow using evidence.txt, identify unknowns, and name the next probe. Do not change files.",
    ),
    "review": (
        "pull-request",
        "Review pull request #42 using evidence.txt. Inspect its scope and correctness, give a "
        "verdict, identify findings and risk, and recommend the next move. Do not change files.",
    ),
    "implementation": (
        "feature",
        "Implement the feature by changing feature.txt from disabled to enabled, then verify the "
        "result. Only feature.txt may be changed in this disposable fixture.",
    ),
    "debugging": (
        "bug",
        "Debug the failing bug where the feature remains disabled although enabled is expected. "
        "Inspect feature.txt and evidence.txt, diagnose the root cause, explain the fix and "
        "regression protection, and retain residual risk. Do not change files.",
    ),
    "planning": (
        "architecture",
        "Plan the architecture implementation using evidence.txt. Define phases, decisions, "
        "acceptance criteria, and release gates. Do not change files.",
    ),
    "research": (
        "dependency",
        "Research the latest dependency tools represented by evidence.txt. Compare and evaluate "
        "the evidence quality, limitations, and recommendation without network access or changes.",
    ),
    "operations": (
        "incident",
        "Assess the incident outage described by evidence.txt. Explain impact, current state, "
        "recovery actions, rollback posture, and follow-up. Do not change files.",
    ),
}

_PRESENTATION = {
    "general": "outcome",
    "exploration": "orient",
    "review": "teach",
    "implementation": "spoken",
    "debugging": "outcome",
    "planning": "orient",
    "research": "teach",
    "operations": "spoken",
}

_SECONDARY_TYPES = ("review", "exploration", "implementation", "debugging")
_TYPED_MARKER_GRAMMAR = (
    "<!-- brief-spec:typed:v1 type={value} subject={value} confidence={value} origin={value} "
    "classified_at={value} profile=1.0 decision_id={value} -->"
)


def _prompt(host: str, work_type: str, mode: str) -> str:
    _, task = _TASKS[work_type]
    proof_path = "feature.txt" if work_type == "implementation" else "evidence.txt"
    routing = (
        "The installed native Brief-Spec hook owns classification. Do not run the classifier or "
        "inspect Brief-Spec skill/reference files. If the first response lacks authoritative "
        "metadata, follow the native Stop hook's single correction exactly."
        if host == "grok"
        else "Follow the automatic classification supplied by the installed Brief-Spec hook."
    )
    handshake = (
        "GROK NATIVE HOOK HANDSHAKE: Before any tool call or task answer, your first response "
        "must be exactly BRIEF_SPEC_METADATA_PENDING. This sentinel is the sole permitted "
        "pre-boundary response. The native Stop hook will block it and supply the "
        "authoritative classification. Only after receiving that feedback may you use the named "
        "fixture tools and return the complete typed region.\n\n"
        if host == "grok"
        else ""
    )
    boundary = {
        "outcome": (
            "Close with `<!-- briefspec:outcome:v1 -->`, then the Outcome fields Status, Outcome, "
            "Human action, Proof, Gaps, Next, and Open in that order, then `<!-- /briefspec -->`. "
            "The inner closer is exactly the generic `<!-- /briefspec -->`, never "
            "`<!-- /briefspec:outcome:v1 -->`. Use Status DONE and set Human action, Gaps, Next, "
            "and Open to exactly None."
        ),
        "orient": (
            "Close with `<!-- briefspec:checkpoint:v1 mode=orient -->`, then Mode: orient and the "
            "fields Headline, Current state, Completed, Decisions, Proof, Next, and Open in that "
            "order, then `<!-- /briefspec -->`. Do not use an Outcome Brief."
        ),
        "teach": (
            "Close with `<!-- briefspec:checkpoint:v1 mode=teach -->`, then Mode: teach and the "
            "fields Headline, Mental model, Why it matters, What changed, Example, Watch-outs, "
            "Next, and Proof in that order, then `<!-- /briefspec -->`. Do not use an Outcome "
            "Brief."
        ),
        "spoken": (
            "Close with `<!-- briefspec:checkpoint:v1 mode=spoken -->`, then Mode: spoken and the "
            "fields Headline, Script, Screen-only proof, and Next in that order, then "
            "`<!-- /briefspec -->`. Include a concrete Next and a natural 100-to-140-word Script "
            "that does not speak file paths. Count the Script words before returning; if it has "
            "fewer than 100 or more than 140, revise it. Do not use an Outcome Brief."
        ),
    }[mode]
    return f"""{handshake}{task}

{routing} Use exactly one complete typed region and copy its real classification metadata without
inventing it. The outer opening marker is exactly one line with this grammar:
`{_TYPED_MARKER_GRAMMAR}`.
Replace every braced value with the hook decision. Copy work type and subject character-for-
character; never expand or reinterpret the subject. After it, use every selected-profile heading
as `### Label` once in order. Every H3 must be immediately followed by its non-empty content before
the next H3. {boundary} Keep at least one `[direct/pass]` file proof plus explicit gaps. The legacy
Proof or Screen-only proof field itself must begin with the literal locator
`[direct/pass] [file]({proof_path})`; a description may follow that locator, but do not report
command evidence. Return only the typed region. The first output
characters must be `<!-- brief-spec:typed:v1` and the literal last line must be
`<!-- /brief-spec -->`; the preceding line must be `<!-- /briefspec -->`. Prose, YAML-style
metadata, H2 headings, bold legacy field labels, or a checkpoint outside those HTML comments is
invalid. Opening and closing delimiter strings are reserved and must never appear inside section
prose. Every legacy field is plain `Field: value`. The origin token must be exactly one of explicit,
host, inferred, or fallback; it is never the word hook. Do not use network access, URLs, web search,
external connectors, another model, raw transcripts, or secrets."""


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 300,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(env_overrides or {})
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=environment,
    )


def _redact(value: str) -> str:
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED_API_KEY]", value)
    return re.sub(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)\S+", r"\1[REDACTED]", value)


def _stderr_evidence(value: str) -> str:
    redacted = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", _redact(value))
    selected = [
        line
        for line in redacted.splitlines()
        if any(token in line.lower() for token in ("brief-spec", "error", "max turns"))
    ][-20:]
    record = {
        "sha256": hashlib.sha256(redacted.encode()).hexdigest(),
        "line_count": len(redacted.splitlines()),
        "selected": selected,
    }
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def _sanitized_events(stream: str) -> str:
    """Retain acceptance evidence without signatures, connector inventories, or credentials."""
    retained: list[str] = []
    for line in stream.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = value.get("type")
        subtype = value.get("subtype")
        record: dict[str, Any] = {"type": event_type}
        if subtype:
            record["subtype"] = subtype
        for name in (
            "thread_id",
            "session_id",
            "hook_name",
            "hook_event",
            "outcome",
            "exit_code",
            "terminal_reason",
            "total_cost_usd",
            "is_error",
        ):
            if value.get(name) is not None:
                record[name] = value[name]
        message = value.get("message")
        if isinstance(message, dict):
            if isinstance(message.get("model"), str):
                record["model"] = message["model"]
            content = message.get("content", [])
            texts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if texts:
                assistant = "\n".join(texts)
                record["assistant_chars"] = len(assistant)
                record["assistant_sha256"] = hashlib.sha256(assistant.encode()).hexdigest()
            tools = [
                item.get("name", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "tool_use"
            ]
            if tools:
                record["tool_uses"] = tools
        item = value.get("item")
        if isinstance(item, dict):
            record["item_type"] = item.get("type")
            if item.get("type") == "agent_message":
                assistant = str(item.get("text", ""))
                record["assistant_chars"] = len(assistant)
                record["assistant_sha256"] = hashlib.sha256(assistant.encode()).hexdigest()
            if item.get("type") == "command_execution":
                command = str(item.get("command", ""))
                record["command_sha256"] = hashlib.sha256(command.encode()).hexdigest()
                record["command_status"] = item.get("status", "")
        retained.append(json.dumps(record, sort_keys=True))
    return "\n".join(retained) + ("\n" if retained else "")


def _extract_claude(stream: str) -> str:
    result_text = ""
    assistant_text = ""
    for line in stream.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("type") == "result" and isinstance(value.get("result"), str):
            result_text = value["result"]
        message = value.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content", [])
            pieces = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if pieces:
                assistant_text = "\n".join(pieces)
    return result_text or assistant_text


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _all_strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _all_strings(child)]
    return []


def _extract_typed(stream: str) -> str:
    candidates: list[str] = []
    for line in stream.splitlines():
        try:
            candidates.extend(_all_strings(json.loads(line)))
        except json.JSONDecodeError:
            continue
    candidates.extend(_all_strings(_json_document(stream)))
    bounded = [
        value.strip()
        for value in candidates
        if value.strip().startswith("<!-- brief-spec:typed:v1")
        and value.strip().endswith("<!-- /brief-spec -->")
        and ("<!-- briefspec:outcome:v1 -->" in value or "<!-- briefspec:checkpoint:v1" in value)
    ]
    if bounded:
        return max(bounded, key=len)
    marker = stream.find("<!-- brief-spec:typed:v1")
    end = stream.find("<!-- /brief-spec -->", marker)
    if marker >= 0 and end >= 0:
        return stream[marker : end + len("<!-- /brief-spec -->")]
    return ""


def _extract_grok(stream: str) -> str:
    """Reassemble Grok's streamed text deltas without selecting skill examples."""
    groups: list[str] = []
    current: list[str] = []
    for line in stream.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("type") == "text" and isinstance(value.get("data"), str):
            current.append(value["data"])
            continue
        if value.get("type") in {"usage", "tool_call", "end"} and current:
            groups.append("".join(current))
            current = []
    if current:
        groups.append("".join(current))
    bounded = [
        group.strip()
        for group in groups
        if group.strip().startswith("<!-- brief-spec:typed:v1")
        and group.strip().endswith("<!-- /brief-spec -->")
        and ("<!-- briefspec:outcome:v1 -->" in group or "<!-- briefspec:checkpoint:v1" in group)
    ]
    return bounded[-1] if bounded else _extract_typed(stream)


def _json_document(stream: str) -> Any:
    try:
        return json.loads(stream)
    except json.JSONDecodeError:
        return None


def _session_refs(stream: str) -> list[str]:
    found: set[str] = set()
    for line in stream.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        stack: list[Any] = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, child in current.items():
                    if key in {"session_id", "thread_id", "sessionId", "threadId"} and child:
                        found.add(str(child))
                    else:
                        stack.append(child)
            elif isinstance(current, list):
                stack.extend(current)
    return sorted(found)


def _stream_values(stream: str) -> tuple[list[str], float | None]:
    models: set[str] = set()
    cost: float | None = None
    for line in stream.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        model = value.get("model")
        if isinstance(model, str) and model:
            models.add(model)
        message = value.get("message")
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            models.add(message["model"])
        if isinstance(value.get("total_cost_usd"), (int, float)):
            cost = float(value["total_cost_usd"])
        model_usage = value.get("modelUsage")
        if isinstance(model_usage, dict):
            models.update(str(name) for name in model_usage if name)
    return sorted(models), cost


def _authorized_workspace_changes(workspace: Path, work_type: str, worktree: str) -> bool:
    if work_type != "implementation":
        return not worktree.strip()
    feature = workspace / "feature.txt"
    return (
        worktree == " M feature.txt\n"
        and feature.is_file()
        and feature.read_text(encoding="utf-8").splitlines() == ["feature flag: enabled"]
    )


def _state_snapshot(host: str) -> dict[str, str]:
    return {
        str(value.get("session_id")): str(value.get("updated_at"))
        for value in list_sessions()
        if value.get("runtime") == host and value.get("session_id")
    }


def _host_command(host: str, work_type: str, prompt: str, final_path: Path) -> list[str]:
    implementation = work_type == "implementation"
    if host == "codex":
        return [
            "codex",
            "exec",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "workspace-write" if implementation else "read-only",
            "--dangerously-bypass-hook-trust",
            "--output-last-message",
            str(final_path),
            prompt,
        ]
    if host == "claude":
        return [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-hook-events",
            "--no-session-persistence",
            "--permission-mode",
            "acceptEdits" if implementation else "dontAsk",
            "--tools",
            "Read,Write" if implementation else "Read",
            "--max-budget-usd",
            "0.5",
            "--model",
            "sonnet",
            "--strict-mcp-config",
            '--mcp-config={"mcpServers":{}}',
            prompt,
        ]
    if host == "omp":
        command = [
            "omp",
            "-p",
            "--mode",
            "json",
            "--no-session",
            "--tools",
            "read,write" if implementation else "read",
            "--max-time",
            "5m",
        ]
        if implementation:
            command.extend(("--approval-mode", "write"))
        return [*command, prompt]
    if host == "grok":
        file_policy = {
            "implementation": (
                "Use read_file for feature.txt, use search_replace once to change only "
                "feature.txt as requested, then use read_file once more to verify it. Do not "
                "touch any other file."
            ),
            "debugging": (
                "Read evidence.txt and feature.txt at most once each and do not modify files."
            ),
        }.get(
            work_type,
            "Read evidence.txt at most once and do not modify files.",
        )
        return [
            "grok",
            "-p",
            prompt,
            "--output-format",
            "streaming-json",
            "--permission-mode",
            "bypassPermissions" if implementation else "plan",
            "--model",
            "grok-4.5",
            "--system-prompt-override",
            (
                "Follow the user prompt exactly. The complete Brief-Spec type profile and boundary "
                "contract are supplied in the prompt. If Grok automatically invokes the installed "
                "Brief-Spec skill, follow it once and load at most the one matching profile; do "
                "not search directories, inspect executables, or run the classifier. The native "
                "hook owns classification. Before Stop feedback, its metadata is intentionally "
                "unavailable: it does not exist in the workspace, so never search for it. Before "
                "any tool call, the first text completion MUST be exactly "
                "BRIEF_SPEC_METADATA_PENDING. The native Stop hook will then supply one "
                "correction. After that correction, copy its marker exactly, perform the "
                "authorized fixture "
                f"work, and return only the requested bounded Markdown. {file_policy} Do not call "
                "any other tool."
            ),
            "--tools",
            "read_file,search_replace" if implementation else "read_file",
            "--disable-web-search",
            "--no-subagents",
            "--no-memory",
            "--max-turns",
            "16",
        ]
    return ["kimi", "-p", prompt, "--output-format", "stream-json"]


def _host_version(host: str, workspace: Path) -> str:
    command = [host, "--version"]
    result = _run(command, cwd=workspace, timeout=30)
    return (result.stdout or result.stderr).strip().splitlines()[0]


def _host_environment(host: str, workspace: Path) -> dict[str, str]:
    if host != "grok":
        return {}
    isolated_home = workspace / ".host-home"
    exclude = workspace / ".git" / "info" / "exclude"
    exclusions = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if ".host-home/" not in exclusions.splitlines():
        exclude.write_text(exclusions.rstrip() + "\n.host-home/\n", encoding="utf-8")
    grok_home = isolated_home / ".grok"
    source_home = Path.home() / ".grok"
    grok_home.mkdir(parents=True, mode=0o700)
    for skill in ("brief-spec", "outcome-brief", "session-checkpoint"):
        shutil.copytree(source_home / "skills" / skill, grok_home / "skills" / skill)
    shutil.copytree(source_home / "brief-spec", grok_home / "brief-spec")
    (grok_home / "hooks").mkdir(parents=True)
    shutil.copy2(source_home / "hooks" / "brief-spec.json", grok_home / "hooks")
    (grok_home / "config.toml").write_text(
        """[compat.claude]
skills = false
rules = false
agents = false
mcps = false
hooks = false
sessions = false

[compat.cursor]
skills = false
rules = false
agents = false
mcps = false
hooks = false
sessions = false

[marketplace]
official_marketplace_auto_installed = false

[workflows]
enabled = false

[memory]
enabled = false
""",
        encoding="utf-8",
    )
    # Grok 1.0.46 resolves its signed-in session through the real HOME; isolating HOME makes it
    # exit before any model call. GROK_HOME alone keeps skills, hooks, and config isolated.
    return {
        "GROK_HOME": str(grok_home),
        "GROK_AUTH_PATH": str(source_home / "auth.json"),
        "BRIEF_SPEC_HOME": str(briefspec_home()),
    }


def _prepare_repository(root: Path, work_type: str = "general") -> None:
    evidence = {
        "general": (
            "Verified fact: canonical delivery is ready for local verification.\n"
            "Basis: this fixture is the direct source supplied to the read-only task.\n"
        ),
        "exploration": (
            "Repository map: evidence.txt records requirements; feature.txt is the only runtime "
            "state entry point.\nFlow: a reader inspects evidence.txt, then feature.txt, then "
            "reports unknown external integrations as unresolved.\n"
        ),
        "review": (
            "Pull request #42 scope: change feature.txt from 'feature flag: disabled' to "
            "'feature flag: enabled'.\nValidation: the proposed value matches the stated target; "
            "no other files are in scope. Risk: downstream integration behavior is not represented "
            "in this fixture and remains an explicit gap.\n"
        ),
        "implementation": (
            "Feature requirement: feature.txt must contain exactly 'feature flag: enabled'.\n"
            "Authorization: only feature.txt may change.\n"
        ),
        "debugging": (
            "Bug report: feature.txt is expected to say 'feature flag: enabled' but currently says "
            "'feature flag: disabled'.\nRoot-cause boundary: the stale literal is the only modeled "
            "cause; external integrations are outside this fixture.\n"
        ),
        "planning": (
            "Architecture constraint: keep the core dependency-free and optional renderers "
            "version-aligned.\nGate order: schema, deterministic build, clean-room install, live "
            "host verification, then publication authorization.\n"
        ),
        "research": (
            "Dependency evidence snapshot: core has zero runtime dependencies; PDF and audio are "
            "optional packages.\nLimitation: network research is disabled, so recency claims "
            "remain unresolved and recommendations must stay bounded to this snapshot.\n"
        ),
        "operations": (
            "Incident: delivery verification is unavailable while the release gate is blocked.\n"
            "Impact: publication cannot proceed; local artifacts remain intact. Recovery: restore "
            "the gate, rerun verification, and preserve rollback evidence.\n"
        ),
    }[work_type]
    (root / "evidence.txt").write_text(evidence, encoding="utf-8")
    (root / "feature.txt").write_text("feature flag: disabled\n", encoding="utf-8")
    commands = (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "briefspec@example.invalid"],
        ["git", "config", "user.name", "Brief-Spec E2E"],
        ["git", "add", "evidence.txt", "feature.txt"],
        ["git", "commit", "-qm", "test: add acceptance fixture"],
    )
    for command in commands:
        result = _run(command, cwd=root, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed: {' '.join(command)}")


def run_scenario(
    host: str,
    work_type: str,
    mode: str,
    output_root: Path,
) -> dict[str, Any]:
    expected_subject, _ = _TASKS[work_type]
    scenario = output_root / host / f"{work_type}-{mode}"
    scenario.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"brief-spec-{host}-{work_type}-{mode}-") as temporary:
        workspace = Path(temporary)
        _prepare_repository(workspace, work_type)
        final_path = scenario / "final.md"
        state_before = _state_snapshot(host)
        result = _run(
            _host_command(host, work_type, _prompt(host, work_type, mode), final_path),
            cwd=workspace,
            env_overrides=_host_environment(host, workspace),
        )
        stream = _redact(result.stdout)
        error = _redact(result.stderr)
        (scenario / "events.jsonl").write_text(_sanitized_events(stream), encoding="utf-8")
        (scenario / "stderr.json").write_text(_stderr_evidence(error), encoding="utf-8")
        if result.returncode != 0:
            error_tail = "\n".join(error.splitlines()[-20:])
            raise RuntimeError(
                f"{host}/{work_type}/{mode} failed: {error_tail or result.returncode}"
            )
        if host == "claude":
            final_path.write_text(_extract_claude(stream), encoding="utf-8")
        elif host == "grok":
            final_path.write_text(_extract_grok(stream), encoding="utf-8")
        elif host != "codex":
            final_path.write_text(_extract_typed(stream), encoding="utf-8")
        if not final_path.is_file():
            raise RuntimeError(f"{host}/{work_type}/{mode} produced no final output")
        final = final_path.read_text(encoding="utf-8")
        if not final.strip():
            raise RuntimeError(f"{host}/{work_type}/{mode} produced an empty final output")
        session_refs = _session_refs(stream)
        state_after = _state_snapshot(host)
        changed_sessions = sorted(
            session_id
            for session_id, updated_at in state_after.items()
            if state_before.get(session_id) != updated_at
        )
        session_refs = sorted(set(session_refs) | set(changed_sessions))
        hook_state_paths = [session_path(Runtime(host), ref) for ref in session_refs]
        parsed_typed = parse_typed(final)
        if parsed_typed is None:
            raise ValueError(
                f"{host}/{work_type}/{mode} did not emit a complete brief-spec:typed:v1 region"
            )
        reported_classification, _ = parsed_typed
        marker_match = TYPED_PATTERN.search(final)
        if marker_match is None:
            raise ValueError("live output is missing the typed marker")
        marker = {
            "work_type": marker_match.group("work_type"),
            "subject": marker_match.group("subject"),
            "confidence": marker_match.group("confidence"),
            "origin": marker_match.group("origin"),
            "classified_at": marker_match.group("classified_at"),
            "decision_id": marker_match.group("decision_id"),
        }
        hook_states = [
            json.loads(state_file.read_text(encoding="utf-8"))
            for state_file in hook_state_paths
            if state_file.is_file()
        ]
        hook_state = next(
            (
                state
                for state in hook_states
                if state.get("classification_decision_id") == marker.get("decision_id")
            ),
            None,
        )
        models, cost_usd = _stream_values(stream)
        delivery, warnings = load_delivery(
            final,
            source_path=final_path,
            runtime=host,
            session_ref=(session_refs or [None])[0],
            host_version=_host_version(host, workspace),
            source_revision=_run(
                ["git", "rev-parse", "HEAD"], cwd=workspace, timeout=30
            ).stdout.strip(),
            model=(models or [None])[0],
            created_at="2026-08-11T12:00:00Z",
        )
        formats = ["markdown", "json", "html"]
        if mode == "spoken":
            formats.extend(("spoken-text", "ssml"))
        exports = scenario / "exports"
        export_core(delivery, formats, exports, force=True)
        bundle_path = scenario / "delivery.zip"
        build_delivery_bundle(delivery, bundle_path, formats=formats, force=True)
        rendered = verify_target(
            bundle_path,
            level=VerificationLevel.RENDERED,
            workspace=workspace,
        )
        resolved = verify_target(
            exports / "brief.json",
            level=VerificationLevel.RESOLVED,
            workspace=workspace,
        )
        delivered = deliver_bundle(bundle_path, scenario / "delivered", force=True)
        delivered_result = verify_target(
            Path(delivered["receipt"]),
            level=VerificationLevel.DELIVERED,
            workspace=workspace,
        )
        worktree = _run(["git", "status", "--porcelain"], cwd=workspace, timeout=30).stdout
        authorized_changes = _authorized_workspace_changes(workspace, work_type, worktree)
        strict = _run(
            ["brief-spec", "validate", str(final_path), "--strict", "--json"],
            cwd=workspace,
            timeout=30,
        )
        expected_kind = "outcome-brief" if mode == "outcome" else "session-checkpoint"
        actual_brief = delivery.get("brief", {})
        mode_matches = actual_brief.get("kind") == expected_kind and (
            mode == "outcome" or actual_brief.get("mode") == mode
        )
        classification = delivery.get("classification", {})
        if classification != reported_classification:
            raise ValueError("delivery classification differs from the parsed typed marker")
        explanation = delivery.get("explanation", {})
        expected_sections = [section.section_id for section in type_profile(work_type).sections]
        # Classifier 1.3 has positive general rules ("answer what…"), so a general request may be
        # inferred at medium confidence as well as reached by fallback at low confidence.
        expected_pairs = (
            {("fallback", "low"), ("inferred", "medium")}
            if work_type == "general"
            else {("inferred", "medium")}
        )
        hook_classification = (
            {
                "work_type": hook_state.get("work_type"),
                "subject": hook_state.get("subject"),
                "confidence": hook_state.get("classification_confidence"),
                "origin": hook_state.get("classification_origin"),
                "classified_at": hook_state.get("classified_at"),
                "decision_id": hook_state.get("classification_decision_id"),
            }
            if hook_state
            else {}
        )
        classification_matches = (
            hook_classification.get("work_type") == work_type
            and hook_classification.get("subject") == expected_subject
            and (hook_classification.get("origin"), hook_classification.get("confidence"))
            in expected_pairs
            and all(
                marker.get(name) == hook_classification.get(name) for name in hook_classification
            )
            and classification.get("work_type") == work_type
            and classification.get("subject") == expected_subject
            and classification.get("origin") == "reported"
            and classification.get("confidence") == "low"
            and [item.get("id") for item in explanation.get("sections", [])] == expected_sections
        )
        hook_observed = "briefspec" in stream.lower() or any(
            state_file.is_file() for state_file in hook_state_paths
        )
        passed = (
            strict.returncode == 0
            and rendered["status"] == "PASS"
            and resolved["status"] in {"PASS", "WARN"}
            and delivered_result["status"] == "PASS"
            and authorized_changes
            and not warnings
            and mode_matches
            and classification_matches
            and hook_observed
            and (cost_usd is None or cost_usd <= 1)
        )
        record = {
            "host": host,
            "work_type": work_type,
            "subject": expected_subject,
            "mode": mode,
            "status": "PASS" if passed else "FAIL",
            "host_returncode": result.returncode,
            "session_refs": session_refs,
            "models": models,
            "cost_usd": cost_usd,
            "hook_observed": hook_observed,
            "hook_state_paths": [str(path) for path in hook_state_paths if path.is_file()],
            "mode_matches": mode_matches,
            "classification_matches": classification_matches,
            "classification": classification,
            "hook_classification": hook_classification,
            "strict_validation": {
                "status": "PASS" if strict.returncode == 0 else "FAIL",
                "returncode": strict.returncode,
                "output": _redact(strict.stdout or strict.stderr),
            },
            "warnings": warnings,
            "rendered": rendered,
            "resolved": resolved,
            "delivered": delivered_result,
            "authorized_changes_only": authorized_changes,
            "worktree_status": worktree,
        }
        (scenario / "result.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return record


def collect_results(output: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(output.glob("*/*/result.json")):
        if ".attempt-" in path.parent.name:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            results.append(value)
    return results


def _archive_failed_attempt(
    output: Path, host: str, work_type: str, mode: str, attempt: int
) -> None:
    scenario = output / host / f"{work_type}-{mode}"
    if not scenario.exists():
        return
    archive = scenario.with_name(f"{scenario.name}.attempt-{attempt}")
    suffix = 1
    while archive.exists():
        suffix += 1
        archive = scenario.with_name(f"{scenario.name}.attempt-{attempt}-{suffix}")
    scenario.rename(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=[*HOSTS, "all"], default="all")
    parser.add_argument("--type", choices=[*WORK_TYPES, "all"], default="all")
    parser.add_argument("--mode", choices=[*MODES, "matrix", "all"], default="matrix")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--max-attempts", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or Path(".briefspec") / "live-e2e" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=True)
    hosts = HOSTS if args.host == "all" else (args.host,)
    expected: list[tuple[str, str, str]] = []
    for host in hosts:
        if args.type == "all":
            types = WORK_TYPES if host in CORE_HOSTS else _SECONDARY_TYPES
        else:
            types = (args.type,)
        for work_type in types:
            modes = MODES if args.mode == "all" else (_PRESENTATION[work_type],)
            if args.mode not in {"all", "matrix"}:
                modes = (args.mode,)
            expected.extend((host, work_type, mode) for mode in modes)
    failures: list[dict[str, str]] = []
    attempt_history: list[dict[str, Any]] = []
    if not args.collect_only:
        for host, work_type, mode in expected:
            for attempt in range(1, args.max_attempts + 1):
                try:
                    record = run_scenario(host, work_type, mode, output)
                    error = None if record["status"] == "PASS" else "scenario result status FAIL"
                    cost_usd = record.get("cost_usd")
                except Exception as exc:
                    record = None
                    error = _redact(str(exc))
                    events = output / host / f"{work_type}-{mode}" / "events.jsonl"
                    cost_usd = (
                        _stream_values(events.read_text(encoding="utf-8"))[1]
                        if events.is_file()
                        else None
                    )
                attempt_history.append(
                    {
                        "host": host,
                        "work_type": work_type,
                        "mode": mode,
                        "attempt": attempt,
                        "status": "PASS" if error is None else "FAIL",
                        "cost_usd": cost_usd,
                        **({"error": error} if error else {}),
                    }
                )
                if error is None:
                    break
                if attempt < args.max_attempts:
                    _archive_failed_attempt(output, host, work_type, mode, attempt)
                    continue
                failure = {
                    "host": host,
                    "work_type": work_type,
                    "mode": mode,
                    "error": error,
                }
                failures.append(failure)
                print(json.dumps(failure, sort_keys=True), file=os.sys.stderr)
    expected_set = set(expected)
    results = [
        result
        for result in collect_results(output)
        if (result.get("host"), result.get("work_type"), result.get("mode")) in expected_set
    ]
    summary = {
        "schema_version": 2,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "output": str(output),
        "expected_scenarios": len(expected),
        "completed_scenarios": len(results),
        "execution_failures": failures,
        "attempt_history": attempt_history,
        "results": results,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    incomplete = not args.collect_only and len(results) != len(expected)
    return (
        1 if failures or incomplete or any(result["status"] != "PASS" for result in results) else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
