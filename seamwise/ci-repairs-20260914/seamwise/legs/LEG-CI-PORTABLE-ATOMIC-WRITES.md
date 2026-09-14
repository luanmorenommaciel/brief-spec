---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-CI-PORTABLE-ATOMIC-WRITES
seam_id: SEAM-CI-PORTABLE-ATOMIC-WRITES
swimlane_id: LANE-CI-PORTABLE-ATOMIC-WRITES
observable_state: Write atomically when descriptor chmod is unavailable and close the temporary descriptor
  before failure cleanup.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- ci:portable-atomic-writes:verified
tasks:
- id: T-20260914-briefspec-ci-portable-atomic-writes
  title: Preserve atomic writes on supported Windows Python versions
  goal: Write atomically when descriptor chmod is unavailable and close the temporary descriptor before
    failure cleanup.
  done_condition: Write atomically when descriptor chmod is unavailable and close the temporary descriptor
    before failure cleanup.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - src/briefspec/state.py
  - tests/test_state_and_hooks.py
  creates_paths: []
  behavior:
  - id: B-1
    given: os.fchmod is unavailable
    when: private state is saved and reloaded
    then: The state round-trips without weakening supported-platform permission handling.
  - id: B-2
    given: permission assignment fails for an existing destination
    when: the atomic write fails
    then: The original destination survives, the temporary descriptor is closed, the temporary file is
      removed, and the original permission error remains visible.
  evals:
  - id: eval_1
    description: portable state round-trip
    bash: uv run --no-sync pytest -q tests/test_state_and_hooks.py -k "without_fchmod or state_files_are_private"
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: permission failure ownership
    bash: uv run --no-sync pytest -q tests/test_state_and_hooks.py -k atomic_permission_failure
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: transaction rollback
    bash: uv run --no-sync pytest -q tests/test_delivery_edge_cases.py -k atomic
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
  observability: Keep descriptor-based chmod where available; use pathname chmod only when the platform
    lacks fchmod. Enter descriptor ownership before changing permissions. Do not suppress permission errors.
    Keep a discriminating round-trip and descriptor-cleanup regression.
  rollback: Revert only the authorized source/test patch; preserve all other user files and generated
    authorization records.
source_seam_sha256: e3c6391bcb38e1f59137e320efbec0b184228fe4a82cea1f1eb2458bcff4b294
---
# Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure cleanup.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-ci-portable-atomic-writes` — Preserve atomic writes on supported Windows Python versions: Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure cleanup.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
