---
id: T-20260914-briefspec-ci-audio-verification-contract
title: "Verify native audio against its actual delivery contract"
status: done
format_version: 3
profile: full
effort: M
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [scripts/run-renderer-smoke.py, docs/delivery.md, CHANGELOG.md]
creates_paths: []
source_note: "seamwise/legs/LEG-CI-AUDIO-VERIFICATION-CONTRACT.md#T-20260914-briefspec-ci-audio-verification-contract"
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
signed_off_at: 2026-09-14T20:04:33Z
accepted: true
accepted_by: luanmorenomaciel
accepted_at: 2026-09-14T20:27:26Z
signed_off_sig: hmac-sha256-v3:7c09a871:8e8bff8cd483746195d28ab8bf12cd995634f7171e2240de568ac8f5b9787280
accepted_tier: 1
accepted_attempt_id: c1aaf92c-d7b1-48f7-a720-54f591dcac53
accepted_authorization_ref: hmac-sha256-v3:7c09a871:8e8bff8cd483746195d28ab8bf12cd995634f7171e2240de568ac8f5b9787280
acceptance_record_digest: sha256:c31099d25da7c7c4c453a2ffc2c8fbd7bab84db145b8366a5ba30743356c2d9f
---

# Verify native audio against its actual delivery contract

> **Why:** Verify identical canonical input and each independently rendered audio artifact rather than require byte-identical output from separate operating-system speech generations.

## Goal

Verify identical canonical input and each independently rendered audio artifact rather than require byte-identical output from separate operating-system speech generations.

## Context

Intent INTENT-BRIEFSPEC-CI-REPAIRS; seam SEAM-CI-AUDIO-VERIFICATION-CONTRACT; swimlane LANE-CI-AUDIO-VERIFICATION-CONTRACT; capability leg LEG-CI-AUDIO-VERIFICATION-CONTRACT. Done condition: Verify identical canonical input and each independently rendered audio artifact rather than require byte-identical output from separate operating-system speech generations.

## Behavior

- **B-1** — GIVEN export and bundle use the same canonical source and created_at WHEN the real optional-renderer smoke runs THEN Canonical JSON bytes agree; standalone media and the completed bundle pass rendered verification, including audio provenance and actual artifact integrity.
- **B-2** — GIVEN PDF output changes or a rendered artifact is invalid WHEN verification runs THEN PDF byte identity remains required and invalid artifacts still fail; only the unsupported independent-TTS-byte assertion is removed.

## Success Criteria

```bash
# eval_1: native offline audio delivery
eval_1() {
  python scripts/run-renderer-smoke.py audio
}

# eval_2: native PDF byte identity
eval_2() {
  python scripts/run-renderer-smoke.py pdf
}

# eval_3: bundle and receipt tamper rejection
eval_3() {
  uv run --no-sync pytest -q tests/test_delivery_edge_cases.py::test_html_bundle_and_receipt_tampering_is_detected
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "native offline audio delivery"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "native PDF byte identity"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "bundle and receipt tamper rejection"
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

Export canonical JSON alongside the optional format and compare it with the bundled canonical JSON. Preserve PDF byte equality and both existing rendered verification calls. Do not cache speech, trim or change audio content, switch providers, loosen integrity verification, or claim native TTS byte determinism. Clarify the existing delivery documentation and, after smoke proof, record all four authorized CI repairs in the existing changelog.

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
