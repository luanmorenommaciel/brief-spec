---
schema_version: 1
kind: seam
claim: derived
id: SEAM-CI-LIVE-RELEASE-EVIDENCE
name: Source-bound native release evidence
description: All software and clean-room installation checks passed; release authorization rejects the
  August evidence because its source fingerprint is stale.
evidence:
- EVIDENCE-LIVE-RELEASE-FRESHNESS
responsibility: Regenerate source-bound release evidence from fresh native measurements of every required
  host; retain failures and authorize only complete passing coverage.
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
  id: LANE-CI-LIVE-RELEASE-EVIDENCE
  name: Fresh native release acceptance
  owner: brief-spec
  legs:
  - id: LEG-CI-LIVE-RELEASE-EVIDENCE
    observable_state: Regenerate source-bound release evidence from fresh native measurements of every
      required host; retain failures and authorize only complete passing coverage.
    proof: Declared observable regressions and independent Task-Spec acceptance.
    requires: []
    produces:
    - ci:live-release-evidence:verified
    tasks:
    - id: T-20260914-briefspec-ci-live-release-evidence
      title: Refresh native release evidence for the corrected source
      goal: Regenerate source-bound release evidence from fresh native measurements of every required
        host; retain failures and authorize only complete passing coverage.
      done_condition: Regenerate source-bound release evidence from fresh native measurements of every
        required host; retain failures and authorize only complete passing coverage.
      effort: S
      profile: full
      execution_backend: omp
      required_tools:
      - python
      - uv
      depends_on: []
      touches_paths:
      - release/live-e2e-evidence.json
      creates_paths: []
      behavior:
      - id: B-1
        given: the corrected source and fresh native summaries for every required host
        when: the native builder regenerates release evidence
        then: The source fingerprint matches and all required host coverage is passing before authorization.
      - id: B-2
        given: fresh source-bound evidence
        when: release authorization is preflighted locally
        then: The real authorization builder succeeds without relabeling old evidence or weakening any
          gate.
      evals:
      - id: eval_1
        description: Fresh source-bound authorized host coverage
        bash: uv run --no-sync python scripts/build-live-e2e-evidence.py --check --require-authorized
        verifies:
        - B-1
      - id: eval_2
        description: Existing release surface integrity
        bash: uv run --no-sync python scripts/verify-release.py
        verifies:
        - B-1
      - id: eval_3
        description: Nonpublishing local release authorization preflight
        bash: uv run --no-sync python -c 'import subprocess, sys, tempfile; from pathlib import Path;
          sha = subprocess.check_output(['"'"'git'"'"', '"'"'rev-parse'"'"', '"'"'HEAD'"'"'], text=True).strip();
          temp = tempfile.TemporaryDirectory(prefix='"'"'brief-spec-local-authorization-'"'"'); subprocess.run([sys.executable,
          '"'"'scripts/build-release-authorization.py'"'"', '"'"'--sha'"'"', sha, '"'"'--run-id'"'"',
          '"'"'local-preflight'"'"', '"'"'--dist'"'"', temp.name], check=True); temp.cleanup()'
        verifies:
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
      observability: Run the existing native matrix against the actual corrected installation, preserving
        raw local attempt histories. Generate release/live-e2e-evidence.json only with scripts/build-live-e2e-evidence.py
        and fresh summaries. Local preflight uses run-id local-preflight in a disposable directory; it
        is not claimed as hosted authorization. Do not rewrite old scenario hashes or manufacture model
        identities.
      rollback: Revert only the authorized source/test patch; preserve all other user files and generated
        authorization records.
---
# Source-bound native release evidence

All software and clean-room installation checks passed; release authorization rejects the August evidence because its source fingerprint is stale.

## Responsibility

Regenerate source-bound release evidence from fresh native measurements of every required host; retain failures and authorize only complete passing coverage.

## Independent proof

Execute the declared real behavioral checks; preserve separate exact-SHA hosted-platform evidence.

## Rejected alternatives

- **Bypass validation or broaden write scope to a whole source directory** — That would hide the defect or evade the existing authorization boundary.

This derived seam is ready only while its cited evidence, named owner, contract,
and rejected alternatives remain intact.
