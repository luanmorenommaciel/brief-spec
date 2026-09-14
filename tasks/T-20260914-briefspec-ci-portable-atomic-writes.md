---
id: T-20260914-briefspec-ci-portable-atomic-writes
title: "Preserve atomic writes on supported Windows Python versions"
status: ready
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [src/briefspec/state.py, tests/test_state_and_hooks.py]
creates_paths: []
source_note: "seamwise/legs/LEG-CI-PORTABLE-ATOMIC-WRITES.md#T-20260914-briefspec-ci-portable-atomic-writes"
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
signed_off_at: 2026-09-14T20:04:44Z
accepted: false
accepted_by: (none)
accepted_at: (none)
signed_off_sig: hmac-sha256-v3:7c09a871:86d4f75be5e411e29dd510ed42aa40aae0d1679d4df3abd701e7225a204103b1
---

# Preserve atomic writes on supported Windows Python versions

> **Why:** Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure cleanup.

## Goal

Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure cleanup.

## Context

Intent INTENT-BRIEFSPEC-CI-REPAIRS; seam SEAM-CI-PORTABLE-ATOMIC-WRITES; swimlane LANE-CI-PORTABLE-ATOMIC-WRITES; capability leg LEG-CI-PORTABLE-ATOMIC-WRITES. Done condition: Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure cleanup.

## Behavior

- **B-1** — GIVEN os.fchmod is unavailable WHEN private state is saved and reloaded THEN The state round-trips without weakening supported-platform permission handling.
- **B-2** — GIVEN permission assignment fails for an existing destination WHEN the atomic write fails THEN The original destination survives, the temporary descriptor is closed, the temporary file is removed, and the original permission error remains visible.

## Success Criteria

```bash
# eval_1: portable state round-trip
eval_1() {
  uv run --no-sync pytest -q tests/test_state_and_hooks.py -k "without_fchmod or state_files_are_private"
}

# eval_2: permission failure ownership
eval_2() {
  uv run --no-sync pytest -q tests/test_state_and_hooks.py -k atomic_permission_failure
}

# eval_3: transaction rollback
eval_3() {
  uv run --no-sync pytest -q tests/test_delivery_edge_cases.py -k atomic
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "portable state round-trip"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "permission failure ownership"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "transaction rollback"
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

Keep descriptor-based chmod where available; use pathname chmod only when the platform lacks fchmod. Enter descriptor ownership before changing permissions. Do not suppress permission errors. Keep a discriminating round-trip and descriptor-cleanup regression.

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
