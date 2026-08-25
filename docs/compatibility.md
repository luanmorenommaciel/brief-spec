# Compatibility

Brief-Spec ships one dependency-free Python core with a data-driven harness adapter registry. Its
compatibility promise is split into four independently testable layers:

1. unchanged Outcome Brief and Session Checkpoint `1.0` contracts;
2. deterministic work-type classification and explanation profiles;
3. host event normalization, installation ownership, and rollback;
4. real discovery and lifecycle execution in each host.

The first three layers are deterministic and covered by the repository test suite. Lifecycle
claims require retained, expiring conformance evidence; installation evidence alone does not imply
an authenticated host run.

## Supported harnesses

| Harness | Maturity | User scope | Project scope | Native projection |
| --- | --- | --- | --- | --- |
| Codex | Live-verified | Yes | Yes | portable skills, hooks, and runtime |
| Claude Code | Live-verified | Yes | Yes | portable skills, hooks, and runtime |
| Oh My Pi (OMP) | Live-verified | Yes | Yes | native skills and lifecycle extension |
| Grok Build | Live-verified | Yes | Yes | `.grok/skills` and `.grok/hooks/brief-spec.json` |
| Kimi Code | Live-verified | Yes | Skills only | user plugin; project lifecycle requires that user plugin |
| Google Antigravity | Live-verified | Yes | Yes | `.gemini/antigravity/skills`, `.agents/skills`, and `hooks.json` |
| GitHub Copilot | Experimental | Yes | Yes | portable skills, hooks, and cloud bridge |
| Cursor Agent | Experimental | Yes | Yes | portable skills and hooks |
| Goose | Experimental | Yes | Yes | portable skills; lifecycle automation unavailable |

A model is not a harness. For example, Grok selected inside OMP is recorded as `harness=omp`,
`model_provider=xai`, and a separate model value.

Grok's native passive hooks record session, prompt, tool, compaction, and agent events, but Grok
1.0.x ignores stdout from passive hooks. The installed native `brief-spec` skill therefore performs
the user-facing routing in the first visible response. The Stop hook returns one bounded
correction with exact classification metadata only when that response still lacks a valid Outcome
Brief or Session Checkpoint. It does not continue a completed brief merely to wrap typed profile
sections, because Grok displays the first message before Stop runs. Its live implementation gate
runs in a disposable repository with only native `read_file` and `search_replace`; shell, web,
memory, and subagents remain disabled.

`brief-spec setup all` touches detected harnesses only. Missing executables are warnings unless
named by `--require`. Multi-host setup is one transaction: failure restores every touched path and
preserves foreign configuration.

## Naming compatibility through `0.x`

The canonical interfaces are the `brief-spec` distribution and CLI, the `brief_spec` import,
`BRIEF_SPEC_HOME`, and `~/.local/state/brief-spec`. The same distribution also supplies:

- the `briefspec` CLI alias and `briefspec` forwarding import;
- legacy `briefspec:*` markers, schemas, receipts, environment variable, state directory, and
  renderer entry-point group;
- `install` as an alias for `setup`.

Legacy interfaces warn only when explicitly invoked. Doctor reads legacy receipts and state and
migrates receipt-owned paths transactionally with `--fix`. There is no separately published
`briefspec` compatibility distribution.

## Shared assets and package projection

The source tree retains compatibility-oriented internal paths while its public metadata is
canonical:

```text
.codex-plugin/plugin.json             Codex metadata
.claude-plugin/plugin.json            Claude-compatible metadata
plugin.json                           portable Agent Plugins 1.0 manifest
skills/brief-spec/                    universal type router
skills/outcome-brief/                 terminal lifecycle contract
skills/session-checkpoint/            checkpoint lifecycle contract
schemas/brief-spec-delivery.schema.json
scripts/brief-spec-hook               canonical source-checkout entrypoint
scripts/briefspec-hook                legacy source-checkout entrypoint
```

The wheel includes both import names and projects skills, hooks, schemas, integrations, and plugin
metadata under its resources. `scripts/verify-release.py` verifies source-to-wheel byte equality.

## Lifecycle normalization

Adapters map native events to the common session, prompt, tool-result, pre-compaction, and stop
boundaries. Unsupported capabilities remain explicit in `brief-spec capabilities all --json`.
Automatic type routing occurs on substantive prompts; checkpoints and terminal briefs remain at
safe lifecycle boundaries.

Codex and Claude keep their established command-hook projections. OMP uses `session_start`,
`before_agent_start`, `tool_result`, `session.compacting`, and `session_stop`. Grok and Kimi receive
their native hook manifests. Kimi project installation deliberately omits hooks because Kimi
plugins are user-wide; doctor reports whether the user plugin supplies lifecycle automation.

## Deterministic and live gates

Run the deterministic gates:

```bash
uv run ruff check .
uv run ruff format --check .
uv run python scripts/verify-release.py
uv run pytest --cov=briefspec --cov-report=term-missing
```

Then build wheel and sdist once, verify both canonical and legacy imports/commands in clean
environments, and run the live disposable-repository harness. Fixture-only coverage is not evidence
that a host loaded an integration. Cursor, Goose, and Copilot therefore remain experimental until
their authenticated live gates are separately completed.

The retained live baseline passed 8/8 Codex, 8/8 Claude, and 4/4 each for OMP, Grok, and Kimi.
Those runs predate the exact current uncommitted candidate, so they are regression evidence rather
than authorization to publish the current bytes. See the generated
[verification record](verification.md) for the exact truth boundary; deterministic local passes,
live host passes, hosted CI, and publication are separate claims.

## Official references

- [OMP skills](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md)
- [OMP extensions](https://github.com/can1357/oh-my-pi/blob/main/docs/extensions.md)
- [OMP extension loading](https://github.com/can1357/oh-my-pi/blob/main/docs/extension-loading.md)
- [Kimi plugins](https://moonshotai.github.io/kimi-code/en/customization/plugins.html)
- [Kimi hooks](https://moonshotai.github.io/kimi-code/en/customization/hooks)
- [Kimi skills](https://moonshotai.github.io/kimi-code/en/customization/skills)
- [Claude plugin reference](https://code.claude.com/docs/en/plugins-reference)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
- [GitHub Copilot hooks](https://docs.github.com/en/copilot/reference/hooks-reference)
