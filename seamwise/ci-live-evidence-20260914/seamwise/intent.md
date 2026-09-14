---
schema_version: 1
kind: delivery-intent
id: INTENT-BRIEFSPEC-LIVE-RELEASE-EVIDENCE
title: Refresh native release evidence for the corrected source
claim: proposed
source:
  uri: seamwise/input.json
  captured_at: '2026-09-14T21:49:41.360607+00:00'
  sha256: 9930a989f33f5f83329b5e28e773a65bd1c5f979873599c7dfdba61a49ac74a4
success:
- Regenerate source-bound release evidence from fresh native measurements of every required host; retain
  failures and authorize only complete passing coverage.
out_of_scope:
- Do not change product code, validators, required coverage, or existing user policy.
- Global candidate installation and publication are separately authorized supervisor operations, not worker
  write scope.
---
# Refresh native release evidence for the corrected source

## Delivery outcome

User explicitly selected Test candidate and refresh evidence, including local candidate installation before final CI and one HMAC-sealed release-evidence leaf.

## Evidence boundary

This artifact records a **proposed** claim. Its source, capture time,
and content hash are recorded in frontmatter; the claim is not implementation
evidence by itself.
