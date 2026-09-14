---
schema_version: 1
kind: seam
claim: derived
id: SEAM-CI-PORTABLE-ATOMIC-WRITES
name: Preserve atomic writes on supported Windows Python versions
description: Windows Python 3.11 and 3.12 lack os.fchmod; permission failure occurs before descriptor
  ownership enters a context and cleanup can mask it with WinError 32.
evidence:
- EVIDENCE-CI-PORTABLE-ATOMIC-WRITES
responsibility: Write atomically when descriptor chmod is unavailable and close the temporary descriptor
  before failure cleanup.
consumes:
- Existing supported public boundary inputs
produces:
- Corrected observable boundary behavior and regression evidence
owner: brief-spec
independent_proof: Execute the declared real behavioral checks; preserve separate exact-SHA hosted-platform
  evidence.
rejected_alternatives:
- alternative: Bypass validation or broaden write scope to a whole source directory
  reason: That would hide the defect or evade the existing authorization boundary.
swimlane:
  id: LANE-CI-PORTABLE-ATOMIC-WRITES
  name: Preserve atomic writes on supported Windows Python versions
  owner: brief-spec
  legs:
  - id: LEG-CI-PORTABLE-ATOMIC-WRITES
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
        then: The original destination survives, the temporary descriptor is closed, the temporary file
          is removed, and the original permission error remains visible.
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
      - action: Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants
          or stale summaries
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
        lacks fchmod. Enter descriptor ownership before changing permissions. Do not suppress permission
        errors. Keep a discriminating round-trip and descriptor-cleanup regression.
      rollback: Revert only the authorized source/test patch; preserve all other user files and generated
        authorization records.
---
# Preserve atomic writes on supported Windows Python versions

Windows Python 3.11 and 3.12 lack os.fchmod; permission failure occurs before descriptor ownership enters a context and cleanup can mask it with WinError 32.

## Responsibility

Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure cleanup.

## Independent proof

Execute the declared real behavioral checks; preserve separate exact-SHA hosted-platform evidence.

## Rejected alternatives

- **Bypass validation or broaden write scope to a whole source directory** — That would hide the defect or evade the existing authorization boundary.

This derived seam is ready only while its cited evidence, named owner, contract,
and rejected alternatives remain intact.
