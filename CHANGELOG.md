# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses semantic versioning.

## [Unreleased]

### Added

- Compact Outcome Brief for honest `DONE` results: Status, Outcome, and Proof only. The omitted
  Human action, Gaps, Next, and Open fields are read as `None`, so the canonical object equals the
  full form. Every other status still needs all seven fields, and `enforce` does not ask to wrap a
  compact brief in the typed region.
- Every stop now validates the terminal message under every policy and records the brief kind,
  validity, status, first errors, and running valid/invalid counts in session state. Claude Code
  shows a one-line `systemMessage` warning when a brief is present but invalid.
- `brief-spec doctor codex` reproduces Codex's hook-approval hash and reports each Brief-Spec hook
  as approved, not yet reviewed, or changed since review. Codex skips unapproved hooks without an
  error, and `codex exec` never shows the review screen.
- Grok receives the classification through a `PreToolUse` hook, once per decision, because it
  discards prompt-hook output. A Stop correction counts as delivery. The hook never returns a
  permission decision.
- Copilot receives the classification on the first `postToolUse` of each classified task, because
  command hooks cannot add context from `userPromptSubmitted`.
- Portuguese classification rules for all eight work types and the built-in subjects, including
  Portuguese negation masking.
- A `session_end` event. Grok's observe-only Stop at session end (`channel_closed`, `shutdown`) maps
  to it and changes no state.
- `tests/test_reading_experience.py`, including a labeled everyday-prompt corpus.

- `brief-spec frame` with versioned request and receipt schemas for bounded,
  presentation-only Human Frames. Lifecycle coordinators can delegate Markdown
  rendering without delegating approval or dispatch authority.

- Experimental `brief-spec-chronicle` package with explicit per-project activation, private
  append-only material-event segments, idempotent ingestion, hash-chain receipts, a rebuildable
  SQLite relation index, deterministic drift rules, canonical Project Chronicle snapshots, Human
  Review Packs, transactional exports, external receipts, archive/restore, doctor, exact deletion,
  and reviewed lesson export.
- Public `brief-spec-event/1.0` schema and dependency-free artifact primitives for canonical JSON,
  hashing, atomic output sets, manifests, and receipts.
- Method contexts for Seamwise, Task-Spec, Converge, and general work, kept independent from work
  type, subject, presentation, and lifecycle horizon.
- Harness capability reporting for Human Frame delivery tiers.
- Bounded source normalization for Brief-Spec delivery, Seamwise, Task-Spec, Converge, Exa,
  Tavily, Firecrawl, and RAFT records, plus correlation-based cross-harness deduplication.
- Ingest-order replay across monthly segments, explicit ledger cutoffs, visible late arrivals,
  human-approved pivot baselines, expanded deterministic drift rules, and a disposable
  Seamwise → Task-Spec → Converge end-to-end journey.
- Generic HTML-to-PDF and spoken-script-to-MP3 helpers that preserve the existing renderer
  contracts while allowing Chronicle to reuse their verified engines.
- Experimental `brief-spec-renderer-video` package for offline storyboard scenes, H.264/AAC MP4,
  captions, transcript, chapters, source hashes, and renderer-fingerprint-scoped determinism.
- Comprehensive behavior catalog covering all eight work types, four reading experiences, six
  continuity horizons, method-aware Human Frames, Outcome states, evidence edge cases, downloads,
  harnesses, Chronicle operations, and explicit non-goals.
- Enforced repository-layout contract that requires canonical package-directory/distribution name
  alignment while preserving the intentional `0.x` import and entry-point compatibility surfaces.

### Changed

- Classifier adapter `1.2`: an explicit request verb weighs twice as much as a noun that only
  mentions other work, so "find the root cause after the last deploy" is debugging and "deploy the
  new build" is operations. Two request verbs of different types still abstain to `general`.
  "Now that X is done," clauses are read as background. A labeled corpus of 31 everyday prompts
  moved from 20 to 31 correct types and from 20 to 30 correct subjects; all 40 live-harness
  prompts keep their expected classification.
- Subject selection prefers the subject that fits the chosen type, accepts plurals, and no longer
  reads the word "table" as data work.
- A valid Outcome Brief closes the task. The decision stays recorded, a plain follow-up receives no
  guidance, and the next substantive prompt is classified afresh. A soft cue such as "now that" or
  "moving on" switches the type only when the new prompt classifies as a different, non-fallback
  type.
