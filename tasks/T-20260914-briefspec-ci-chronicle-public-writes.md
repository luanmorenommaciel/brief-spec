---
id: T-20260914-briefspec-ci-chronicle-public-writes
title: "Reuse portable public atomic writes for Chronicle outputs"
status: ready
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [packages/brief-spec-chronicle/src/brief_spec_chronicle/storage.py, tests/test_chronicle.py]
creates_paths: []
source_note: "seamwise/legs/LEG-CI-CHRONICLE-PUBLIC-WRITES.md#T-20260914-briefspec-ci-chronicle-public-writes"
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
signed_off_by: repository-owner-via-explicit-chronicle-repair-approval
signed_off_at: 2026-09-14T21:23:37Z
accepted: false
accepted_by: (none)
accepted_at: (none)
signed_off_sig: hmac-sha256-v3:7c09a871:25438d01f1f1debd8662520e3b9b5eb1eeabbeeb4dcb37a0f05db67fe4540f0d
---

# Reuse portable public atomic writes for Chronicle outputs

> **Why:** Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite authorization, and atomic failure cleanup.

## Goal

Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite authorization, and atomic failure cleanup.

## Context

Intent INTENT-BRIEFSPEC-CI-CHRONICLE-WRITES; seam SEAM-CI-CHRONICLE-PUBLIC-WRITES; swimlane LANE-CI-CHRONICLE-PUBLIC-WRITES; capability leg LEG-CI-CHRONICLE-PUBLIC-WRITES. Done condition: Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite authorization, and atomic failure cleanup.

## Behavior

- **B-1** — GIVEN os.fchmod is unavailable and an output parent is public WHEN Chronicle creates and explicitly replaces an output THEN Both writes succeed without changing the parent mode; an unforced overwrite is rejected without changing the existing bytes.
- **B-2** — GIVEN the shared writer fails during permission assignment WHEN an atomic write fails THEN The original destination survives, the descriptor is closed, temporary output is removed, and the real failure remains visible.

## Success Criteria

```bash
# eval_1: Chronicle public output portability and overwrite boundary
eval_1() {
  uv run --no-sync pytest -q tests/test_chronicle.py -k external_write_without_fchmod
}

# eval_2: Shared atomic writer failure ownership
eval_2() {
  uv run --no-sync pytest -q tests/test_state_and_hooks.py -k atomic_permission_failure
}

# eval_3: Real Chronicle CLI and family journey outputs
eval_3() {
  uv run --no-sync pytest -q tests/test_chronicle_cli_and_video.py -k "chronicle_cli_end_to_end or disposable_seamwise_task_spec_converge_journey"
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "Chronicle public output portability and overwrite boundary"
    runnable: bash
    check_type: deterministic
    verifies: [B-1]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "Shared atomic writer failure ownership"
    runnable: bash
    check_type: deterministic
    verifies: [B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "Real Chronicle CLI and family journey outputs"
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

Retain the existing FileExistsError guard, call existing briefspec.state.atomic_write_public, remove the obsolete local temporary-file implementation and its unused import, and add a behavior-level regression covering missing fchmod, explicit overwrite, output bytes, and unchanged public parent mode. Do not introduce another writer or suppress failures.

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
