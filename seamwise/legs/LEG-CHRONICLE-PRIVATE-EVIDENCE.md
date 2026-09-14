---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-CHRONICLE-PRIVATE-EVIDENCE
seam_id: SEAM-CHRONICLE-PRIVATE-EVIDENCE
swimlane_id: LANE-CHRONICLE-PRIVATE-EVIDENCE
observable_state: Valid delivery references preserve private access, digest and expiry through normalization
  and snapshot derivation; repeated observations do not silently widen access or erase provenance.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- repaired:chronicle-private-evidence
tasks:
- id: T-20260914-briefspec-chronicle-private-evidence
  title: Preserve private evidence identity across Chronicle imports
  goal: Preserve per-reference access, digest, expiry and observed evidence metadata when normalizing
    delivery references and deriving Chronicle evidence records. Avoid less-restrictive last-write-wins
    behavior for repeated locators. Reuse the existing event/details contract rather than adding an authority
    system.
  done_condition: Valid delivery references preserve private access, digest and expiry through normalization
    and snapshot derivation; repeated observations do not silently widen access or erase provenance.
  effort: M
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - packages/brief-spec-chronicle/src/brief_spec_chronicle/sources.py
  - packages/brief-spec-chronicle/src/brief_spec_chronicle/derive.py
  - tests/test_chronicle.py
  creates_paths: []
  behavior:
  - id: B-1
    given: A valid supported input and its corresponding prior reproduced defect
    when: Preserve per-reference access, digest, expiry and observed evidence metadata when normalizing
      delivery references and deriving Chronicle evidence records. Avoid less-restrictive last-write-wins
      behavior for repeated locators. Reuse the existing event/details contract rather than adding an
      authority system.
    then: Valid delivery references preserve private access, digest and expiry through normalization and
      snapshot derivation; repeated observations do not silently widen access or erase provenance.
  - id: B-2
    given: A valid ordinary input or an adversarial variant at the same public boundary
    when: The consumer exercises the corrected public behavior
    then: Ordinary behavior remains supported and the invalid or contradictory variant cannot silently
      pass or acquire stronger authority.
  evals:
  - id: eval_1
    description: Exercise the chronicle private evidence roundtrip observable contract.
    bash: uv run --no-sync pytest -q tests/test_chronicle.py -k chronicle_private_evidence_roundtrip
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: Exercise the chronicle evidence metadata preserved observable contract.
    bash: uv run --no-sync pytest -q tests/test_chronicle.py -k chronicle_evidence_metadata_preserved
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: Exercise the chronicle repeated reference restrictions observable contract.
    bash: uv run --no-sync pytest -q tests/test_chronicle.py -k chronicle_repeated_reference_restrictions
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
  - CLAUDE.md
  - unrelated product source or tests
  - global host installations and user branch history
  observability: Add deterministic behavioral regressions to the existing declared test file. Do not assert
    source text or mock echoes. Skip formatters, linters and all tests while sibling writers are in flight;
    the supervising parent independently runs the declared evaluations and final suite. Do not change
    tests merely to preserve implementation details. Read the exact source path; keep changes only in
    signed scope.
  rollback: Revert only the authorized source/test patch; preserve all other user files and generated
    authorization records.
source_seam_sha256: dd0d76c87948977de2406deac9aa3a8cdca024433133caf49a0dfcc72697fb59
---
# Valid delivery references preserve private access, digest and expiry through normalization and snapshot derivation; repeated observations do not silently widen access or erase provenance.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-chronicle-private-evidence` — Preserve private evidence identity across Chronicle imports: Valid delivery references preserve private access, digest and expiry through normalization and snapshot derivation; repeated observations do not silently widen access or erase provenance.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
