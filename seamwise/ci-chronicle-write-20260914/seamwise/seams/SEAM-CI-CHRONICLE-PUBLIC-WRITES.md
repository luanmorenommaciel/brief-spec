---
schema_version: 1
kind: seam
claim: derived
id: SEAM-CI-CHRONICLE-PUBLIC-WRITES
name: Reuse portable public atomic writes for Chronicle outputs
description: Candidate 32e36be passes 17 hosted jobs. Windows Python 3.11 and 3.12 still fail six Chronicle
  scenarios because storage.atomic_external_write independently calls missing os.fchmod before owning
  its descriptor. Core atomic_write_public already implements the portable public-write contract in this
  candidate.
evidence:
- EVIDENCE-CI-CHRONICLE-PUBLIC-WRITES
responsibility: Chronicle outputs succeed without os.fchmod while retaining public parent permissions,
  explicit overwrite authorization, and atomic failure cleanup.
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
  id: LANE-CI-CHRONICLE-PUBLIC-WRITES
  name: Reuse portable public atomic writes for Chronicle outputs
  owner: brief-spec
  legs:
  - id: LEG-CI-CHRONICLE-PUBLIC-WRITES
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
        then: Both writes succeed without changing the parent mode; an unforced overwrite is rejected
          without changing the existing bytes.
      - id: B-2
        given: the shared writer fails during permission assignment
        when: an atomic write fails
        then: The original destination survives, the descriptor is closed, temporary output is removed,
          and the real failure remains visible.
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
      observability: Retain the existing FileExistsError guard, call existing briefspec.state.atomic_write_public,
        remove the obsolete local temporary-file implementation and its unused import, and add a behavior-level
        regression covering missing fchmod, explicit overwrite, output bytes, and unchanged public parent
        mode. Do not introduce another writer or suppress failures.
      rollback: Revert only the authorized source/test patch; preserve all other user files and generated
        authorization records.
---
# Reuse portable public atomic writes for Chronicle outputs

Candidate 32e36be passes 17 hosted jobs. Windows Python 3.11 and 3.12 still fail six Chronicle scenarios because storage.atomic_external_write independently calls missing os.fchmod before owning its descriptor. Core atomic_write_public already implements the portable public-write contract in this candidate.

## Responsibility

Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite authorization, and atomic failure cleanup.

## Independent proof

Execute the declared real behavioral checks; preserve separate exact-SHA hosted-platform evidence.

## Rejected alternatives

- **Bypass validation or broaden write scope to a whole source directory** — That would hide the defect or evade the existing authorization boundary.

This derived seam is ready only while its cited evidence, named owner, contract,
and rejected alternatives remain intact.