- Full classification guidance is sent once per context window; later prompts receive a one-line
  reminder with the sections and exact typed marker. Session start and compaction reset the window.
  OMP and Grok always receive the full text.
- Checkpoint time and volume are measured since the last valid checkpoint or Outcome Brief instead
  of since session start. Under `suggest`, the model receives one suggestion per window rather than
  one every cooldown period.
- The session context is one paragraph without a mid-sentence line break and mentions the compact
  form.
- Method context needs a product reference. The ordinary verb "converge" no longer selects the
  Converge frame; `taskmesh` now selects Task-Spec.
- OMP capabilities no longer list `agent_end`, which the extension does not register.
- README, installation, compatibility, architecture, configuration, skills, examples, repository
  layout, and contributing docs now match the installed behavior and paths.

- Renamed the PDF and audio renderer source directories to
  `packages/brief-spec-renderer-pdf` and `packages/brief-spec-renderer-audio`; their legacy internal
  Python modules and `briefspec.renderers` entry points remain operational through `0.x`.
- Expanded the README feature map, Human Continuity matrix, repository map, complete candidate
  installation, documentation index, development gates, and honest limits to match the current
  source tree.
- Documented the tracked multi-model `output/` corpus separately from runtime exports and defined
  the ownership and cleanup policy for tracked release inputs, local evidence, builds, and caches.

### Fixed

- Host-inserted prompt text no longer drives the reading frame. Claude Code passes background task
  notifications through the prompt hook; their words could reclassify the task, select a method,
  or turn "orient checkpoint" into an explicit checkpoint request. Notifications, system
  reminders, slash-command echoes, and hook feedback are now removed first, and a prompt made only
  of them is not a user turn.
- The router skill's typed-marker example now includes `decision_id`, which the hook requires to
  accept the wrapper.
- The typed review example in `docs/examples.md` used `##` headings that the parser rejects.
- The README first journey ran `brief-spec types` after installing `v0.2.0`, which ships only the
  `briefspec` command and has no `types` command.
- Project destinations for OMP (`.omp/`) and Kimi (`.kimi-code/skills/`) were documented as
  `.agents/skills/`; the Cursor and Goose rows had no command.
- The Copilot cloud README and architecture doc used legacy `briefspec` file names, and the
  installation guide referenced a `v0.5.0` tag that does not exist.

- Atomic writes now support Windows Python versions without `os.fchmod` and close the temporary
  descriptor before cleaning up a failed permission change, preserving the original destination
  and error.
- Chronicle ZIP creation and reading now share one owned temporary stream, avoiding Windows
  pathname-sharing failures while preserving deterministic archive contents.
- Repository Claude instructions now live in `.claude/CLAUDE.md`, retaining the `OPERATING.md`
  import without triggering strict plugin-root validation warnings.
- Optional-renderer smoke checks now compare canonical JSON between export and bundle and verify
  each MP3 independently, rather than requiring separate speech generations to be byte-identical.
  PDF byte-identity and rendered-artifact integrity checks remain enforced.
- Made path-rendering and POSIX-permission tests platform-aware so Windows validates native path
  behavior without pretending that Unix mode bits are enforceable.
- Chronicle doctor now reports the Windows permission boundary explicitly instead of producing a
  permanent false warning, while retaining `0700`/`0600` enforcement on POSIX systems.
- The hosted macOS audio gate now installs `ffmpeg`/`ffprobe` before rendering and verification.
- Installation snapshots now retain full-stack rollback commands when Chronicle and video wheels
  are present, including restoration of the separate Chronicle executable.
- OMP integration now injects the classification context through the turn system prompt
  (`before_agent_start` → `systemPrompt`) instead of a hidden transcript message, primes the model
  with session context, and enforces the terminal Outcome/typed wrapper at `session_stop`. The dead
  `agent_end` block path and the unmapped `SessionStop` event were removed.
- Grok Stop no longer continues a turn that already has a valid Outcome Brief or Session
  Checkpoint. Grok paints that message and opens suggested-question chips plus the follow-up
  queue before Stop runs; a continuation held the queue, wiped the chips, and could send the
  wrong next prompt. Stop still supplies one classification repair when the brief itself is
  missing.
- Grok Stop no longer forces an Outcome Brief onto a non-substantive follow-up. Sticky work
  type from an earlier review was wrapping a literal `PINEAPPLE` reply as a codebase review.

### Security

