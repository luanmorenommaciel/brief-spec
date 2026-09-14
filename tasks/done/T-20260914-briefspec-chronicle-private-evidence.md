---
id: T-20260914-briefspec-chronicle-private-evidence
title: "Preserve private evidence identity across Chronicle imports"
status: done
format_version: 3
profile: full
effort: M
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [packages/brief-spec-chronicle/src/brief_spec_chronicle/sources.py, packages/brief-spec-chronicle/src/brief_spec_chronicle/derive.py, tests/test_chronicle.py]
creates_paths: []
source_note: "seamwise/legs/LEG-CHRONICLE-PRIVATE-EVIDENCE.md#T-20260914-briefspec-chronicle-private-evidence"
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
signed_off_at: 2026-09-14T13:30:26Z
accepted: true
accepted_by: luanmorenomaciel
accepted_at: 2026-09-14T14:11:57Z
signed_off_sig: hmac-sha256-v3:7c09a871:aa4b860eed831ac9d708ee3715443c66d7dd990ebfb2eeae2545dab43738dd9b
accepted_tier: 1
accepted_attempt_id: 5441ce05-4260-416b-b5e8-bafe60c03d43
accepted_authorization_ref: hmac-sha256-v3:7c09a871:aa4b860eed831ac9d708ee3715443c66d7dd990ebfb2eeae2545dab43738dd9b
acceptance_record_digest: sha256:fca188fa9d5eebe87eb1e5198cb75fbc09c8a730c5fcd6cd203968cd0fcf1315
---

# Preserve private evidence identity across Chronicle imports

> **Why:** Preserve per-reference access, digest, expiry and observed evidence metadata when normalizing delivery references and deriving Chronicle evidence records. Avoid less-restrictive last-write-wins behavior for repeated locators. Reuse the existing event/details contract rather than adding an authority system.

## Goal

Preserve per-reference access, digest, expiry and observed evidence metadata when normalizing delivery references and deriving Chronicle evidence records. Avoid less-restrictive last-write-wins behavior for repeated locators. Reuse the existing event/details contract rather than adding an authority system.

## Context

Intent INTENT-BRIEFSPEC-REPAIR-WAVE; seam SEAM-CHRONICLE-PRIVATE-EVIDENCE; swimlane LANE-CHRONICLE-PRIVATE-EVIDENCE; capability leg LEG-CHRONICLE-PRIVATE-EVIDENCE. Done condition: Valid delivery references preserve private access, digest and expiry through normalization and snapshot derivation; repeated observations do not silently widen access or erase provenance.

## Behavior

- **B-1** — GIVEN A valid supported input and its corresponding prior reproduced defect WHEN Preserve per-reference access, digest, expiry and observed evidence metadata when normalizing delivery references and deriving Chronicle evidence records. Avoid less-restrictive last-write-wins behavior for repeated locators. Reuse the existing event/details contract rather than adding an authority system. THEN Valid delivery references preserve private access, digest and expiry through normalization and snapshot derivation; repeated observations do not silently widen access or erase provenance.
- **B-2** — GIVEN A valid ordinary input or an adversarial variant at the same public boundary WHEN The consumer exercises the corrected public behavior THEN Ordinary behavior remains supported and the invalid or contradictory variant cannot silently pass or acquire stronger authority.

## Success Criteria

```bash
# eval_1: Exercise the chronicle private evidence roundtrip observable contract.
eval_1() {
  uv run --no-sync pytest -q tests/test_chronicle.py -k chronicle_private_evidence_roundtrip
}

# eval_2: Exercise the chronicle evidence metadata preserved observable contract.
eval_2() {
  uv run --no-sync pytest -q tests/test_chronicle.py -k chronicle_evidence_metadata_preserved
}

# eval_3: Exercise the chronicle repeated reference restrictions observable contract.
eval_3() {
  uv run --no-sync pytest -q tests/test_chronicle.py -k chronicle_repeated_reference_restrictions
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "Exercise the chronicle private evidence roundtrip observable contract."
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "Exercise the chronicle evidence metadata preserved observable contract."
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "Exercise the chronicle repeated reference restrictions observable contract."
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
