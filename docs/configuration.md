# Configuration

Create a user or project configuration:

```bash
brief-spec config init
brief-spec config init --scope project
brief-spec config show --json
```

User configuration lives under the Brief-Spec state root. Project configuration is
`.brief-spec.toml`. The legacy `.briefspec.toml` remains readable. Only known presentation,
typing, and retention keys are read; configuration cannot inject
commands or arbitrary executable paths.

## Defaults

```toml
[checkpoint]
policy = "suggest" # off | manual | suggest | auto
default_mode = "orient" # orient | teach | spoken
elapsed_minutes = 12
turns = 8
assistant_chars = 16000
tool_calls = 12
cooldown_minutes = 6
minimum_turns_after_checkpoint = 2

[outcome]
policy = "suggest" # off | suggest | enforce
one_repair = true

[typing]
enabled = true
activation = "substantive"
default_type = "general"
sticky = true

[state]
retention_days = 14
```

### Checkpoint policies

- `off`: no lifecycle checkpoint behavior; explicit skill use remains possible.
- `manual`: only explicit checkpoint requests.
- `suggest`: record eligibility and give the model one suggestion per window at a tool boundary.
  The window restarts at every valid checkpoint or Outcome Brief.
- `auto`: request one checkpoint at the next agent-stop boundary.

### Outcome policies

- `off`: no lifecycle outcome behavior; explicit skill use remains possible.
- `suggest`: install the contract as session context.
- `enforce`: conservatively classify action requests and request one correction when the terminal
  handoff lacks a valid Outcome Brief.

Enforcement is intentionally opt-in. A stop hook cannot perfectly infer whether every conversational
turn is a terminal task boundary.

Under every policy, each stop validates the terminal message and records the result in session
state. Claude Code also shows a one-line warning when a brief is present but invalid. A compact
`DONE` Outcome (Status, Outcome, Proof) is valid, and `enforce` does not ask to wrap it.

### Thresholds and typing

`elapsed_minutes`, `turns`, `assistant_chars`, and `tool_calls` are measured since the last valid
checkpoint or Outcome Brief, not since the session started. `cooldown_minutes` and
`minimum_turns_after_checkpoint` still apply after a checkpoint.

With `sticky = true`, the work type stays fixed until an explicit override, a clear pivot, or a valid
Outcome Brief closes the task. With `sticky = false`, every substantive prompt is classified.

## State operations

```bash
brief-spec state list --json
brief-spec state prune --older-than 14
brief-spec state prune --older-than 14 --dry-run
brief-spec state reset --runtime codex --session SESSION_ID
```

`state list` includes `last_brief_kind`, `last_brief_valid`, `last_brief_status`, and
`last_brief_errors` for each session, plus running `briefs_validated` and `briefs_invalid` counts.

Set `BRIEF_SPEC_HOME` to isolate state for automation or testing. Brief-Spec stores bounded metadata,
not raw session content. `BRIEFSPEC_HOME` remains a readable `0.x` compatibility alias.
