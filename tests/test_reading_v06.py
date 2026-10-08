from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from briefspec.adapters import normalize_event
from briefspec.cli import main
from briefspec.delivery import load_delivery
from briefspec.hooks import process_event
from briefspec.markdown import parse_typed, validate_outcome
from briefspec.models import Runtime
from briefspec.notify import acknowledgments
from briefspec.work_types import type_profile

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _send(runtime: Runtime, name: str, session: str, minute: int = 0, **payload: object):
    raw = {"session_id": session, "timestamp": (NOW + timedelta(minutes=minute)).isoformat()}
    raw.update(payload)
    return process_event(normalize_event(runtime, raw, name), raw)


def test_compaction_restart_asks_for_orient_reentry(isolated_homes: dict[str, Path]) -> None:
    _send(Runtime.CLAUDE, "UserPromptSubmit", "reentry", prompt="Debug the failing upload test.")
    resumed = _send(Runtime.CLAUDE, "SessionStart", "reentry", minute=5, source="compact")
    assert "Orient re-entry" in (resumed.context or "")
    assert "debugging" in (resumed.context or "")
    fresh = _send(Runtime.CLAUDE, "SessionStart", "other", minute=6, source="startup")
    assert "Orient re-entry" not in (fresh.context or "")


def test_session_context_states_the_done_rule() -> None:
    from briefspec.hooks import SESSION_CONTEXT

    assert "[direct/pass]" in SESSION_CONTEXT


def _typed(work_type: str, outcome: str, extra: str = "") -> str:
    explanation = "\n".join(
        f"### {section.label}\nContent for {section.label}.\n"
        for section in type_profile(work_type).sections
    )
    return (
        f"<!-- brief-spec:typed:v1 type={work_type} subject=general confidence=medium "
        "origin=inferred classified_at=2026-10-08T12:00:00Z profile=1.0 -->\n"
        f"{explanation}{extra}\n{outcome}\n<!-- /brief-spec -->"
    )


def test_optional_trailing_assessment_is_accepted(outcome_text: Callable[..., str]) -> None:
    text = _typed("debugging", outcome_text(), "### Assessment\nLikely a race in the retry loop.\n")
    parsed = parse_typed(text)
    assert parsed is not None
    assert parsed[1]["sections"][-1]["id"] == "assessment"
    misplaced = _typed("debugging", outcome_text()).replace(
        "### Symptom", "### Assessment\nEarly guess.\n### Symptom"
    )
    with pytest.raises(ValueError):
        parse_typed(misplaced)


def test_decide_without_recommendation_warns(outcome_text: Callable[..., str]) -> None:
    plain = validate_outcome(
        outcome_text(
            status="DECIDE",
            human_action="Choose the queue.",
            open_items=("Use SQS or Redis streams?",),
        )
    )
    assert plain.valid
    assert any("recommendation" in warning for warning in plain.warnings)
    card = validate_outcome(
        outcome_text(
            status="DECIDE",
            human_action="Choose the queue.",
            open_items=(
                "Options: SQS; Redis streams — Recommendation: SQS — Reversible: yes — "
                "Needed by: 2026-10-12",
            ),
        )
    )
    assert card.valid and not card.warnings


def test_ack_records_choice_bound_to_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome_text: Callable[..., str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("BRIEF_SPEC_HOME", str(tmp_path / "state"))
    brief = tmp_path / "decide.md"
    text = outcome_text(
        status="DECIDE",
        human_action="Choose the queue.",
        open_items=("Options: SQS; Redis — Recommendation: SQS",),
    )
    brief.write_text(text, encoding="utf-8")
    assert main(["ack", str(brief), "--choice", "SQS", "--by", "luan", "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["kind"] == "brief-spec-ack-receipt"
    assert receipt["metadata"]["choice"] == "SQS"
    delivery, _ = load_delivery(text, source_path=brief)
    assert [item["receipt_id"] for item in acknowledgments(delivery)] == [receipt["receipt_id"]]
    done = tmp_path / "done.md"
    done.write_text(outcome_text(), encoding="utf-8")
    assert main(["ack", str(done), "--choice", "ok"]) == 1


def test_stop_hook_spawns_notify_end_to_end_and_never_blocks(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    outcome_text: Callable[..., str],
) -> None:
    state = isolated_homes["state"]
    state.mkdir(parents=True, exist_ok=True)
    (state / "config.toml").write_text(
        '[notify]\non_stop = true\nconsent_network = true\nchannels = ["eng"]\n',
        encoding="utf-8",
    )
    launched: list[list[str]] = []
    monkeypatch.setattr(
        "briefspec.notify._launch_detached", lambda command, cwd: launched.append(command)
    )
    _send(Runtime.CLAUDE, "UserPromptSubmit", "notify-e2e", prompt="Implement the retry feature.")
    decision = _send(
        Runtime.CLAUDE, "Stop", "notify-e2e", minute=1, last_assistant_message=outcome_text()
    )
    assert decision.action == "allow"
    assert len(launched) == 1 and "--consent-network" in launched[0]

    def explode(command: list[str], cwd: Path) -> None:
        raise OSError("spawn failed")

    monkeypatch.setattr("briefspec.notify._launch_detached", explode)
    _send(Runtime.CLAUDE, "UserPromptSubmit", "notify-e2e-2", prompt="Implement the cache.")
    failed = _send(
        Runtime.CLAUDE, "Stop", "notify-e2e-2", minute=2, last_assistant_message=outcome_text()
    )
    assert failed.action == "allow"
