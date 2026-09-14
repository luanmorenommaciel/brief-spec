# Verified delivery

Brief-Spec treats downloads as projections of one canonical object, not as
separate model responses.

```text
bounded host output
  -> canonical brief-spec-delivery/2.0 object
  -> validation and safe evidence resolution
  -> Markdown, JSON, HTML, or optional renderer
  -> deterministic bundle manifest
  -> external delivery receipt
```

## Canonical envelope

The envelope keeps the existing Outcome Brief and Session Checkpoint `1.0`
objects unchanged under `brief`. It adds deterministic `classification` and ordered
`explanation.sections`, while `source` records the harness, adapter, model, and host boundary;
`provenance` can identify Exa, Tavily, Firecrawl, local files, or another
provider without importing a provider SDK; `artifacts` describes evidence
objects; and `work_items` carries explicit multi-agent activity.

Brief-Spec exports only the bounded region. Raw transcripts, authentication
state, and host resume tokens are not canonical fields and cannot leak through
the renderer pipeline by default.

## Determinism

Canonical time is captured once from an explicit `--created-at`,
`SOURCE_DATE_EPOCH`, source-file modification time, or finally the current UTC
time. Renderers reuse that value. ZIP member ordering, timestamps, modes,
compression, and manifest serialization are fixed, so the same canonical
object produces identical bundle bytes.

The bundle contains generated files and `manifest.json`. The adjacent receipt
template is intentionally outside the archive. `deliver` replaces that
template with a receipt containing the actual destination, delivery time, and
delivered-byte hash.

## Verification

`structural` verifies schemas and manifests. `resolved` additionally checks
local files and Git commit objects. Network access is off by default; public
HTTP(S) locators are checked only with `--consent-network`. Command evidence is
never executed. Paths are contained within the declared workspace unless
`--allow-outside-workspace` is explicit.

Consented URL verification resolves every hop before connection and rejects
loopback, private, link-local, multicast, unspecified, reserved, metadata, and
mixed public/private DNS answers. It caps redirects, requests, headers, body
hashing, and time. Reachability is reported separately from a matching content
hash and is never promoted into proof merely because an artifact says it is
public. Environment proxies are not used.

`rendered` verifies the output itself: embedded canonical JSON and restrictive
CSP for HTML; Poppler text, fonts, metadata, geometry, and rendered-page hash
for PDF; or `ffprobe` codec, duration, disclosure, and source-Script hash for
MP3. Core ZIP members must be deterministic byte renderings of the embedded
canonical JSON. `delivered` verifies the external receipt against its local
destination.

Independent speech generations need not produce identical MP3 bytes. Each audio
artifact must satisfy its own integrity, Script provenance, codec, duration, and
disclosure checks. The audio release smoke compares canonical JSON across export
and bundle, then verifies both rendered outputs; it does not require two speech
generations to have identical bytes.

Offline URL checks remain visibly declared but unresolved instead of being
promoted to passing evidence. Bundles are limited to 64 MiB, 128 members, 64
MiB per expanded member, 256 MiB total expanded bytes, and a 100:1 compression
ratio. Traversal, absolute paths, duplicate names, links, and special members
fail before extraction. Local evidence hashing is capped at 256 MiB unless
`--allow-large-artifact` is explicit.

## Optional renderers

Renderer packages register under the canonical `brief_spec.renderers` Python entry-point
group and the legacy `briefspec.renderers` group. The interface exposes capabilities, setup,
render, and verify behavior.
Core verification does not discover or import renderer code by default.
`--allow-plugins` is required for a target that declares PDF or audio, and
verification loads only the requested official renderer name with matching
core major/minor compatibility. Renderer distribution, version, and entry-point
group are retained in artifact metadata. Installing or allowing a renderer is
therefore an explicit trust decision for installed Python code.

PDF uses the exact self-contained HTML as its source. Audio reads only a Spoken
Checkpoint Script. Local macOS speech never falls back to OpenAI; OpenAI speech
requires an explicit provider, network consent, and runtime credential. The
OpenAI implementation follows the current
[text-to-speech guide](https://developers.openai.com/api/docs/guides/text-to-speech)
with `gpt-4o-mini-tts` and the recommended `marin` voice; model and voice remain
explicitly configurable and are recorded with the artifact.

## Chronicle and video boundary

`brief-spec-chronicle` uses the same canonicalization, hashing, atomic-write, manifest, and receipt
principles, but its source object is a sealed project snapshot rather than a task delivery envelope.
Its Markdown, JSON, HTML, ZIP, optional PDF, and optional audio outputs are projections of that one
snapshot. Chronicle never activates merely because the global tools are installed; each project
requires explicit initialization.

The experimental `brief-spec-renderer-video` package renders Chronicle storyboards with offline
assets, captions, chapters, source hashes, and a recorded renderer fingerprint. Video is optional,
is not a core release gate, and promises byte identity only for an identical renderer fingerprint.
See [Human Continuity Fabric and Project Chronicle](human-continuity.md) for the authority, privacy,
retention, and reviewed-learning boundaries.
