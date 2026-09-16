# Installation

## Prerequisites

- Python 3.11 or newer
- One or more supported hosts. Codex, Claude Code, Oh My Pi (OMP), Grok Build,
  and Kimi Code retain their full `0.5.0` live baseline; the exact current
  uncommitted worktree has installation and synthetic-probe evidence. GitHub
  Copilot, Cursor Agent, and Goose are experimental.
- `uv` is recommended for isolated tool installation

Brief-Spec has no runtime Python dependencies and performs no network calls from hooks.

## Published and candidate installation

The public GitHub release is currently `v0.2.0`. The source checkout is a
`v0.5.0` candidate and must not be described as published until current
exact-worktree live gates, hosted CI, GitHub Release, and PyPI evidence all
pass. The retained five-host baseline is not an exact-current-worktree rerun.

Install the public release:

```bash
uv tool install git+https://github.com/luanmorenommaciel/brief-spec.git@v0.2.0
briefspec install all --scope user
briefspec doctor all --probe
```

Those commands intentionally use the legacy `v0.2.0` interface. The `briefspec`
alias remains supported throughout `0.x`, but new installations use `brief-spec`.

Dogfood the complete candidate stack from its checkout. Installing Chronicle
does not initialize or capture any project:

```bash
uv tool install --force --reinstall \
  --with ./packages/brief-spec-renderer-pdf \
  --with ./packages/brief-spec-renderer-audio \
  --with ./packages/brief-spec-chronicle \
  --with ./packages/brief-spec-renderer-video \
  --with-executables-from brief-spec-chronicle \
  .
brief-spec setup all --scope user --require codex,claude,omp,grok,kimi
brief-spec doctor all --scope user --probe --all-scopes
brief-spec-chronicle --version
```

After `v0.5.0` is published to PyPI, replace the candidate with immutable,
version-pinned core and renderer distributions. Chronicle and video remain a
separate experimental extension track until they are independently published:

```bash
uv tool install --force "brief-spec==0.5.0" \
  --with "brief-spec-renderer-pdf==0.5.0" \
  --with "brief-spec-renderer-audio==0.5.0"
brief-spec setup all --scope user --require codex,claude,omp,grok,kimi
brief-spec doctor all --scope user --probe --all-scopes
```

Install or inspect one host:

```bash
brief-spec setup codex
brief-spec setup claude
brief-spec setup omp
brief-spec setup grok
brief-spec setup kimi
brief-spec setup antigravity
brief-spec doctor all --probe
```

The portable installer copies the three skills, creates a self-contained runtime zipapp, merges host
hook configuration, and writes a receipt. It does not require a host executable to be present;
`doctor` reports a missing executable as a warning so configuration can be prepared ahead of time.
`setup all` installs detected harnesses only; use `--require` when named absences must fail.

Upgrade ownership requires both a receipt path and its prior hash; a
Brief-Spec-looking marker is not ownership. If a receipt-owned skill was edited
locally, setup preserves it and stages the new bytes beside it as
`*.brief-spec-new`. Doctor reports the drift. After reviewing the difference,
replacement requires explicit `doctor --fix --replace-modified` flags.

Preview without writing:

```bash
brief-spec setup all --dry-run
```

## Project and Copilot cloud installation

```bash
brief-spec setup copilot --scope project --project .
brief-spec doctor copilot --scope project --project . --probe
```

Commit the generated `.github/brief-spec/`, `.github/hooks/`,
`.github/instructions/`, and `.agents/skills/` files when a Copilot cloud job must discover them.
The bridge is network-free and stores only ephemeral counters during the job.

Codex and Claude also support project scope:

```bash
brief-spec setup codex --scope project
brief-spec setup claude --scope project
```

A Codex project install resolves `.codex/brief-spec/brief-spec.pyz` from
`git rev-parse --show-toplevel` at hook execution time, so starting a task in a
nested repository directory does not change the bundle location. Windows
installs receive the equivalent `commandWindows` PowerShell override.

A Claude Code project install writes skills to `.claude/skills/` so the host discovers
`outcome-brief` and `session-checkpoint` natively, and anchors hook commands on
`$CLAUDE_PROJECT_DIR` so they keep working when a session's working directory moves.
`brief-spec doctor` resolves scope automatically: it reports the project install when one
exists for the current directory and the user install otherwise; pass `--scope` to force one.

