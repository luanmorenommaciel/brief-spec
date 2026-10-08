from __future__ import annotations

from collections.abc import Callable

import pytest

from briefspec.markdown import detect_kind, validate_checkpoint, validate_outcome
from briefspec.models import CheckpointMode


@pytest.mark.parametrize(
    ("status", "human_action", "gaps", "next_items", "open_items"),
    [
        ("DONE", "None", ("None",), ("None",), ("None",)),
        (
            "REVIEW",
            "Inspect the implementation.",
            ("None",),
            ("Review the diff.",),
            ("None",),
        ),
        (
            "DECIDE",
            "Choose the installation scope.",
            ("None",),
            ("Select user or project scope.",),
            ("User scope or project scope?",),
        ),
        (
            "BLOCKED",
            "Grant access to the host.",
            ("The host is unavailable.",),
            ("Install and authenticate the host.",),
            ("None",),
        ),
        (
            "FAILED",
            "None",
            ("The build did not complete.",),
            ("Inspect the build error.",),
            ("None",),
        ),
    ],
)
def test_each_outcome_status_accepts_its_required_invariants(
    outcome_text: Callable[..., str],
    status: str,
    human_action: str,
    gaps: tuple[str, ...],
    next_items: tuple[str, ...],
    open_items: tuple[str, ...],
) -> None:
    result = validate_outcome(
        outcome_text(
            status=status,
            human_action=human_action,
            gaps=gaps,
            next_items=next_items,
            open_items=open_items,
        )
    )
    assert result.valid, result.errors
    assert result.data["Status"] == status


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        ({"status": "DONE", "human_action": "Review it."}, "DONE cannot require human action"),
        (
            {"status": "DONE", "gaps": ("Coverage is missing.",)},
            "DONE cannot contain unresolved gaps",
        ),
        ({"status": "REVIEW", "human_action": "None"}, "REVIEW requires explicit human action"),
        (
            {"status": "DECIDE", "human_action": "Choose.", "open_items": ("None",)},
            "DECIDE requires an open decision",
        ),
        (
            {
                "status": "BLOCKED",
                "human_action": "Unblock it.",
                "gaps": ("None",),
                "next_items": ("Retry.",),
            },
            "BLOCKED requires an explicit gap",
        ),
        (
            {"status": "FAILED", "gaps": ("It failed.",), "next_items": ("None",)},
            "FAILED requires a next action",
        ),
        ({"status": "UNKNOWN"}, "Status must be DONE"),
        ({"proof": ("None",)}, "Proof must contain at least one"),
    ],
)
def test_outcome_status_invariants_are_enforced(
    outcome_text: Callable[..., str],
    changes: dict[str, object],
    expected_error: str,
) -> None:
    result = validate_outcome(outcome_text(**changes))
    assert not result.valid
    assert any(expected_error in error for error in result.errors)


def test_outcome_requires_bounded_markers(outcome_text: Callable[..., str]) -> None:
    text = outcome_text().replace("<!-- /briefspec -->", "")
    result = validate_outcome(text)
    assert not result.valid
    assert result.errors == ("Missing bounded outcome marker",)


def test_outcome_requires_field_order(outcome_text: Callable[..., str]) -> None:
    text = outcome_text()
    text = text.replace(
        "Status: DONE\nOutcome: The requested work is complete.",
        "Outcome: The requested work is complete.\nStatus: DONE",
    )
    result = validate_outcome(text)
    assert not result.valid
    assert "Fields are not in the required order" in result.errors


@pytest.mark.parametrize(
    ("field", "items", "expected"),
    [
        ("proof", tuple(str(index) for index in range(6)), "Proof supports at most 5 items"),
        ("next_items", tuple(str(index) for index in range(4)), "Next supports at most 3 items"),
        ("open_items", tuple(str(index) for index in range(4)), "Open supports at most 3 items"),
    ],
)
def test_outcome_item_limits(
    outcome_text: Callable[..., str],
    field: str,
    items: tuple[str, ...],
    expected: str,
) -> None:
    kwargs: dict[str, object] = {field: items}
    if field == "open_items":
        kwargs.update(status="DECIDE", human_action="Choose.")
    result = validate_outcome(outcome_text(**kwargs))
    assert not result.valid
    assert expected in result.errors


def test_long_outcome_is_a_warning_not_a_structural_failure(
    outcome_text: Callable[..., str],
) -> None:
    result = validate_outcome(outcome_text(outcome="x" * 501))
    assert result.valid
    assert "Outcome is longer than 500 characters" in result.warnings


