---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-CI-PORTABLE-CHRONICLE-ZIP
seam_id: SEAM-CI-PORTABLE-CHRONICLE-ZIP
swimlane_id: LANE-CI-PORTABLE-CHRONICLE-ZIP
observable_state: Create and read deterministic ZIP bytes through one owned temporary stream without reopening
  its live pathname.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- ci:portable-chronicle-zip:verified
tasks:
- id: T-20260914-briefspec-ci-portable-chronicle-zip
  title: Preserve deterministic Chronicle ZIP exports on Windows
  goal: Create and read deterministic ZIP bytes through one owned temporary stream without reopening its
    live pathname.
  done_condition: Create and read deterministic ZIP bytes through one owned temporary stream without reopening
    its live pathname.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  depends_on: []
  touches_paths:
  - packages/brief-spec-chronicle/src/brief_spec_chronicle/rendering.py
  creates_paths: []
  behavior:
  - id: B-1
    given: canonical Chronicle files and created_at are unchanged
    when: ZIP exports are repeated
    then: Archive contents, ordering, timestamps, modes, and resulting bytes remain deterministic.
  - id: B-2
    given: the platform restricts reopening temporary-file names
    when: Chronicle creates or restores an archive
    then: One owned temporary stream supplies completed ZIP bytes without a second pathname open.
  evals:
  - id: eval_1
    description: deterministic project export
    bash: uv run --no-sync pytest -q tests/test_chronicle.py::test_project_lifecycle_snapshot_and_deterministic_exports
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: archive restore
    bash: uv run --no-sync pytest -q tests/test_chronicle.py::test_archive_restore_to_new_project_root
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: native Chronicle command path
    bash: uv run --no-sync pytest -q tests/test_chronicle_cli_and_video.py::test_chronicle_cli_end_to_end
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
  observability: Use a portable temporary binary stream, pass the stream to ZipFile, finish its central
    directory, then seek and read the same stream. Preserve archive format and existing Windows regressions.
    Do not replace disk-backed staging with unnecessary whole-archive intermediate copies.
  rollback: Revert only the authorized source/test patch; preserve all other user files and generated
    authorization records.
source_seam_sha256: 77f36639c5178e3c444457842edd2c9c4d180e241ea6a1e1648b4d9c8b551524
---
# Create and read deterministic ZIP bytes through one owned temporary stream without reopening its live pathname.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-ci-portable-chronicle-zip` — Preserve deterministic Chronicle ZIP exports on Windows: Create and read deterministic ZIP bytes through one owned temporary stream without reopening its live pathname.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
