---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-TERMINAL-OBLIGATIONS
seam_id: SEAM-TERMINAL-OBLIGATIONS
swimlane_id: LANE-TERMINAL-OBLIGATIONS
observable_state: Required typed outcomes and requested checkpoint modes cannot be skipped by clearing
  flags too early, and every repair path remains bounded.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- repaired:terminal-obligations
tasks:
- id: T-20260914-briefspec-terminal-obligations
  title: Preserve stop obligations before clearing state
  goal: Evaluate typed outcome obligations against pre-stop state and validate explicitly requested checkpoint
    mode before clearing state. Preserve exactly one repair and the intentional Grok completed-brief release
    behavior.
  done_condition: Required typed outcomes and requested checkpoint modes cannot be skipped by clearing
    flags too early, and every repair path remains bounded.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - src/briefspec/hooks.py
  - tests/test_hook_policies.py
  creates_paths: []
  behavior:
  - id: B-1
    given: A valid supported input and its corresponding prior reproduced defect
    when: Evaluate typed outcome obligations against pre-stop state and validate explicitly requested
      checkpoint mode before clearing state. Preserve exactly one repair and the intentional Grok completed-brief
      release behavior.
    then: Required typed outcomes and requested checkpoint modes cannot be skipped by clearing flags too
      early, and every repair path remains bounded.
  - id: B-2
    given: A valid ordinary input or an adversarial variant at the same public boundary
    when: The consumer exercises the corrected public behavior
    then: Ordinary behavior remains supported and the invalid or contradictory variant cannot silently
      pass or acquire stronger authority.
  evals:
  - id: eval_1
    description: Exercise the terminal typed obligation observable contract.
    bash: uv run --no-sync pytest -q tests/test_hook_policies.py -k terminal_typed_obligation
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: Exercise the terminal requested mode observable contract.
    bash: uv run --no-sync pytest -q tests/test_hook_policies.py -k terminal_requested_mode
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: Exercise the terminal bounded native repair observable contract.
    bash: uv run --no-sync pytest -q tests/test_hook_policies.py -k terminal_bounded_native_repair
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
source_seam_sha256: f2726ccb03b4ee3379e2e5793bd1308745958b1168dde5a966de8301fb89d6d5
---
# Required typed outcomes and requested checkpoint modes cannot be skipped by clearing flags too early, and every repair path remains bounded.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-terminal-obligations` — Preserve stop obligations before clearing state: Required typed outcomes and requested checkpoint modes cannot be skipped by clearing flags too early, and every repair path remains bounded.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
