---
schema_version: 1
kind: capability-leg
claim: derived
id: LEG-CI-CLAUDE-PLUGIN-LAYOUT
seam_id: SEAM-CI-CLAUDE-PLUGIN-LAYOUT
swimlane_id: LANE-CI-CLAUDE-PLUGIN-LAYOUT
observable_state: Preserve the repository operating import in the officially supported .claude/CLAUDE.md
  location while keeping strict native plugin validation enabled.
proof: Declared observable regressions and independent Task-Spec acceptance.
requires: []
produces:
- ci:claude-plugin-layout:verified
tasks:
- id: T-20260914-briefspec-ci-claude-plugin-layout
  title: Separate repository Claude memory from plugin-root content
  goal: Preserve the repository operating import in the officially supported .claude/CLAUDE.md location
    while keeping strict native plugin validation enabled.
  done_condition: Preserve the repository operating import in the officially supported .claude/CLAUDE.md
    location while keeping strict native plugin validation enabled.
  effort: S
  profile: full
  execution_backend: omp
  required_tools:
  - python
  - uv
  - claude
  depends_on: []
  touches_paths:
  - CLAUDE.md
  creates_paths:
  - .claude/CLAUDE.md
  behavior:
  - id: B-1
    given: the repository is opened in Claude
    when: project memory is loaded
    then: The existing OPERATING.md instructions remain reachable through .claude/CLAUDE.md with the corrected
      relative import.
  - id: B-2
    given: current Claude validates the published plugin and marketplace
    when: strict validation runs
    then: Both native validators pass without suppressing warnings, weakening strict mode, or deleting
      the repository instructions.
  evals:
  - id: eval_1
    description: strict plugin validation
    bash: claude plugin validate .claude-plugin/plugin.json --strict
    verifies:
    - B-1
    - B-2
  - id: eval_2
    description: strict marketplace validation
    bash: claude plugin validate .claude-plugin/marketplace.json --strict
    verifies:
    - B-1
    - B-2
  - id: eval_3
    description: native operating import through project memory
    bash: 'python -c ''import json,secrets,subprocess,tempfile;from pathlib import Path;fixture=tempfile.TemporaryDirectory(prefix=''"''"''briefspec-claude-memory-''"''"'');root=Path(fixture.name);(root/''"''"''.claude''"''"'').mkdir();(root/''"''"''.claude/CLAUDE.md''"''"'').write_text(Path(''"''"''.claude/CLAUDE.md''"''"'').read_text());token=secrets.token_hex(16);(root/''"''"''OPERATING.md''"''"'').write_text(Path(''"''"''OPERATING.md''"''"'').read_text()+''"''"''\nMemory
      proof token: ''"''"''+token+''"''"''\n''"''"'');result=subprocess.run([''"''"''claude''"''"'',''"''"''--print''"''"'',''"''"''Return
      only the memory proof token from the project instructions. Do not use tools.''"''"'',''"''"''--output-format''"''"'',''"''"''json''"''"'',''"''"''--tools''"''"'',''"''"''''"''"'',''"''"''--max-turns''"''"'',''"''"''1''"''"'',''"''"''--no-session-persistence''"''"'',''"''"''--setting-sources''"''"'',''"''"''project''"''"'',''"''"''--settings''"''"'',''"''"''{"autoMemoryEnabled":false}''"''"'',''"''"''--mcp-config''"''"'',''"''"''{"mcpServers":{}}''"''"'',''"''"''--strict-mcp-config''"''"''],cwd=root,capture_output=True,text=True,timeout=120);assert
      result.returncode==0,result.stderr;answer=json.loads(result.stdout)[''"''"''result''"''"''].strip();assert
      answer==token,answer;print(''"''"''Native Claude project-memory import: PASS''"''"'');fixture.cleanup()'''
    verifies:
    - B-1
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
  observability: 'Move the existing one-line repository memory file; adjust @OPERATING.md to @../OPERATING.md.
    Do not modify OPERATING.md, plugin functionality, or CI strictness. Official contract: https://code.claude.com/docs/en/memory
    .'
  rollback: Revert only the authorized source/test patch; preserve all other user files and generated
    authorization records.
source_seam_sha256: d84381c03a0b8dac6512af2f837ebd534d1ffd8cfb21613ac9d7f416ea2b90f6
---
# Preserve the repository operating import in the officially supported .claude/CLAUDE.md location while keeping strict native plugin validation enabled.

## Observable proof

Declared observable regressions and independent Task-Spec acceptance.

## Runnable leaves

- `T-20260914-briefspec-ci-claude-plugin-layout` — Separate repository Claude memory from plugin-root content: Preserve the repository operating import in the officially supported .claude/CLAUDE.md location while keeping strict native plugin validation enabled.

The leg names a capability state, not an activity. Each leaf owns one coherent,
independently provable done-condition.
