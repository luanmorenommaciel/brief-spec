---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-CI-CHRONICLE-PUBLIC-WRITES
seam_id: SEAM-CI-CHRONICLE-PUBLIC-WRITES
swimlane_id: LANE-CI-CHRONICLE-PUBLIC-WRITES
observable_state: Chronicle outputs succeed without os.fchmod while retaining public parent permissions,
  explicit overwrite authorization, and atomic failure cleanup.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- ci:chronicle-public-writes:verified
tasks:
- id: T-20260914-briefspec-ci-chronicle-public-writes
  title: Reuse portable public atomic writes for Chronicle outputs
  goal: Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit
    overwrite authorization, and atomic failure cleanup.
  done_condition: Chronicle outputs succeed without os.fchmod while retaining public parent permissions,
    explicit overwrite authorization, and atomic failure cleanup.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - packages/brief-spec-chronicle/src/brief_spec_chronicle/storage.py
  - tests/test_chronicle.py
  creates_paths: []
  behavior:
  - id: B-1
    given: os.fchmod is unavailable and an output parent is public
    when: Chronicle creates and explicitly replaces an output
    then: Both writes succeed without changing the parent mode; an unforced overwrite is rejected without
      changing the existing bytes.
  - id: B-2
    given: the shared writer fails during permission assignment
    when: an atomic write fails
    then: The original destination survives, the descriptor is closed, temporary output is removed, and
      the real failure remains visible.
  evals:
  - id: eval_1
    description: Chronicle public output portability and overwrite boundary
    bash: uv run --no-sync pytest -q tests/test_chronicle.py -k external_write_without_fchmod
    verifies:
    - B-1
  - id: eval_2
    description: Shared atomic writer failure ownership
    bash: uv run --no-sync pytest -q tests/test_state_and_hooks.py -k atomic_permission_failure
    verifies:
    - B-2
  - id: eval_3
    description: Real Chronicle CLI and family journey outputs
    bash: uv run --no-sync pytest -q tests/test_chronicle_cli_and_video.py -k "chronicle_cli_end_to_end
      or disposable_seamwise_task_spec_converge_journey"
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
  observability: Retain the existing FileExistsError guard, call existing briefspec.state.atomic_write_public,
    remove the obsolete local temporary-file implementation and its unused import, and add a behavior-level
    regression covering missing fchmod, explicit overwrite, output bytes, and unchanged public parent
    mode. Do not introduce another writer or suppress failures.
  rollback: Revert only the authorized source/test patch; preserve all other user files and generated
    authorization records.
source_seam_sha256: 18a820014fdf94f3bc571ad058799ab63b228ac92fa810cbee8785b7d8487aeb
---
# Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite authorization, and atomic failure cleanup.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-ci-chronicle-public-writes` — Reuse portable public atomic writes for Chronicle outputs: Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite authorization, and atomic failure cleanup.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
