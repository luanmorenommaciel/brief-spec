---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-CI-AUDIO-VERIFICATION-CONTRACT
seam_id: SEAM-CI-AUDIO-VERIFICATION-CONTRACT
swimlane_id: LANE-CI-AUDIO-VERIFICATION-CONTRACT
observable_state: Verify identical canonical input and each independently rendered audio artifact rather
  than require byte-identical output from separate operating-system speech generations.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- ci:audio-verification-contract:verified
tasks:
- id: T-20260914-briefspec-ci-audio-verification-contract
  title: Verify native audio against its actual delivery contract
  goal: Verify identical canonical input and each independently rendered audio artifact rather than require
    byte-identical output from separate operating-system speech generations.
  done_condition: Verify identical canonical input and each independently rendered audio artifact rather
    than require byte-identical output from separate operating-system speech generations.
  effort: M
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - scripts/run-renderer-smoke.py
  - docs/delivery.md
  - CHANGELOG.md
  creates_paths: []
  behavior:
  - id: B-1
    given: export and bundle use the same canonical source and created_at
    when: the real optional-renderer smoke runs
    then: Canonical JSON bytes agree; standalone media and the completed bundle pass rendered verification,
      including audio provenance and actual artifact integrity.
  - id: B-2
    given: PDF output changes or a rendered artifact is invalid
    when: verification runs
    then: PDF byte identity remains required and invalid artifacts still fail; only the unsupported independent-TTS-byte
      assertion is removed.
  evals:
  - id: eval_1
    description: native offline audio delivery
    bash: python scripts/run-renderer-smoke.py audio
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: native PDF byte identity
    bash: python scripts/run-renderer-smoke.py pdf
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: bundle and receipt tamper rejection
    bash: uv run --no-sync pytest -q tests/test_delivery_edge_cases.py::test_html_bundle_and_receipt_tampering_is_detected
    verifies:
    - B-1
    - B-2
  anti_patterns:
  - action: Treat a structurally valid record or model assertion as authorization or measured success
    reason: Brief-Spec explains observations; Task-Spec and humans retain authorization and acceptance
      authority.
    instead: Preserve explicit basis and require source-bound receipts for stronger claims.
  - action: Overwrite unrelated user changes or widen the signed write surface
    reason: Task-Spec authorization is bounded and receipt ownership must preserve user work.
    instead: Keep edits inside declared paths, serialize shared mutations and fail closed on conflicts.
  - action: Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants or
      stale summaries
    reason: Existence and apparent formatting do not prove the observable behavior.
    instead: Run the specified behavioral scenario on exact candidate inputs and retain truthful failure
      or blocked evidence.
  do_not_touch:
  - OPERATING.md
  - AGENTS.md
  - unrelated source or tests
  - global host installations and user branch history
  - CLAUDE.md
  observability: Export canonical JSON alongside the optional format and compare it with the bundled canonical
    JSON. Preserve PDF byte equality and both existing rendered verification calls. Do not cache speech,
    trim or change audio content, switch providers, loosen integrity verification, or claim native TTS
    byte determinism. Clarify the existing delivery documentation and, after smoke proof, record all four
    authorized CI repairs in the existing changelog.
  rollback: Revert only the authorized source/test patch; preserve all other user files and generated
    authorization records.
source_seam_sha256: a823f76159c44773b5296c2f77dfe4274d92515b5975e956b0bfa3d605ea9682
---
# Verify identical canonical input and each independently rendered audio artifact rather than require byte-identical output from separate operating-system speech generations.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-ci-audio-verification-contract` — Verify native audio against its actual delivery contract: Verify identical canonical input and each independently rendered audio artifact rather than require byte-identical output from separate operating-system speech generations.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
