---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-OPERATIVE-ROUTING
seam_id: SEAM-OPERATIVE-ROUTING
swimlane_id: LANE-OPERATIVE-ROUTING
observable_state: Quoted or negated type/pivot examples cannot acquire explicit routing authority or break
  sticky classification; genuine overrides still take precedence.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- repaired:operative-routing
tasks:
- id: T-20260914-briefspec-operative-routing
  title: Ignore quoted and prohibited routing instructions
  goal: Use the same bounded operative-text interpretation for inferred and explicit routing, explicit
    override detection and pivot detection. Preserve true API overrides and genuine user directives.
  done_condition: Quoted or negated type/pivot examples cannot acquire explicit routing authority or break
    sticky classification; genuine overrides still take precedence.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - src/briefspec/work_types.py
  - tests/test_work_types.py
  creates_paths: []
  behavior:
  - id: B-1
    given: A valid supported input and its corresponding prior reproduced defect
    when: Use the same bounded operative-text interpretation for inferred and explicit routing, explicit
      override detection and pivot detection. Preserve true API overrides and genuine user directives.
    then: Quoted or negated type/pivot examples cannot acquire explicit routing authority or break sticky
      classification; genuine overrides still take precedence.
  - id: B-2
    given: A valid ordinary input or an adversarial variant at the same public boundary
    when: The consumer exercises the corrected public behavior
    then: Ordinary behavior remains supported and the invalid or contradictory variant cannot silently
      pass or acquire stronger authority.
  evals:
  - id: eval_1
    description: Exercise the operative routing quoted observable contract.
    bash: uv run --no-sync pytest -q tests/test_work_types.py -k operative_routing_quoted
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: Exercise the operative routing negated observable contract.
    bash: uv run --no-sync pytest -q tests/test_work_types.py -k operative_routing_negated
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: Exercise the operative routing genuine observable contract.
    bash: uv run --no-sync pytest -q tests/test_work_types.py -k operative_routing_genuine
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
source_seam_sha256: 133977538b4d1975ce0c2657d0f0ed4c6ad437985663f10a9c9c90d65b6c2ef2
---
# Quoted or negated type/pivot examples cannot acquire explicit routing authority or break sticky classification; genuine overrides still take precedence.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-operative-routing` — Ignore quoted and prohibited routing instructions: Quoted or negated type/pivot examples cannot acquire explicit routing authority or break sticky classification; genuine overrides still take precedence.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
