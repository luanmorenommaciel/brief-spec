---
id: T-20260914-briefspec-ci-claude-plugin-layout
title: "Separate repository Claude memory from plugin-root content"
status: in-progress
format_version: 3
profile: full
effort: S
budget_iterations: 15
agent: any
parent: (none)
depends_on: []
supersedes: (none)
touches_paths: [CLAUDE.md]
creates_paths: [.claude/CLAUDE.md]
source_note: "seamwise/legs/LEG-CI-CLAUDE-PLUGIN-LAYOUT.md#T-20260914-briefspec-ci-claude-plugin-layout"
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
signed_off_by: repository-owner-via-explicit-ci-repair-approval
signed_off_at: 2026-09-14T20:04:39Z
accepted: false
accepted_by: (none)
accepted_at: (none)
signed_off_sig: hmac-sha256-v3:7c09a871:0300210dae949d306c31537009b78b5a8f9b5ad92cd24bc49addc75718d1dd46
---

# Separate repository Claude memory from plugin-root content

> **Why:** Preserve the repository operating import in the officially supported .claude/CLAUDE.md location while keeping strict native plugin validation enabled.

## Goal

Preserve the repository operating import in the officially supported .claude/CLAUDE.md location while keeping strict native plugin validation enabled.

## Context

Intent INTENT-BRIEFSPEC-CI-REPAIRS; seam SEAM-CI-CLAUDE-PLUGIN-LAYOUT; swimlane LANE-CI-CLAUDE-PLUGIN-LAYOUT; capability leg LEG-CI-CLAUDE-PLUGIN-LAYOUT. Done condition: Preserve the repository operating import in the officially supported .claude/CLAUDE.md location while keeping strict native plugin validation enabled.

## Behavior

- **B-1** — GIVEN the repository is opened in Claude WHEN project memory is loaded THEN The existing OPERATING.md instructions remain reachable through .claude/CLAUDE.md with the corrected relative import.
- **B-2** — GIVEN current Claude validates the published plugin and marketplace WHEN strict validation runs THEN Both native validators pass without suppressing warnings, weakening strict mode, or deleting the repository instructions.

## Success Criteria

```bash
# eval_1: strict plugin validation
eval_1() {
  claude plugin validate .claude-plugin/plugin.json --strict
}

# eval_2: strict marketplace validation
eval_2() {
  claude plugin validate .claude-plugin/marketplace.json --strict
}

# eval_3: native operating import through project memory
eval_3() {
  python -c 'import json,secrets,subprocess,tempfile;from pathlib import Path;fixture=tempfile.TemporaryDirectory(prefix='"'"'briefspec-claude-memory-'"'"');root=Path(fixture.name);(root/'"'"'.claude'"'"').mkdir();(root/'"'"'.claude/CLAUDE.md'"'"').write_text(Path('"'"'.claude/CLAUDE.md'"'"').read_text());token=secrets.token_hex(16);(root/'"'"'OPERATING.md'"'"').write_text(Path('"'"'OPERATING.md'"'"').read_text()+'"'"'\nMemory proof token: '"'"'+token+'"'"'\n'"'"');result=subprocess.run(['"'"'claude'"'"','"'"'--print'"'"','"'"'Return only the memory proof token from the project instructions. Do not use tools.'"'"','"'"'--output-format'"'"','"'"'json'"'"','"'"'--tools'"'"','"'"''"'"','"'"'--max-turns'"'"','"'"'1'"'"','"'"'--no-session-persistence'"'"','"'"'--setting-sources'"'"','"'"'project'"'"','"'"'--settings'"'"','"'"'{"autoMemoryEnabled":false}'"'"','"'"'--mcp-config'"'"','"'"'{"mcpServers":{}}'"'"','"'"'--strict-mcp-config'"'"'],cwd=root,capture_output=True,text=True,timeout=120);assert result.returncode==0,result.stderr;answer=json.loads(result.stdout)['"'"'result'"'"'].strip();assert answer==token,answer;print('"'"'Native Claude project-memory import: PASS'"'"');fixture.cleanup()'
}

```

## Validation Card

```yaml
success_criteria:
  - id: eval_1
    description: "strict plugin validation"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_2
    description: "strict marketplace validation"
    runnable: bash
    check_type: deterministic
    verifies: [B-1, B-2]
    terminal: false
    expected_duration_sec: 10
  - id: eval_3
    description: "native operating import through project memory"
    runnable: bash
    check_type: deterministic
    verifies: [B-1]
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
  required_tools: [python, uv, claude]
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

Move the existing one-line repository memory file; adjust @OPERATING.md to @../OPERATING.md. Do not modify OPERATING.md, plugin functionality, or CI strictness. Official contract: https://code.claude.com/docs/en/memory .

## Anti-Patterns

- Do not Treat a structurally valid record or model assertion as authorization or measured success: Brief-Spec explains observations; Task-Spec and humans retain authorization and acceptance authority.; instead Preserve explicit basis and require source-bound receipts for stronger claims..
- Do not Overwrite unrelated user changes or widen the signed write surface: Task-Spec authorization is bounded and receipt ownership must preserve user work.; instead Keep edits inside declared paths, serialize shared mutations and fail closed on conflicts..
- Do not Satisfy an acceptance gate with stubs, source-text assertions, fabricated participants or stale summaries: Existence and apparent formatting do not prove the observable behavior.; instead Run the specified behavioral scenario on exact candidate inputs and retain truthful failure or blocked evidence..

## Do-Not-Touch

- `OPERATING.md`
- `AGENTS.md`
- `unrelated source or tests`
- `global host installations and user branch history`

## Open Questions

(none — this task is fully specified)
