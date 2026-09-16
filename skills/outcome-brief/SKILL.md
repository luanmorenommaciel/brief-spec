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
human action or unresolved gaps. `REVIEW`, `DECIDE`, and `BLOCKED` require human action. `BLOCKED`
and `FAILED` require a gap and a next action. `DECIDE` requires an open decision.

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

## References

Read [references/contract.md](references/contract.md) when status selection or evidence strength is
ambiguous. Read [references/examples.md](references/examples.md) when a concrete rendering example
would help.
