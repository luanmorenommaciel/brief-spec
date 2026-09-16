from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
CHRONICLE_SRC = ROOT / "packages" / "brief-spec-chronicle" / "src"
if str(CHRONICLE_SRC) not in sys.path:
    sys.path.insert(0, str(CHRONICLE_SRC))
VIDEO_SRC = ROOT / "packages" / "brief-spec-renderer-video" / "src"
if str(VIDEO_SRC) not in sys.path:
    sys.path.insert(0, str(VIDEO_SRC))


@pytest.fixture(autouse=True)
def hermetic_host_cli_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests opt into fake CLIs explicitly and never inherit maintainer PATH state."""
    fake_inventory = os.environ.get("BRIEF_SPEC_TEST_HOST_INVENTORY") == "fake"
    monkeypatch.setattr(
        "briefspec.harnesses.shutil.which",
        lambda _candidate: sys.executable if fake_inventory else None,
    )


@pytest.fixture
def isolated_homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    roots = {
        "state": tmp_path / "state",
        "codex": tmp_path / "codex",
        "claude": tmp_path / "claude",
        "copilot": tmp_path / "copilot",
        "omp": tmp_path / "omp",
        "grok": tmp_path / "grok",
        "kimi": tmp_path / "kimi",
        "cursor": tmp_path / "cursor",
        "goose": tmp_path / "goose",
        "antigravity": tmp_path / "antigravity",
    }
    monkeypatch.setenv("BRIEFSPEC_HOME", str(roots["state"]))
    monkeypatch.setenv("CODEX_HOME", str(roots["codex"]))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(roots["claude"]))
    monkeypatch.setenv("COPILOT_HOME", str(roots["copilot"]))
    monkeypatch.setenv("OMP_HOME", str(roots["omp"]))
    monkeypatch.setenv("GROK_HOME", str(roots["grok"]))
    monkeypatch.setenv("KIMI_CODE_HOME", str(roots["kimi"]))
    monkeypatch.setenv("CURSOR_HOME", str(roots["cursor"]))
    monkeypatch.setenv("GOOSE_HOME", str(roots["goose"]))
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(roots["antigravity"]))
    monkeypatch.setenv("AGY_HOME", str(roots["antigravity"]))
    return roots


@pytest.fixture
def outcome_text() -> Callable[..., str]:
    def build(
        *,
        status: str = "DONE",
        outcome: str = "The requested work is complete.",
        human_action: str = "None",
        proof: tuple[str, ...] = ("[direct/info] `tests/test_contract.py` — direct evidence",),
        gaps: tuple[str, ...] = ("None",),
        next_items: tuple[str, ...] = ("None",),
        open_items: tuple[str, ...] = ("None",),
    ) -> str:
        def bullets(items: tuple[str, ...]) -> str:
            return "\n".join(f"- {item}" for item in items)

        return f"""\
<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: {status}
Outcome: {outcome}
Human action: {human_action}

Proof:
{bullets(proof)}

Gaps:
{bullets(gaps)}

Next:
{bullets(next_items)}

Open:
{bullets(open_items)}
<!-- /briefspec -->
"""

    return build


@pytest.fixture
def checkpoint_text() -> Callable[..., str]:
    def build(mode: str = "orient", *, script_words: int = 100) -> str:
        if mode == "orient":
            body = """\
Headline: Runtime verification is the current focus.
Current state: The core implementation exists and is under test.
Completed:
- Provider adapters are present.
Decisions:
- Keep the runtime dependency-free.
Proof:
- [direct/info] `src/briefspec/adapters/base.py` — normalized event implementation
Next:
- Run the complete test suite.
Open:
- None"""
        elif mode == "teach":
            body = """\
Headline: Brief-Spec turns variable agent prose into a stable handoff.
Mental model: The agent writes the explanation while deterministic code checks its shape.
Why it matters: A stable reading pattern lowers the cost of finding decisions and next actions.
What changed:
- Shared validators now define the visible contract.
Example: A DONE result must include direct proof and cannot hide a remaining gap.
Watch-outs:
- Formatting is not proof that an external claim is true.
Next:
- Verify the provider lifecycle adapters.
Proof:
- [direct/info] `src/briefspec/markdown.py` — contract validator"""
        elif mode == "spoken":
            words = " ".join(f"word{index}" for index in range(script_words))
            body = f"""\
Headline: A spoken recap of the current implementation.
Script: {words}
Screen-only proof:
- [direct/info] `src/briefspec/markdown.py` — spoken-mode validation
Next:
- Run the next verification stage."""
        else:
            raise ValueError(mode)
        return f"""\
<!-- briefspec:checkpoint:v1 mode={mode} -->
## Session Checkpoint

{body}
<!-- /briefspec -->
"""

    return build
