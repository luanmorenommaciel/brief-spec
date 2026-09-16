# Brief-Spec Skills

Brief-Spec exposes three portable skills that chat agents can discover and use. The CLI validates; the skills are how a chat agent finds the contract.

## brief-spec

**Purpose:** Classify substantive coding-agent work and shape the full explanation for the selected profile.

**When:**

- A task begins or clearly pivots
- The user asks Brief-Spec to explain work
- A Brief-Spec lifecycle hook supplies a type decision

**Not:**

- Sending task text to another model or network service
- Inventing Grok classification metadata (Grok's native passive hooks record the deterministic decision)

**Gate:** `brief-spec classify`

### Eight work types

| Type | Explanation order |
| --- | --- |
| `general` | Answer, rationale, next action |
| `exploration` | Question, system map, entry points, flow, unknowns, next probe |
| `review` | Scope, verdict, findings, risk, validation, recommendation |
| `implementation` | Intent, changes, resulting behavior, verification, tradeoffs |
| `debugging` | Symptom, root cause, fix, regression protection, residual risk |
| `planning` | Goal, decisions, approach, sequence, gates |
| `research` | Question, synthesis, evidence quality, limitations, recommendation |
| `operations` | Event, impact, current state, actions, recovery, follow-up |

### Classification precedence

1. Honor an explicit type from the user or host.
2. On Grok Build, use the native hooks. Do not run the classifier from the model. The decision
   reaches the model after the first tool call, or in a single Stop correction. Put profile
   sections and the Outcome Brief or Session Checkpoint in the same first response; do not wait
   for Stop to append the profile after the brief.
3. On other harnesses, when available, run `brief-spec classify - --json` with only the bounded current task text.
4. Use `general` when signals conflict or remain ambiguous.
5. A request verb outweighs a noun that only mentions other work. Two request verbs of
   different types still tie and fall back to `general`.

A valid Outcome Brief closes the task, and the next substantive request is classified afresh.
Background task notifications, system reminders, and hook feedback never count as a request.

### Subject vocabulary

Built-in subjects: `pull-request`, `codebase`, `change-set`, `issue`, `bug`, `feature`, `refactor`, `test`, `release`, `architecture`, `document`, `data`, `incident`, `dependency`, `security`, and `general`.

Subjects remain open normalized slugs, so an explicit `--subject migration-plan` is valid even though it is not built in.

When a prompt mentions several subjects, the classifier prefers the one that fits the chosen type.
"Implement the login feature and write tests" is `implementation + feature`, not `+ test`.
Portuguese prompts use the same types and subjects.

---

## outcome-brief

**Purpose:** Close substantive implementation, investigation, review, or research work with a short, consistently ordered, evidence-backed handoff.

**When:**

- A task reaches a terminal outcome
- The user asks what shipped, what changed, what needs attention, or what happens next
- A host hook requests a valid Brief-Spec outcome

**Not:**

- Turning formatting into proof
- Claiming DONE with required action or unresolved gaps

**Gate:** `brief-spec validate outcome`

### The contract

Seven fields in fixed order:

```text
Status → Outcome → Human action → Proof → Gaps → Next → Open
```

### Five statuses

| Status | Meaning | Constraints |
| --- | --- | --- |
| `DONE` | Requested outcome achieved and directly verified | No required action, no unresolved gaps |
| `REVIEW` | Implementation ready for human inspection | Requires human action |
| `DECIDE` | A meaningful choice is required | Requires human action and an open decision |
| `BLOCKED` | External dependency prevents continuation | Requires a gap and a next action |
| `FAILED` | The attempt did not achieve the requested outcome | Requires a gap and a next action |

### Compact form

A `DONE` result with no human action, gaps, next steps, or open items may keep only Status,
Outcome, and Proof. The missing fields are read as `None`, so the canonical object equals the full
form. Every other status needs all seven fields.

```markdown
<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: DONE
Outcome: The parser now accepts empty input.
Proof: [direct/pass] `uv run pytest tests/test_parser.py` → 12 passed
<!-- /briefspec -->
```

### Proof evidence

Proof items are prefixed with evidence basis and result:

```text
[direct|derived|reported]/[pass|fail|info]
```

- **direct**: Evidence observed firsthand
- **derived**: Evidence computed or inferred from direct evidence
- **reported**: Evidence from an external source

Up to five inspectable proof references. Each must retain an inspectable locator (file path, command, URL, etc.).

### Validation

```bash
brief-spec validate outcome path/to/handoff.md --json
brief-spec validate outcome -  # read from stdin
```

See [`schemas/`](../schemas/) for the machine-readable contracts.

---

## session-checkpoint

**Purpose:** Re-orient a long, dense, or interruption-prone agent session without replacing the underlying evidence.

**When:**

- The user asks for a recap, orientation, simple teaching explanation, or spoken summary
- A session has accumulated many turns or tool calls
- Before context compaction
- A Brief-Spec hook says a checkpoint is eligible

**Not:**

- Treating spoken mode as audio generation
- Claiming the checkpoint is canonical project memory
- Silently ingesting the checkpoint into Nexo, Obsidian, or another knowledge system

**Gate:** `brief-spec validate checkpoint`

### Three modes

| Mode | Purpose | Length |
| --- | --- | --- |
| `orient` | 30–45 second operational scan | Where we are, what changed, next move |
| `teach` | Plain-language mental model | What we did, why it works, example, watch-outs |
| `spoken` | Sequential script designed to be heard | 80–240 words |

Use `orient` when no mode is requested.

### Eligibility and cooldown

Time or interaction volume can make a checkpoint eligible. They do not force an interruption. Brief-Spec delivers an automatic checkpoint only when the host reaches a lifecycle boundary.

Volume is counted inside a window that restarts at every valid checkpoint or Outcome Brief, so a
long session does not stay eligible forever. Under the default `suggest` policy the model gets one
suggestion per window, not one every few minutes.

Configurable thresholds:

- `elapsed_minutes`: Minutes since session start or last checkpoint
- `turns`: Number of conversation turns
- `assistant_chars`: Characters in assistant responses
- `tool_calls`: Number of tool invocations
- `cooldown_minutes`: Minimum time between checkpoints
- `minimum_turns_after_checkpoint`: Minimum turns before next checkpoint

### Validation

```bash
brief-spec validate checkpoint path/to/checkpoint.md --mode orient
brief-spec validate checkpoint path/to/checkpoint.md --mode teach
brief-spec validate checkpoint path/to/checkpoint.md --mode spoken
```

### Invariants

- A checkpoint is never more authoritative than its source
- Keep at least one inspectable proof reference outside the spoken script
- Never claim a checkpoint is canonical project memory
- Do not repeat a valid final Outcome Brief with an automatic orient checkpoint

---

## Skill installation

Skills are installed to host-specific destinations by `brief-spec setup`:

| Harness | Command | Project skills destination |
| --- | --- | --- |
| Codex | `brief-spec setup codex` | `.agents/skills/` |
| Claude Code | `brief-spec setup claude` | `.claude/skills/` |
| OMP | `brief-spec setup omp` | `.omp/skills/` |
| Grok Build | `brief-spec setup grok` | `.grok/skills/` |
| Kimi Code | `brief-spec setup kimi` | `.kimi-code/skills/` |
| Copilot | `brief-spec setup copilot --scope project` | `.agents/skills/` |
| Cursor Agent | `brief-spec setup cursor` | `.cursor/skills/` |
| Goose | `brief-spec setup goose` | `.agents/skills/` |

User scope writes to each host's own skills directory, for example `~/.codex/skills/` or
`~/.kimi-code/plugins/managed/brief-spec/skills/`. Copilot, Cursor Agent, and Goose are
experimental.

The installer merges lifecycle hooks instead of replacing the host file, refuses to overwrite foreign skill files, restores prior files if installation fails, and records what it owns.