- Chronicle is disabled until explicit project initialization, writes only below
  `$BRIEF_SPEC_HOME/chronicles`, rejects credential/transcript/prompt/tool-output fields before
  persistence, bounds events to 64 KiB, and requires an exact project ID for deletion.
- Lesson approval creates an offline proposal export only; it cannot modify a knowledge system,
  method, skill, policy, or canonical project state.

## [0.5.0] - Unreleased candidate

### Added

- Canonical Brief-Spec identity: `brief-spec` distribution and CLI plus the
  `brief_spec` Python package, while preserving all `briefspec` 0.x aliases.
- Eight deterministic work-type profiles and open subject slugs with local,
  bounded classification; explicit, host, inferred, and fallback origins;
  categorical confidence; sticky task behavior; and pivot handling.
- `types list`, `types show`, and `classify` CLI commands, plus the universal
  `brief-spec` routing skill.
- Typed Markdown wrapper and `brief-spec-delivery/2.0` canonical schema with
  classification, ordered explanations, harness/model metadata, provenance,
  artifacts, work items, manifests, and receipts.
- Data-driven harness registry and native user/project installers for Codex,
  Claude Code, OMP, Grok Build, and Kimi Code; experimental adapters for
  Copilot, Cursor Agent, and Goose.
- OMP native extension lifecycle and Kimi user-plugin integration, including
  the project-scope skills-only capability boundary.
- Canonical `brief-spec-delivery/2.0` exports for deterministic Markdown,
  JSON, self-contained offline HTML, ZIP, spoken text, and SSML, with optional
  PDF and MP3 renderer packages.
- Provider-neutral provenance for Exa, Tavily, Firecrawl, local files, and
  future research systems without adding their SDKs to the core package.
- Ordered bundle manifests, external delivery receipts, SHA-256 integrity,
  fixed ZIP metadata, and structural, resolved, rendered, and delivered
  verification levels.
- Portable Outcome Brief, Session Checkpoint, evidence, delivery, manifest,
  and receipt schemas plus a self-contained offline compound schema bundle.
- Sanitized, source-fingerprinted live-host evidence and exact-SHA release
  authorization inputs for build-once publication.
- Browser, PDF, local-audio, clean-wheel, clean-sdist, rollback, hermetic-host,
  and cross-harness live acceptance gates.

### Changed

- The unpublished `0.3.0` delivery and `0.4.0` renderer candidates are folded
  into this release; no intermediate packages will be published.
- Canonical state uses `BRIEF_SPEC_HOME` and `~/.local/state/brief-spec`, while
  the legacy variable, state, markers, receipts, schemas, and renderer entry
  point group remain readable through the `0.x` line.
- Optional renderer distribution names are now `brief-spec-renderer-pdf` and
  `brief-spec-renderer-audio`, version-aligned at `0.5.0`.
- Human-facing Markdown, HTML, and PDF projections are status-first while the
  unchanged Outcome Brief and Session Checkpoint `1.0` contracts remain
  backward compatible.
- Verification is zero-network and no-plugin by default. Public URL checks and
  renderer code loading require explicit consent.
- Inferred classifications are capped at medium confidence; ambiguous or
  conflicting intent falls back to `general` instead of fabricating certainty.
- Harness maturity is evidence-based: Codex, Claude Code, OMP, Grok Build, and
  Kimi Code pass their required local live matrices; Copilot, Cursor, and Goose
  remain explicitly experimental.

### Fixed

- Grok Build now accepts native camelCase assistant payloads, obtains exact
  classification metadata through one bounded Stop-hook repair, and uses the
  actual `read_file`/`search_replace` native tool IDs for its disposable
  implementation gate.
- Receipt ownership now requires the recorded path and prior hash. Locally
  modified managed files are preserved and staged beside the new candidate
  rather than overwritten.
- Runtime auto-detection follows explicit payload, stable session identifier,
  mutually exclusive host markers, then deterministic fallback precedence.
- Doctor and installer tests no longer depend on optional host executables from
  the maintainer's real `PATH`.

### Security

- Resolved verification rejects loopback, private, link-local, metadata,
  multicast, unspecified, redirected-private, and other non-public network
  targets unless the relevant operation is explicitly permitted.
- File and archive verification now bounds input size, member count, expanded
  size, compression ratio, redirects, requests, headers, and fetched bodies.
- Path traversal, absolute archive members, duplicate names, special files,
  symlink escapes, command-like evidence, and silent workspace escapes are
  rejected.
