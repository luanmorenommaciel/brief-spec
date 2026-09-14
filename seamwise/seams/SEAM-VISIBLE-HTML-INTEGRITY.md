---
schema_version: 1
kind: seam
claim: derived
id: SEAM-VISIBLE-HTML-INTEGRITY
name: Bind visible HTML to valid canonical content
description: Validate canonical content embedded in standalone HTML and verify its visible deterministic
  projection as rigorously as core bundles, without changing rendering or relaxing offline controls.
evidence:
- EVIDENCE-REPAIR-INPUT
responsibility: Visible outcome or other report content mutations fail rendered verification even when
  embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.
consumes:
- Existing supported public boundary inputs
produces:
- Corrected observable boundary behavior and regression evidence
owner: brief-spec
independent_proof: Three specified behavioral regression selectors must pass after the fix and discriminate
  the prior defect.
rejected_alternatives:
- alternative: Bypass validation or broaden write scope to a whole source directory
  reason: That would hide the defect or evade the existing authorization boundary.
swimlane:
  id: LANE-VISIBLE-HTML-INTEGRITY
  name: Bind visible HTML to valid canonical content
  owner: brief-spec
  legs:
  - id: LEG-VISIBLE-HTML-INTEGRITY
    observable_state: Visible outcome or other report content mutations fail rendered verification even
      when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.
    proof: Declared observable regressions and independent Task-Spec acceptance.
    requires: []
    produces:
    - repaired:visible-html-integrity
    tasks:
    - id: T-20260914-briefspec-visible-html-integrity
      title: Bind visible HTML to valid canonical content
      goal: Validate canonical content embedded in standalone HTML and verify its visible deterministic
        projection as rigorously as core bundles, without changing rendering or relaxing offline controls.
      done_condition: Visible outcome or other report content mutations fail rendered verification even
        when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.
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
      - action: Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants
          or stale summaries
        reason: Existence and apparent formatting do not prove the observable behavior.
        instead: Run the specified behavioral scenario on exact candidate inputs and retain truthful failure
          or blocked evidence.
      do_not_touch:
      - OPERATING.md
      - AGENTS.md
      - CLAUDE.md
      - unrelated product source or tests
      - global host installations and user branch history
      observability: Add deterministic behavioral regressions to the existing declared test file. Do not
        assert source text or mock echoes. Skip formatters, linters and all tests while sibling writers
        are in flight; the supervising parent independently runs the declared evaluations and final suite.
        Do not change tests merely to preserve implementation details. Read the exact source path; keep
        changes only in signed scope.
      rollback: Revert only the authorized source/test patch; preserve all other user files and generated
        authorization records.
---
# Bind visible HTML to valid canonical content

Validate canonical content embedded in standalone HTML and verify its visible deterministic projection as rigorously as core bundles, without changing rendering or relaxing offline controls.

## Responsibility

Visible outcome or other report content mutations fail rendered verification even when embedded canonical JSON and hash remain unchanged; valid canonical renderings still pass.

## Independent proof

Three specified behavioral regression selectors must pass after the fix and discriminate the prior defect.

## Rejected alternatives

- **Bypass validation or broaden write scope to a whole source directory** — That would hide the defect or evade the existing authorization boundary.

This derived seam is ready only while its cited evidence, named owner, contract,
and rejected alternatives remain intact.
