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
2. On Grok Build, use the native passive hooks. Do not run the classifier from the model.
3. On other harnesses, when available, run `brief-spec classify - --json` with only the bounded current task text.
4. Use `general` when signals conflict or remain ambiguous.

### Subject vocabulary

Built-in subjects: `pull-request`, `codebase`, `change-set`, `issue`, `bug`, `feature`, `refactor`, `test`, `release`, `architecture`, `document`, `data`, `incident`, `dependency`, `security`, and `general`.

Subjects remain open normalized slugs, so an explicit `--subject migration-plan` is valid even though it is not built in.

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

| Harness | Command | Destination |
| --- | --- | --- |
| Codex | `brief-spec setup codex` | `.codex/`, `.agents/skills/` |
| Claude Code | `brief-spec setup claude` | `.claude/skills/` |
| OMP | `brief-spec setup omp` | `.agents/skills/` |
| Grok Build | `brief-spec setup grok` | `.grok/skills/` |
| Kimi Code | `brief-spec setup kimi` | `.agents/skills/` |
| Copilot | `brief-spec setup copilot --scope project` | `.agents/skills/`, `.github/` |

Cursor Agent and Goose are experimental. Cursor paths are implemented but unpublished.

The installer merges lifecycle hooks instead of replacing the host file, refuses to overwrite foreign skill files, restores prior files if installation fails, and records what it owns.