- Hook input, transcript tails, session state, and repair behavior remain
  bounded; secrets and raw transcripts are excluded from artifacts and
  receipts.

## [0.4.0] - Unpublished candidate folded into 0.5.0

### Added

- Optional `briefspec-renderer-pdf` package using canonical offline HTML and Playwright Chromium,
  with A4/Letter output and Poppler-backed verification.
- Optional `briefspec-renderer-audio` package for local macOS `say` plus `ffmpeg` MP3 output and
  explicitly consented OpenAI text-to-speech.
- Renderer discovery through the `briefspec.renderers` Python entry-point group.
- Linux PDF/browser and macOS local-audio end-to-end CI jobs, plus mocked OpenAI boundaries.

## [0.3.0] - Unpublished candidate folded into 0.5.0

### Added

- Canonical `briefspec-delivery/1.0` envelope with source metadata, provenance, artifacts, and
  multi-agent work activity.
- Deterministic Markdown, JSON, self-contained HTML, and ZIP downloads generated from one object.
- External delivery receipts and structural, resolved, rendered, and delivered verification.
- `export`, `bundle`, `verify`, `deliver`, `setup`, and `capabilities` CLI commands.
- Atomic multi-runtime setup plus doctor repair, all-scope inventory, and version-drift checks.
- PyPI Trusted Publishing workflow, release manifest, restart-safe digest checks, and staged
  GitHub Release finalization.

### Changed

- Rich evidence annotations can include a safe `kind=` hint while legacy annotations remain valid.
- Spoken text and SSML are exportable only from a Spoken Checkpoint.

## [0.2.1] - Unpublished candidate from 2026-08-03

### Fixed

- Codex project hook commands resolve the Brief-Spec bundle from the Git root, so all lifecycle
  events continue to work when a task starts in a nested repository directory.
- Codex project installs include a PowerShell `commandWindows` override with the same root-stable
  behavior.
- The README badge, installation command, and verification record now stay aligned with the
  package version.
- Session Checkpoint JSON fields now match all Orient, Teach, and Spoken human-facing contracts.
- Markdown validation rejects proof without an inspectable locator and warns when evidence basis
  and result labels are missing.
- Repeat installation upgrades unchanged receipt-owned skill references while preserving
  independently modified files.

### Added

- Executable nested-directory regression coverage for Codex project hooks.
- A tag-driven release workflow that builds once, verifies the wheel, records SHA-256 checksums,
  generates GitHub build provenance, and attaches the artifacts to the release.
- Contract-equivalence tests for every Session Checkpoint mode.
- Full-SHA GitHub Actions pins with Dependabot maintenance.

## [0.2.0] - 2026-07-31

### Fixed

- Checkpoint suggestions are delivered once per pending checkpoint and repeat only after the
  configured cooldown, instead of on every completed tool call.

### Changed

- Claude Code project installs write skills to `.claude/skills/` so the host lists
  `outcome-brief` and `session-checkpoint` natively.
- Claude Code project hook commands anchor on `$CLAUDE_PROJECT_DIR` so they no longer depend
  on the session's working directory.
- `doctor` resolves scope automatically: a project install for the current directory wins,
  the user install is the fallback, and `--scope` forces either. The report header now names
  the scope it checked.

## [0.1.0] - 2026-07-31

### Added

- `outcome-brief` with five honest terminal statuses and an evidence-first field order.
- `session-checkpoint` with orient, teach, and spoken modes.
- Dependency-free Python control plane for validation, safe-boundary triggers, and bounded state.
- Codex, Claude Code, and GitHub Copilot lifecycle adapters.
- Native plugin and marketplace manifests for all three ecosystems.
- Reversible user and project installers with receipts and conflict preservation.
- Network-free Copilot cloud bridge built as a deterministic Python zipapp.
- Doctor, configuration, state-retention, and validation commands.
- Synthetic Apex experience pilot and clean-room verification surfaces.

[Unreleased]: https://github.com/luanmorenommaciel/brief-spec/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/luanmorenommaciel/brief-spec/compare/v0.2.0...v0.5.0
[0.4.0]: https://github.com/luanmorenommaciel/brief-spec/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/luanmorenommaciel/brief-spec/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/luanmorenommaciel/brief-spec/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/luanmorenommaciel/brief-spec/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/luanmorenommaciel/brief-spec/releases/tag/v0.1.0
