---
name: outcome-brief
description: Close substantive implementation, investigation, review, or research work with a short, consistently ordered, evidence-backed handoff. Use when a task reaches a terminal outcome; when the user asks what shipped, what changed, what needs attention, or what happens next; or when a host hook requests a valid Brief-Spec outcome.
---

# Outcome Brief

<!-- briefspec:skill:v1 -->

End with one bounded brief that lets the reader recognize the outcome without rediscovering the
response structure. Preserve uncertainty and source references; do not turn formatting into proof.

## Workflow

1. Determine the honest status:
   - `DONE`: requested outcome achieved and directly verified.
   - `REVIEW`: implementation is ready for human inspection.
   - `DECIDE`: a meaningful choice is required.
   - `BLOCKED`: an external dependency prevents continuation.
   - `FAILED`: the attempt did not achieve the requested outcome.
2. State what is now true, not the activity performed.
3. Identify the smallest human action. Use `None` when no action is required.
4. Cite up to five inspectable proof references. Prefix each with its evidence basis and result:
   `[direct|derived|reported]/[pass|fail|info]`.
5. State gaps without softening them.
6. Give no more than three next actions and three open items.
7. Render the fields exactly in the order below.
8. If available, run `brief-spec validate outcome -` before returning the brief.

## Required output

```markdown
<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: DONE
Outcome: One sentence describing what is now true.
Human action: None

Proof:
- [direct/info] `path/to/file:line` — what it proves
- [direct/pass] `command` → observed result

Gaps:
- None

Next:
- The next useful move, or None

Open:
- None
<!-- /briefspec -->
```

Keep `None` explicit; never omit a field in the full form. A `DONE` brief cannot contain required
human action or unresolved gaps, needs at least one `[direct/pass]` proof that you observed
yourself, and cannot include any `fail` proof; use `REVIEW` when the evidence is only derived or
reported. `REVIEW`, `DECIDE`, and `BLOCKED` require human action. `BLOCKED` and `FAILED` require a
gap and a next action. `DECIDE` requires an open decision; write it as a decision card:
`Options: A; B — Recommendation: A — Reversible: yes — Needed by: <date>`.

When the result depends on named inputs, you may end with an optional `Sources:` list
(`- name: ok|stale|missing|error`, plus `(optional)` or `— note`). Every non-`ok` source counts as a
gap, and a required one blocks `DONE`; see [references/contract.md](references/contract.md).

## Compact form

A `DONE` result with no human action, gaps, next steps, or open items may use the compact form.
It is read exactly as the full form with those four fields set to `None`. Every other status needs
the full form.

```markdown
<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: DONE
Outcome: One sentence describing what is now true.
Proof: [direct/pass] `command` → observed result
<!-- /briefspec -->
```

Use it for short answers. If anything is left for the human, write the full form.

## Phone chat form and fenced storage

The fenced brief above is the storage form. Files, exports, hooks, and `brief-spec validate`
read it. A coding host with a Brief-Spec hook still needs it as the terminal message.

When the reader is on a phone chat surface, send the four-line chat form instead and keep the
fenced brief in storage. Never paste `<!-- briefspec` fences, the typed wrapper, or a leading
table into chat.

```markdown
**Now:** What is now true (Outcome).
**You:** The smallest human action, or None.
**Not yet:** What is still missing (Gaps), or None.
**Done:** Who holds Done.
```

- `Done` names who closes the work. Use `Agent, verified.` for `DONE`, `You, after review.` for
  `REVIEW`, `You, once you decide.` for `DECIDE`, `Agent, once unblocked.` for `BLOCKED`, and
  `Agent, after another attempt.` for `FAILED`.
- Redirect form: when another owner holds Done, such as a tracker, a reviewer, or another hop,
  write `**You:** Open {Owner}.` and `**Done:** {Owner}.`
- Keep each label on one line. Join several gaps with `; `.
- `brief-spec export --formats phone` renders this form from a stored brief.
- If available, run `brief-spec validate --format phone -` before sending. It fails on leaked
  fences and leading tables.

## References

Read [references/contract.md](references/contract.md) when status selection or evidence strength is
ambiguous. Read [references/examples.md](references/examples.md) when a concrete rendering example
would help.
