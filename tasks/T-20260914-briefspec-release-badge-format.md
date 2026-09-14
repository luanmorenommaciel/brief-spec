---
id: T-20260914-briefspec-release-badge-format
title: "Verify candidate version independent of badge markup"
status: ready
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [scripts/verify-release.py]
creates_paths: [tests/test_release_verification.py]
source_note: "seamwise/legs/LEG-RELEASE-BADGE-FORMAT.md#T-20260914-briefspec-release-badge-format"
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
signed_off_by: repository-owner-via-explicit-release-gate-approval
signed_off_at: 2026-09-14T17:41:02Z
accepted: false
accepted_by: (none)
accepted_at: (none)
signed_off_sig: hmac-sha256-v3:7c09a871:4bf42a65d709eef846a486fb2650e527792be6034e8605f44838183cbc62bc25
---

# Verify candidate version independent of badge markup

> **Why:** Accept truthful HTML or Markdown badges without weakening version mismatch detection.

## Goal

Accept truthful HTML or Markdown badges without weakening version mismatch detection.

## Context

Intent INTENT-RELEASE-BADGE-FORMAT; seam SEAM-RELEASE-BADGE-FORMAT; swimlane LANE-RELEASE-BADGE-FORMAT; capability leg LEG-RELEASE-BADGE-FORMAT. Done condition: The unchanged current README passes release verification; HTML and Markdown badges with stale URL or label versions fail.

## Behavior

- **B-1** — GIVEN The candidate version is 0.5.0 and README uses either an HTML img or Markdown image with source_candidate-0.5.0 URL and Source candidate 0.5.0 label WHEN The release verifier checks versioned release evidence THEN Matching version evidence passes independently of the image markup syntax.
- **B-2** — GIVEN A candidate badge URL or label advertises a version different from the package version WHEN The same release verifier runs THEN Version inconsistency remains a verification failure.

## Success Criteria

```bash
# eval_1: release badge html
eval_1() {
  uv run --no-sync pytest -q tests/test_release_verification.py -k release_badge_html
}

# eval_2: release badge markdown
eval_2() {
  uv run --no-sync pytest -q tests/test_release_verification.py -k release_badge_markdown
}

# eval_3: release badge mismatch
eval_3() {
  uv run --no-sync pytest -q tests/test_release_verification.py -k release_badge_mismatch
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "release badge html"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "release badge markdown"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "release badge mismatch"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
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

Use the current valid HTML badge as the failing reproduction. Add semantic behavior regressions for HTML, Markdown, and stale URL/label versions. Do not edit README or disable version checks. Preserve all unrelated release checks. Run actual scripts/verify-release.py after the correction.

## Anti-Patterns

- Do not Treat a structurally valid record or model assertion as authorization or measured success: Brief-Spec explains observations; Task-Spec and humans retain authorization and acceptance authority.; instead Preserve explicit basis and require source-bound receipts for stronger claims..
- Do not Overwrite unrelated user changes or widen the signed write surface: Task-Spec authorization is bounded and receipt ownership must preserve user work.; instead Keep edits inside declared paths, serialize shared mutations and fail closed on conflicts..
- Do not Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants or stale summaries: Existence and apparent formatting do not prove the observable behavior.; instead Run the specified behavioral scenario on exact candidate inputs and retain truthful failure or blocked evidence..

## Do-Not-Touch

- `OPERATING.md`
- `AGENTS.md`
- `CLAUDE.md`
- `unrelated product source or tests`
- `global host installations and user branch history`

## Open Questions

(none — this task is fully specified)
