# Outcome contract

## Status decision

Choose the strongest status supported by current evidence, not the most optimistic label.

- `DONE` requires direct verification of the requested outcome.
- `REVIEW` means the implementation is complete enough for inspection but acceptance remains human.
- `DECIDE` means implementation should not continue until a choice is made.
- `BLOCKED` means the same objective remains active but an external condition prevents progress.
- `FAILED` means the attempted route did not achieve the objective.

## Evidence strength

Prefer, in order:

1. Direct runtime observation tied to an exact command, artifact, URL, commit, or file location.
2. Derived evidence whose inference is stated.
3. Reported evidence explicitly labeled as reported.

A passing syntax check does not prove a live integration. A local commit does not prove publication.
A planned file does not prove implementation. Keep these boundaries visible in `Proof` and `Gaps`.

## Compression rules

- Lead with outcome and required human action.
- Use at most five proof items.
- Keep each proof item independently inspectable.
- Start each item with `[basis/result]`, where basis is `direct`, `derived`, or
  `reported`, and result is `pass`, `fail`, or `info`.
- Link to logs or artifacts instead of copying them.
- Do not hide an unresolved risk in `Next`; put it in `Gaps` or `Open`.
- Do not repeat the full narrative from the response.

## Compact DONE

The compact form (Status, Outcome, Proof) is valid only for `DONE`. Omitted Human action, Gaps,
Next, and Open are read as `None`, so the canonical object equals the full form. A compact brief
that hides a gap is a false claim, not a shorter one; use the full form whenever something is left.
