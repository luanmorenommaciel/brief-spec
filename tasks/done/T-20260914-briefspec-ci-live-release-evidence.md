---
id: T-20260914-briefspec-ci-live-release-evidence
title: "Refresh native release evidence for the corrected source"
status: done
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [release/live-e2e-evidence.json]
creates_paths: []
source_note: "seamwise/legs/LEG-CI-LIVE-RELEASE-EVIDENCE.md#T-20260914-briefspec-ci-live-release-evidence"
created: "2026-09-14T00:00:00Z"
tags: []
owner: (none)
priority: P2
severity: feature
due_date: (none)
precondition: (none)
blocked_reason: (none)
security_class: (none)
source_action_item: (none)
tracker_ref: (none)
execution_backend: omp
signed_off: true
signed_off_by: repository-owner-via-explicit-live-evidence-approval
signed_off_at: 2026-09-14T21:49:48Z
accepted: true
accepted_by: luanmorenomaciel
accepted_at: 2026-09-14T22:25:45Z
signed_off_sig: hmac-sha256-v3:7c09a871:8ce4e338c82e007074721fd36c7ca765efd25f393ff4eebcf471129a03fb7ba7
accepted_tier: 1
accepted_attempt_id: 2b6658e9-6f0a-4dcd-9559-699125bac130
accepted_authorization_ref: hmac-sha256-v3:7c09a871:8ce4e338c82e007074721fd36c7ca765efd25f393ff4eebcf471129a03fb7ba7
acceptance_record_digest: sha256:307ed407b37f9e49fb04562b2b7bee266661c47c6ef1a54fddcfc4dd11b1924b
---

# Refresh native release evidence for the corrected source

> **Why:** Regenerate source-bound release evidence from fresh native measurements of every required host; retain failures and authorize only complete passing coverage.

## Goal

Regenerate source-bound release evidence from fresh native measurements of every required host; retain failures and authorize only complete passing coverage.

## Context

Intent INTENT-BRIEFSPEC-LIVE-RELEASE-EVIDENCE; seam SEAM-CI-LIVE-RELEASE-EVIDENCE; swimlane LANE-CI-LIVE-RELEASE-EVIDENCE; capability leg LEG-CI-LIVE-RELEASE-EVIDENCE. Done condition: Regenerate source-bound release evidence from fresh native measurements of every required host; retain failures and authorize only complete passing coverage.

## Behavior

- **B-1** — GIVEN the corrected source and fresh native summaries for every required host WHEN the native builder regenerates release evidence THEN The source fingerprint matches and all required host coverage is passing before authorization.
- **B-2** — GIVEN fresh source-bound evidence WHEN release authorization is preflighted locally THEN The real authorization builder succeeds without relabeling old evidence or weakening any gate.

## Success Criteria

```bash
# eval_1: Fresh source-bound authorized host coverage
eval_1() {
  uv run --no-sync python scripts/build-live-e2e-evidence.py --check --require-authorized
}

# eval_2: Existing release surface integrity
eval_2() {
  uv run --no-sync python scripts/verify-release.py
}

# eval_3: Nonpublishing local release authorization preflight
eval_3() {
  uv run --no-sync python -c 'import subprocess, sys, tempfile; from pathlib import Path; sha = subprocess.check_output(['"'"'git'"'"', '"'"'rev-parse'"'"', '"'"'HEAD'"'"'], text=True).strip(); temp = tempfile.TemporaryDirectory(prefix='"'"'brief-spec-local-authorization-'"'"'); subprocess.run([sys.executable, '"'"'scripts/build-release-authorization.py'"'"', '"'"'--sha'"'"', sha, '"'"'--run-id'"'"', '"'"'local-preflight'"'"', '"'"'--dist'"'"', temp.name], check=True); temp.cleanup()'
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "Fresh source-bound authorized host coverage"
    runnable: bash
    check_type: deterministic
    verifies: [B-1]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "Existing release surface integrity"
    runnable: bash
    check_type: deterministic
    verifies: [B-1]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "Nonpublishing local release authorization preflight"
    runnable: bash
    check_type: deterministic
    verifies: [B-2]
    terminal: true
    expected_duration_sec: 10
retry_policy:
  max_iterations: 15
  circuit_breaker_no_progress: 3
  on_terminal_failure: park_with_context
agent_contract:
  version: 2
  read: [intent, behavior, contract, guardrails]
  produce: [code, tests]
  required_tools: [python, uv]
  timeout_minutes: 30
  sandbox_type: host
  output_artifacts: []
  mcp_dependencies: []
  emit: [pass, fail, retry_with_reason, parked_with_context]
  backend_metadata: {}
```

## Exit Check

```bash
eval_1 && eval_2 && eval_3
```

## Rollback Plan

Revert only the authorized source/test patch; preserve all other user files and generated authorization records.

## Observability Hooks

Run the existing native matrix against the actual corrected installation, preserving raw local attempt histories. Generate release/live-e2e-evidence.json only with scripts/build-live-e2e-evidence.py and fresh summaries. Local preflight uses run-id local-preflight in a disposable directory; it is not claimed as hosted authorization. Do not rewrite old scenario hashes or manufacture model identities.

## Anti-Patterns

- Do not Treat a structurally valid record or model assertion as authorization or measured success: Brief-Spec explains observations; Task-Spec and humans retain authorization and acceptance authority.; instead Preserve explicit basis and require source-bound receipts for stronger claims..
- Do not Overwrite unrelated user changes or widen the signed write surface: Task-Spec authorization is bounded and receipt ownership must preserve user work.; instead Keep edits inside declared paths, serialize shared mutations and fail closed on conflicts..
- Do not Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants or stale summaries: Existence and apparent formatting do not prove the observable behavior.; instead Run the specified behavioral scenario on exact candidate inputs and retain truthful failure or blocked evidence..

## Do-Not-Touch

- `OPERATING.md`
- `AGENTS.md`
- `unrelated source or tests`
- `global host installations and user branch history`
- `CLAUDE.md`

## Open Questions

(none — this task is fully specified)
