---
schema_version: 1
kind: delivery-intent
id: INTENT-RELEASE-BADGE-FORMAT
title: Correct the authorized release verification gate
claim: proposed
source:
  uri: seamwise/input.json
  captured_at: '2026-09-14T17:39:02.696482Z'
  sha256: a328d29ab385b94232dbf0b7a68b9ee9ff8b2b802bfe645cf285b1c21fec9434
success:
- HTML and Markdown candidate badges accept matching advertised versions and reject mismatches.
out_of_scope:
- No README redesign, version change, or unrelated product behavior change.
---
# Correct the authorized release verification gate

## Delivery outcome

User explicitly selected: Fix the verifier, then publish. Authorized scripts/verify-release.py and tests/test_release_verification.py, engine-managed seal, and regression checks. README and advertised version remain unchanged.

## Evidence boundary

This artifact records a **proposed** claim. Its source, capture time,
and content hash are recorded in frontmatter; the claim is not implementation
evidence by itself.
