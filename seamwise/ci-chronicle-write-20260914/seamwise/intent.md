---
schema_version: 1
kind: delivery-intent
id: INTENT-BRIEFSPEC-CI-CHRONICLE-WRITES
title: Reuse portable public atomic writes for Chronicle outputs
claim: proposed
source:
  uri: seamwise/input.json
  captured_at: '2026-09-14T20:51:15.164840+00:00'
  sha256: bf2ebe087c905f1b8f6997e1bd08142b9a6806eb467633fbb7a0017f045ada62
success:
- Chronicle outputs succeed without os.fchmod while retaining public parent permissions, explicit overwrite
  authorization, and atomic failure cleanup.
out_of_scope:
- Do not alter the public overwrite policy or parent-directory permissions.
- No version bump, unrelated source changes, or scope expansion inside a signed leaf.
---
# Reuse portable public atomic writes for Chronicle outputs

## Delivery outcome

Proposed additional repair outside the four previously approved write scopes. Explicit human topology approval is required before sealing or implementation.

## Evidence boundary

This artifact records a **proposed** claim. Its source, capture time,
and content hash are recorded in frontmatter; the claim is not implementation
evidence by itself.