def test_outcome_rejects_uninspectable_proof(
    outcome_text: Callable[..., str],
) -> None:
    result = validate_outcome(outcome_text(proof=("trust me",)))
    assert not result.valid
    assert any(
        "Proof item 1 must contain an inspectable locator" in error for error in result.errors
    )


def test_outcome_warns_when_evidence_classification_is_missing(
    outcome_text: Callable[..., str],
) -> None:
    result = validate_outcome(
        outcome_text(
            status="REVIEW",
            human_action="Review the change.",
            proof=("`tests/test_contract.py` — direct evidence",),
        )
    )
    assert result.valid
    assert any("Proof item 1 should start with" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("proof", "expected"),
    [
        (("[direct/fail] `pytest` → 3 failed",), "DONE cannot include failing proof"),
        (
            ("[direct/pass] `pytest` → 9 passed", "[derived/fail] `ruff check` → 1 error"),
            "DONE cannot include failing proof",
        ),
        (("[derived/pass] `pytest` → 9 passed",), "DONE requires at least one [direct/pass]"),
        (("[reported/pass] CI run #12 → green",), "DONE requires at least one [direct/pass]"),
        (("`pytest` → 9 passed",), "DONE requires at least one [direct/pass]"),
        (("[direct/info] `src/app.py`",), "DONE requires at least one [direct/pass]"),
    ],
)
def test_done_requires_direct_passing_proof_without_failures(
    outcome_text: Callable[..., str], proof: tuple[str, ...], expected: str
) -> None:
    result = validate_outcome(outcome_text(proof=proof))
    assert not result.valid
    assert any(error.startswith(expected) for error in result.errors)


def test_done_accepts_direct_pass_alongside_context(outcome_text: Callable[..., str]) -> None:
    result = validate_outcome(
        outcome_text(
            proof=(
                "[direct/pass kind=test] `pytest` → 9 passed",
                "[reported/info] [CI](https://example.test/run/1) → queued",
            )
        )
    )
    assert result.valid, result.errors


def test_outcome_accepts_explicit_evidence_classification(
    outcome_text: Callable[..., str],
) -> None:
    result = validate_outcome(
        outcome_text(
            proof=(
                "[direct/pass] `uv run pytest` → 217 passed",
                "[reported/info] [Windows CI](https://example.test/run/1) → green",
            )
        )
    )
    assert result.valid
    assert not result.warnings


@pytest.mark.parametrize("mode", list(CheckpointMode))
def test_each_checkpoint_mode_validates(
    checkpoint_text: Callable[..., str], mode: CheckpointMode
) -> None:
    result = validate_checkpoint(checkpoint_text(mode.value), expected_mode=mode)
    assert result.valid, result.errors
    assert result.data["Mode"] == mode.value


def test_checkpoint_expected_mode_mismatch_is_invalid(
    checkpoint_text: Callable[..., str],
) -> None:
    result = validate_checkpoint(checkpoint_text("orient"), expected_mode=CheckpointMode.TEACH)
    assert not result.valid
    assert "Expected teach mode, found orient" in result.errors


def test_spoken_checkpoint_warns_when_short(
    checkpoint_text: Callable[..., str],
) -> None:
    result = validate_checkpoint(checkpoint_text("spoken", script_words=79))
    assert result.valid
    assert "Spoken script is shorter than 80 words" in result.warnings


def test_spoken_checkpoint_rejects_overlong_script(
    checkpoint_text: Callable[..., str],
) -> None:
    result = validate_checkpoint(checkpoint_text("spoken", script_words=241))
    assert not result.valid
    assert "Spoken script must not exceed 240 words" in result.errors


@pytest.mark.parametrize("forbidden", ["| column |", "```python"])
def test_spoken_checkpoint_rejects_non_speech_structures(
    checkpoint_text: Callable[..., str], forbidden: str
) -> None:
    text = checkpoint_text("spoken").replace("Script: ", f"Script: {forbidden} ")
    result = validate_checkpoint(text)
    assert not result.valid
    assert "Spoken script cannot contain tables or code fences" in result.errors


def test_kind_detection_is_marker_based(
    outcome_text: Callable[..., str], checkpoint_text: Callable[..., str]
) -> None:
    assert detect_kind(outcome_text()) == "outcome"
    assert detect_kind(checkpoint_text("orient")) == "checkpoint"
    assert detect_kind("ordinary prose") is None
