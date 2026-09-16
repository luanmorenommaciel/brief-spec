# Contributing

Brief-Spec is intentionally small: host agents synthesize; the core normalizes, schedules, validates,
and installs.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/run-pilot.py
uv build
```

Run the release verifier and both plugin validators before submitting a change. The Codex check
uses a throwaway `CODEX_HOME`, the same way CI does:

```bash
uv run python scripts/verify-release.py
CODEX_HOME="$(mktemp -d)" sh -c \
  'codex plugin marketplace add "$PWD" --json && codex plugin add brief-spec@brief-spec --json'
claude plugin validate .claude-plugin/plugin.json --strict
claude plugin validate .claude-plugin/marketplace.json --strict
```

A change to `src/`, `skills/`, `scripts/`, `hooks/`, `integrations/`, `schemas/`, `packages/`, or a
plugin manifest invalidates the recorded live-host evidence. Rerun the live matrix with
`scripts/run-live-e2e.py` for every required host, then rebuild `release/live-e2e-evidence.json`
with `scripts/build-live-e2e-evidence.py` before release.

Add tests for every behavior change. Adapter fixtures must not contain credentials or private
transcripts. Preserve the distinction between structural, synthetic, local-runtime, and live-cloud
evidence.