OMP supports skills and lifecycle extensions at user and project scope. Grok Build receives native
`.grok/skills` assets and a receipt-owned `.grok/hooks/brief-spec.json`. Kimi project setup installs
skills only; lifecycle automation requires the user-wide managed Brief-Spec plugin, and doctor
reports that boundary instead of claiming project-scoped hooks.

## Optional download renderers

The PDF and audio packages are injected into the isolated Brief-Spec tool
environment. The core package remains dependency-free.

For candidate testing from this checkout:

```bash
uv tool install --force . \
  --with ./packages/brief-spec-renderer-pdf \
  --with ./packages/brief-spec-renderer-audio
brief-spec capabilities all --json
brief-spec doctor codex --fix
```

`doctor --fix` may download Playwright Chromium when the PDF renderer is
installed. It reports missing `ffmpeg`, `ffprobe`, or Poppler tools rather than
silently installing system packages. The audio renderer uses local macOS
`say` by default; OpenAI speech requires `--audio-provider openai` and
`--consent-network` on the export or bundle command.

## Optional Project Chronicle

Chronicle is an experimental, independently versioned package and is never activated by global
Brief-Spec setup. Install the source packages into the same managed tool environment, then
initialize one project. Include every optional package you want to retain because reinstalling the
tool replaces that environment:

```bash
uv tool install --force --reinstall \
  --with ./packages/brief-spec-renderer-pdf \
  --with ./packages/brief-spec-renderer-audio \
  --with ./packages/brief-spec-chronicle \
  --with ./packages/brief-spec-renderer-video \
  --with-executables-from brief-spec-chronicle \
  .
brief-spec-chronicle init --project .
brief-spec-chronicle doctor --project .
brief-spec-chronicle archive --project . --output chronicle-archive.zip
```

Initialization writes only under `$BRIEF_SPEC_HOME/chronicles`; it does not create project files.
Project metadata remains until explicit archive or exact-ID deletion. Optional PDF/audio Chronicle
downloads reuse the existing renderer packages. Optional video additionally requires
`./packages/brief-spec-renderer-video`, Playwright Chromium, `ffmpeg`, and `ffprobe`.
An archive does not delete live state. After an exact-ID deletion, restore can explicitly bind a
validated archive to an existing project directory:

```bash
brief-spec-chronicle restore chronicle-archive.zip --project /path/to/project
```

## Native plugin installation (alternative, not additive)

The portable user-scope installation is the authoritative global integration.
Do not also install Brief-Spec through native Codex or Claude plugin systems;
duplicating lifecycle hooks makes ownership and rollback ambiguous. The native
commands below are retained for users who deliberately choose the plugin-only
path instead of portable setup.

The repository ships native manifests in addition to the portable installer.

### Codex

```bash
codex plugin marketplace add luanmorenommaciel/brief-spec --ref v0.5.0
codex plugin add brief-spec@brief-spec
```

### Claude Code

```bash
claude plugin marketplace add luanmorenommaciel/brief-spec
claude plugin install brief-spec@brief-spec --scope user
```

For local development, pass the absolute checkout path to each marketplace command.

### Copilot CLI

```bash
copilot plugin marketplace add luanmorenommaciel/brief-spec
copilot plugin install brief-spec@brief-spec
```

VS Code can discover plugins installed by Copilot CLI. Agent plugins and hooks in VS Code are
currently Preview features and may be disabled by organization policy.

Native plugin installation gives the host its normal plugin inventory experience. It does not
replace the portable runtime, receipts, drift detection, or transactional multi-host setup.

## Upgrade

```bash
uv tool upgrade brief-spec
brief-spec setup all --scope user
brief-spec doctor all --probe --all-scopes
```

Installation is idempotent. Brief-Spec-owned assets are refreshed; foreign or locally modified files
cause a conflict instead of being overwritten. If any write fails during one runtime installation,
the installer restores every managed path to its exact pre-install content.

## Uninstall

```bash
brief-spec uninstall all --scope user
```

Project installations require the same scope and project path used during installation. Uninstall
removes only matching receipt-owned files. Shared files still used by another runtime and modified
files are preserved with warnings.
