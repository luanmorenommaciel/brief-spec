---
id: T-20260914-briefspec-ci-portable-chronicle-zip
title: "Preserve deterministic Chronicle ZIP exports on Windows"
status: in-progress
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [packages/brief-spec-chronicle/src/brief_spec_chronicle/rendering.py]
creates_paths: []
source_note: "seamwise/legs/LEG-CI-PORTABLE-CHRONICLE-ZIP.md#T-20260914-briefspec-ci-portable-chronicle-zip"
created: "2026-09-14T00:00:00Z"
tags: []
owner: (none)
priority: P2
severity: feature
due_date: (none)
precondition: (none)
blocked_reason: (none)
security_class: (none)
source_action_item: (none)
tracker_ref: (none)
execution_backend: omp
signed_off: true
signed_off_by: repository-owner-via-explicit-ci-repair-approval
signed_off_at: 2026-09-14T20:04:51Z
accepted: false
accepted_by: (none)
accepted_at: (none)
signed_off_sig: hmac-sha256-v3:7c09a871:d0ad098c16c355bee71947690ed6070aedd40297914c26fc953829061b7a3896
---

# Preserve deterministic Chronicle ZIP exports on Windows

> **Why:** Create and read deterministic ZIP bytes through one owned temporary stream without reopening its live pathname.

## Goal

Create and read deterministic ZIP bytes through one owned temporary stream without reopening its live pathname.

## Context

Intent INTENT-BRIEFSPEC-CI-REPAIRS; seam SEAM-CI-PORTABLE-CHRONICLE-ZIP; swimlane LANE-CI-PORTABLE-CHRONICLE-ZIP; capability leg LEG-CI-PORTABLE-CHRONICLE-ZIP. Done condition: Create and read deterministic ZIP bytes through one owned temporary stream without reopening its live pathname.

## Behavior

- **B-1** — GIVEN canonical Chronicle files and created_at are unchanged WHEN ZIP exports are repeated THEN Archive contents, ordering, timestamps, modes, and resulting bytes remain deterministic.
- **B-2** — GIVEN the platform restricts reopening temporary-file names WHEN Chronicle creates or restores an archive THEN One owned temporary stream supplies completed ZIP bytes without a second pathname open.

## Success Criteria

```bash
# eval_1: deterministic project export
eval_1() {
  uv run --no-sync pytest -q tests/test_chronicle.py::test_project_lifecycle_snapshot_and_deterministic_exports
}

# eval_2: archive restore
eval_2() {
  uv run --no-sync pytest -q tests/test_chronicle.py::test_archive_restore_to_new_project_root
}

# eval_3: native Chronicle command path
eval_3() {
  uv run --no-sync pytest -q tests/test_chronicle_cli_and_video.py::test_chronicle_cli_end_to_end
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "deterministic project export"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "archive restore"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "native Chronicle command path"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: true
    expected_duration_sec: 10
retry_policy:
  max_iterations: 15
  circuit_breaker_no_progress: 3
  on_terminal_failure: park_with_context
agent_contract:
  version: 2
  read: [intent, behavior, contract, guardrails]
  produce: [code, tests]
  required_tools: [python, uv]
  timeout_minutes: 30
  sandbox_type: host
  output_artifacts: []
  mcp_dependencies: []
  emit: [pass, fail, retry_with_reason, parked_with_context]
  backend_metadata: {}
```

## Exit Check

```bash
eval_1 && eval_2 && eval_3
```

## Rollback Plan

Revert only the authorized source/test patch; preserve all other user files and generated authorization records.

## Observability Hooks

Use a portable temporary binary stream, pass the stream to ZipFile, finish its central directory, then seek and read the same stream. Preserve archive format and existing Windows regressions. Do not replace disk-backed staging with unnecessary whole-archive intermediate copies.

## Anti-Patterns

- Do not Treat a structurally valid record or model assertion as authorization or measured success: Brief-Spec explains observations; Task-Spec and humans retain authorization and acceptance authority.; instead Preserve explicit basis and require source-bound receipts for stronger claims..
- Do not Overwrite unrelated user changes or widen the signed write surface: Task-Spec authorization is bounded and receipt ownership must preserve user work.; instead Keep edits inside declared paths, serialize shared mutations and fail closed on conflicts..
- Do not Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants or stale summaries: Existence and apparent formatting do not prove the observable behavior.; instead Run the specified behavioral scenario on exact candidate inputs and retain truthful failure or blocked evidence..

## Do-Not-Touch

- `OPERATING.md`
- `AGENTS.md`
- `unrelated source or tests`
- `global host installations and user branch history`
- `CLAUDE.md`

## Open Questions

(none — this task is fully specified)
