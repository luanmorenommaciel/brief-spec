---
schema_version: 1
kind: delivery-intent
id: INTENT-BRIEFSPEC-CI-REPAIRS
title: Correct the four explicitly authorized CI failure areas
claim: proposed
source:
  uri: seamwise/input.json
  captured_at: '2026-09-14T20:00:44.691539Z'
  sha256: d923ee2783ff048f7ba95ce5640544e6554569fba89be579483ff6b9af368db5
success:
- Write atomically when descriptor chmod is unavailable and close the temporary descriptor before failure
  cleanup.
- Create and read deterministic ZIP bytes through one owned temporary stream without reopening its live
  pathname.
- Preserve the repository operating import in the officially supported .claude/CLAUDE.md location while
  keeping strict native plugin validation enabled.
- Verify identical canonical input and each independently rendered audio artifact rather than require
  byte-identical output from separate operating-system speech generations.
out_of_scope:
- No version bump, release-tag publication, unrelated product changes, or weakening of actual artifact-integrity
  checks.
- Native speech synthesis and encoder internals are unchanged. Their hosted variation is not an unresolved
  architectural dependency of correcting a smoke assertion that exceeds the documented contract.
---
# Correct the four explicitly authorized CI failure areas

## Delivery outcome

User selected: Fix the remaining CI failures. The offered scope authorized four new sealed repair tasks, hosted-gate verification, corrected main publication, and affected installation refresh.

## Evidence boundary

This artifact records a **proposed** claim. Its source, capture time,
and content hash are recorded in frontmatter; the claim is not implementation
evidence by itself.
