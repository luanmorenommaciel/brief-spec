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

With `sticky = true`, the work type stays fixed until an explicit override, a clear pivot, a new
request whose main verb names another type (for example "review the folder structure" during a
release task), or a valid Outcome Brief closes the task. Nouns ("the restart path") and questions
("is this correct?") do not switch it. With `sticky = false`, every substantive prompt is
classified. Run `brief-spec eval` to measure the classifier on the bundled labeled prompts, or
`brief-spec eval your-prompts.jsonl` on your own (one JSON object per line with `prompt` and
`type`).

## Explain-back hook

`brief-spec explain` prints a diff-grounded explain-back frame for the current branch: the files
from `git diff --name-only <base>...HEAD`, the changed symbols, a file tree, and room for a 3-line
summary, one line per file, visuals, and at most three `path:line` "look here first" entries.
`--base <ref>` overrides `main`; `--format phone` drops Mermaid and tables;
`brief-spec explain --check <file>` validates a filled one and exits non-zero on any failure.

The optional Stop hook for Claude Code and Codex asks for that explain-back. It ships off:

```toml
[explain]
hook = false          # true turns it on
hook_min_files = 3    # block when the branch changes at least this many files...
hook_min_lines = 100  # ...or at least this many added plus deleted lines
```

On each stop it reads `git diff --shortstat` of the committed branch against its merge-base with
`main`. Over either threshold, it blocks the stop once and passes the explain frame back as the
reason; the next stop with the same diff passes. A new commit that changes the diff can ask once
more, and running `brief-spec explain` marks the current diff as explained. Uncommitted edits are
not counted. It never blocks when the host reports `stop_hook_active`, makes no model or network
call, gives up after 0.9 seconds of git work, and lets the stop through on any error. Kimi has no explain hook;
run the command there. The last explained diff is kept under `$BRIEF_SPEC_HOME/explain/`.

## Output channels

`brief-spec notify` reads `[channels.<name>]` tables from the same user and project config files.
A channel table may only reference environment variables; a literal URL or token makes the config
invalid.

| Key | Required | Meaning |
| --- | --- | --- |
| `kind` | yes | `slack-webhook`, `slack-bot`, `teams-workflow`, `discord`, `google-chat`, or `webhook` |
| `secret_env` | yes, except `webhook` | Environment variable holding the webhook URL or bot token; for `webhook`, the optional `whsec_` signing secret |
| `url_env` | `webhook` only | Environment variable holding the HTTPS endpoint |
| `target` | `slack-bot` | Slack channel id (for example `C0123ABCD`); for `discord`, an optional thread id |
| `template` | no | `card` (default: status, outcome, human action, gaps, next) or `full` (adds proof) |
| `when_status` | no | Statuses this channel receives with `--to all` or from the Stop hook; add `CHECKPOINT` to receive checkpoints |
| `thread_by` | no | `task` (default: follow-ups for the same task share a thread) or `none` |
| `mention` | no | Text placed above the card, for example `<@U0123ABCD>` or `@here` |

The optional `[notify]` table turns on posting from the Stop hook:

```toml
[notify]
on_stop = true
consent_network = true      # explicit, persistent consent for these channels
channels = ["eng"]
```

The hook starts a detached `brief-spec notify` process for every valid brief and never waits for
it. Sends, threads, acknowledgments, and receipts are kept under
`$BRIEF_SPEC_HOME/notify/` (default `~/.local/state/brief-spec/notify/`).

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
