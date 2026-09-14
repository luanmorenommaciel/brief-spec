---
id: T-20260914-briefspec-visible-html-integrity
title: "Bind visible HTML to valid canonical content"
status: done
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [src/briefspec/verification.py, tests/test_delivery.py]
creates_paths: []
source_note: "seamwise/legs/LEG-VISIBLE-HTML-INTEGRITY.md#T-20260914-briefspec-visible-html-integrity"
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
signed_off_by: repository-owner-via-explicit-user-approval
signed_off_at: 2026-09-14T13:30:22Z
accepted: true
accepted_by: luanmorenomaciel
accepted_at: 2026-09-14T14:11:48Z
signed_off_sig: hmac-sha256-v3:7c09a871:077fe8c9eff6592a80a0f54b197e64c0d86311fe81e61c985ee7e73e012bca65
accepted_tier: 1
accepted_attempt_id: 31d84311-959a-4fab-9bfb-c852506d344a
accepted_authorization_ref: hmac-sha256-v3:7c09a871:077fe8c9eff6592a80a0f54b197e64c0d86311fe81e61c985ee7e73e012bca65
acceptance_record_digest: sha256:2ab177799e6a3f6df8da0ab4d51d5e627b6e7bc9d678f68b568eaecc0653bb5c
---

# Bind visible HTML to valid canonical content

> **Why:** Validate canonical content embedded in standalone HTML and verify its visible deterministic projection as rigorously as core bundles, without changing rendering or relaxing offline controls.

## Goal

Validate canonical content embedded in standalone HTML and verify its visible deterministic projection as rigorously as core bundles, without changing rendering or relaxing offline controls.

## Context

Intent INTENT-BRIEFSPEC-REPAIR-WAVE; seam SEAM-VISIBLE-HTML-INTEGRITY; swimlane LANE-VISIBLE-HTML-INTEGRITY; capability leg LEG-VISIBLE-HTML-INTEGRITY. Done condition: Visible outcome or other report content mutations fail rendered verification even when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.

## Behavior

- **B-1** — GIVEN A valid supported input and its corresponding prior reproduced defect WHEN Validate canonical content embedded in standalone HTML and verify its visible deterministic projection as rigorously as core bundles, without changing rendering or relaxing offline controls. THEN Visible outcome or other report content mutations fail rendered verification even when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.
- **B-2** — GIVEN A valid ordinary input or an adversarial variant at the same public boundary WHEN The consumer exercises the corrected public behavior THEN Ordinary behavior remains supported and the invalid or contradictory variant cannot silently pass or acquire stronger authority.

## Success Criteria

```bash
# eval_1: Exercise the standalone html visible tamper observable contract.
eval_1() {
  uv run --no-sync pytest -q tests/test_delivery.py -k standalone_html_visible_tamper
}

# eval_2: Exercise the standalone html invalid canonical observable contract.
eval_2() {
  uv run --no-sync pytest -q tests/test_delivery.py -k standalone_html_invalid_canonical
}

# eval_3: Exercise the standalone html valid projection observable contract.
eval_3() {
  uv run --no-sync pytest -q tests/test_delivery.py -k standalone_html_valid_projection
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "Exercise the standalone html visible tamper observable contract."
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "Exercise the standalone html invalid canonical observable contract."
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "Exercise the standalone html valid projection observable contract."
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

Add deterministic behavioral regressions to the existing declared test file. Do not assert source text or mock echoes. Skip formatters, linters and all tests while sibling writers are in flight; the supervising parent independently runs the declared evaluations and final suite. Do not change tests merely to preserve implementation details. Read the exact source path; keep changes only in signed scope.

## Anti-Patterns

- Do not Treat a structurally valid record or model assertion as authorization or measured success: Brief-Spec explains observations; Task-Spec and humans retain authorization and acceptance authority.; instead Preserve explicit basis and require source-bound receipts for stronger claims..
- Do not Overwrite unrelated user changes or widen the signed write surface: Task-Spec authorization is bounded and receipt ownership must preserve user work.; instead Keep edits inside declared paths, serialize shared mutations and fail closed on conflicts..
- Do not Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants or stale summaries: Existence and apparent formatting do not prove the observable behavior.; instead Run the specified behavioral scenario on exact candidate inputs and retain truthful failure or blocked evidence..

## Do-Not-Touch

- `OPERATING.md`
- `AGENTS.md`
- `CLAUDE.md`
- `unrelated product source or tests`
- `global host installations and user branch history`

## Open Questions

(none — this task is fully specified)
