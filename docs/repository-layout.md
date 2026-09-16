# Repository layout

Brief-Spec keeps release code, optional extensions, portable host assets,
verification evidence, and generated local state in separate ownership zones.
The release verifier enforces the canonical package directories and their
distribution names.

## Canonical map

```text
briefspec/
├── src/
│   ├── brief_spec/                 canonical Python import
│   └── briefspec/                  0.x forwarding/runtime compatibility
│       └── adapters/               bounded harness event normalization
├── packages/
│   ├── brief-spec-chronicle/       optional project-continuity extension
│   ├── brief-spec-renderer-audio/  optional MP3 renderer
│   ├── brief-spec-renderer-pdf/    optional PDF renderer
│   └── brief-spec-renderer-video/  experimental Chronicle video renderer
├── skills/                         portable router, outcome, and checkpoint skills
├── hooks/                          native plugin lifecycle definitions
├── integrations/                   host-specific bridge assets
├── schemas/                        canonical and 0.x-compatible contracts
├── docs/                           maintained architecture and usage guides
├── pilots/                         human-value scenarios and acceptance instruments
├── release/                        checked-in truth-boundary inputs and summaries
├── scripts/                        build, verification, live-gate, and migration tools
├── tests/                          core, compatibility, security, and E2E tests
├── output/                         curated multi-model design-review corpus
├── assets/                         README illustrations
├── seamwise/                       Seamwise task plans and lineage for this repository
├── tasks/                          Task-Spec leaves (completed leaves under tasks/done/)
├── .taskspec/                      Task-Spec acceptance records; mesh/ is ignored runtime state
├── .agents/, .claude/              repository-local agent entry points and plugin marketplace
├── .briefspec/                     ignored local evidence and clean-room workspaces
└── dist/                           ignored, rebuildable distribution artifacts
```

`seamwise/`, `tasks/`, and `.taskspec/` belong to the method tools that plan and accept work in this
repository, as described in `OPERATING.md`. Brief-Spec does not read them at runtime and they are
not shipped in any distribution. `WATCHDOG.yml` configures an external review advisor.

## Naming rules

- Canonical product, distribution, executable, and source-directory names use
  **Brief-Spec** / `brief-spec`.
- `src/brief_spec` is the canonical import surface.
- `src/briefspec`, the `briefspec` executable, legacy marker/schema/state
  identifiers, and the `briefspec.renderers` entry-point group remain available
  through the `0.x` line. They are compatibility surfaces, not naming drift.
- The renderer source directories match their PyPI distribution names. Their
  internal modules remain `briefspec_renderer_pdf` and
  `briefspec_renderer_audio` so existing imports continue to work.
- Chronicle and video are independently versioned optional packages. Their
  versions do not imply the core package version.

`scripts/verify-release.py` rejects unexpected package directories, legacy
renderer directory names, a package-directory/distribution mismatch, or a
missing canonical/compatibility import package.

## Tracked versus generated content

| Location | Tracked | Meaning | Cleanup policy |
| --- | --- | --- | --- |
| `src/`, `packages/`, `skills/`, `hooks/`, `integrations/`, `schemas/` | Yes | Shipped implementation and portable assets | Change through reviewed source edits |
| `docs/`, `pilots/`, `scripts/`, `tests/` | Yes | Maintained guidance and assurance | Must pass release verification |
| `release/` | Yes | Inputs that state the candidate/public evidence boundary | Regenerate only from retained evidence |
| `output/` | Yes | Named independent LLM reviews and their synthesized plan | Not a default export destination; provenance is documented in its README |
| `seamwise/`, `tasks/`, `.taskspec/acceptance/` | Yes | Method records owned by Seamwise and Task-Spec | Change only through those tools |
| `.briefspec/` | No | Local live-host evidence, snapshots, temporary projects, and clean-room environments | Preserve evidence needed by the current candidate; remove only known rebuildable entries |
| `telemetry/`, `.taskspec/mesh/`, `.sc/` | No | Machine-local execution state excluded through `.git/info/exclude` | Never commit |
| `dist/` | No | Locally built wheels and source distributions | Rebuild for each gate; never use as publication truth without a manifest |
| caches and coverage files | No | Rebuildable developer state | Safe to clear between final gates |

## Ownership boundaries

The core remains dependency-free and cannot import Chronicle. Chronicle depends
on the public core contracts. Renderers implement optional entry points and
cannot add facts to a canonical brief or Chronicle snapshot. Harness assets are
projected into the core wheel from their checked-in source locations and are
verified byte-for-byte during release checks.

The Chronicle ledger records what Brief-Spec observed. It never becomes the
authority for Seamwise intent, Task-Spec acceptance, Converge authorization,
Git revisions, CI results, or reviewed knowledge.
