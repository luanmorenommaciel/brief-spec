---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-VISIBLE-HTML-INTEGRITY
seam_id: SEAM-VISIBLE-HTML-INTEGRITY
swimlane_id: LANE-VISIBLE-HTML-INTEGRITY
observable_state: Visible outcome or other report content mutations fail rendered verification even when
  embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- repaired:visible-html-integrity
tasks:
- id: T-20260914-briefspec-visible-html-integrity
  title: Bind visible HTML to valid canonical content
  goal: Validate canonical content embedded in standalone HTML and verify its visible deterministic projection
    as rigorously as core bundles, without changing rendering or relaxing offline controls.
  done_condition: Visible outcome or other report content mutations fail rendered verification even when
    embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - src/briefspec/verification.py
  - tests/test_delivery.py
  creates_paths: []
  behavior:
  - id: B-1
    given: A valid supported input and its corresponding prior reproduced defect
    when: Validate canonical content embedded in standalone HTML and verify its visible deterministic
      projection as rigorously as core bundles, without changing rendering or relaxing offline controls.
    then: Visible outcome or other report content mutations fail rendered verification even when embedded
      canonical JSON and hash remain unchanged; valid canonical renderings still pass.
  - id: B-2
    given: A valid ordinary input or an adversarial variant at the same public boundary
    when: The consumer exercises the corrected public behavior
    then: Ordinary behavior remains supported and the invalid or contradictory variant cannot silently
      pass or acquire stronger authority.
  evals:
  - id: eval_1
    description: Exercise the standalone html visible tamper observable contract.
    bash: uv run --no-sync pytest -q tests/test_delivery.py -k standalone_html_visible_tamper
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: Exercise the standalone html invalid canonical observable contract.
    bash: uv run --no-sync pytest -q tests/test_delivery.py -k standalone_html_invalid_canonical
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: Exercise the standalone html valid projection observable contract.
    bash: uv run --no-sync pytest -q tests/test_delivery.py -k standalone_html_valid_projection
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
source_seam_sha256: a5954183ca705d9da02541fed0452327cfd9acc67e96b1f18c7538c325f8e07b
---
# Visible outcome or other report content mutations fail rendered verification even when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-visible-html-integrity` — Bind visible HTML to valid canonical content: Visible outcome or other report content mutations fail rendered verification even when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
