---
schema_version: 1
kind: system-map
claim: derived
components:
- Atomic state and artifact writes
- Chronicle archive creation
- Claude repository memory and native plugin validation
- Optional media smoke verification
external_dependencies:
- Current native Claude CLI
- Local macOS speech, FFmpeg, Poppler, and Chromium
- Existing Task-Spec HMAC authority and explicit user approval
unknowns: []
proposed_steel_thread:
- LEG-CI-PORTABLE-ATOMIC-WRITES
- LEG-CI-PORTABLE-CHRONICLE-ZIP
- LEG-CI-CLAUDE-PLUGIN-LAYOUT
- LEG-CI-AUDIO-VERIFICATION-CONTRACT
objections: []
contentions: []
---
# System Map

## Components

- Atomic state and artifact writes
- Chronicle archive creation
- Claude repository memory and native plugin validation
- Optional media smoke verification

## External dependencies

- Current native Claude CLI
- Local macOS speech, FFmpeg, Poppler, and Chromium
- Existing Task-Spec HMAC authority and explicit user approval

## Unknowns

- (none)

This is a **derived** map. The delivery-plan transformation must
close or gate every material unknown; it may not reinterpret this text as
runtime proof.
