from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from briefspec.evaluation import DEFAULT_CORPUS, evaluate, load_corpus
from briefspec.work_types import classify_task, model_prediction

DATA = DEFAULT_CORPUS.parent
ROOT = Path(__file__).resolve().parents[1]

# Regression floors, set just below the accuracy measured when classifier 1.3 shipped.
# classification-test.jsonl was written independently and never used for training.
FLOORS = {
    "classification-corpus.jsonl": 0.93,
    "classification-heldout.jsonl": 0.92,
    "classification-test.jsonl": 0.66,
}


@pytest.mark.parametrize(("name", "floor"), FLOORS.items())
def test_classifier_accuracy_does_not_regress(name: str, floor: float) -> None:
    report = evaluate(load_corpus(DATA / name))
    assert report["accuracy"] >= floor, report["mismatches"][:5]


def test_shipped_model_matches_its_training_data() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/train-classifier.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_model_only_backs_up_rules_without_a_request_verb() -> None:
    decisive = classify_task("Review the deploy script for risky steps")
    assert decisive.work_type == "review"
    assert "model.naive-bayes" not in decisive.rule_ids
    prediction = model_prediction("curious how other teams compare vendors for this")
    assert prediction is not None
    label, probability = prediction
    assert label in {"research", "general"}
    assert 0 < probability <= 1


def test_load_corpus_rejects_bad_rows(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"prompt": "x", "type": "unknown"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_corpus(bad)


# Short ids: pytest exports the node id in PYTEST_CURRENT_TEST, and Windows caps an
# environment variable at 32,767 characters.
ADVERSARIAL = {
    "unpunctuated-how": ("how is the value passed here and what gets handled next ", 2000),
    "which-tools": ("which tools ", 8000),
    "dashes": ("a-", 40000),
    "dots": ("x.", 40000),
    "slashes": ("a/", 40000),
}


@pytest.mark.parametrize("name", ADVERSARIAL)
def test_classifier_stays_fast_on_adversarial_input(name: str) -> None:
    import time

    unit, count = ADVERSARIAL[name]
    text = unit * count
    started = time.perf_counter()
    classify_task(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    ("prompt", "decisive"),
    [
        ("make sure the restart path handles errors", False),
        ("the publish command should print the url", False),
        ("Is this approach correct?", False),
        ("ok now review the diff", True),
        ("Review the folder structure and tell me what each item is for.", True),
    ],
)
def test_decisive_shift_needs_a_request_verb_at_clause_start(prompt: str, decisive: bool) -> None:
    from briefspec.work_types import is_decisive_shift

    assert is_decisive_shift(classify_task(prompt), "implementation", prompt) is decisive


def test_dont_just_x_y_keeps_the_real_request() -> None:
    assert classify_task("Don't just review it, fix the parser bug").work_type == "implementation"


def test_render_report_and_cli_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from briefspec.cli import main
    from briefspec.evaluation import render_report

    corpus = tmp_path / "mini.jsonl"
    corpus.write_text(
        '{"id": "m1", "prompt": "Review pull request #42 for risk", "type": "review", '
        '"lang": "en", "tags": ["mixed"]}\n'
        '{"id": "m2", "prompt": "hello there", "type": "debugging", "lang": "pt"}\n',
        encoding="utf-8",
    )
    report = evaluate(load_corpus(corpus))
    text = render_report(report)
    assert "1/2 correct" in text and "m2: expected debugging" in text
    assert report["by_tag"] == {"mixed": 1.0}
    assert main(["eval", str(corpus), "--min-accuracy", "0.9"]) == 1
    assert "below" in capsys.readouterr().err
    assert main(["eval", str(corpus), "--json"]) == 0
    assert '"accuracy": 0.5' in capsys.readouterr().out


def test_every_live_harness_prompt_classifies_as_expected() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "brief_spec_live_e2e", ROOT / "scripts" / "run-live-e2e.py"
    )
    assert spec is not None and spec.loader is not None
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    wrong = []
    for host in live.HOSTS:
        for work_type in live.WORK_TYPES:
            for mode in live.MODES:
                result = classify_task(live._prompt(host, work_type, mode))
                expected_subject = live._TASKS[work_type][0]
                if (result.work_type, result.subject) != (work_type, expected_subject):
                    wrong.append((host, work_type, mode, result.work_type, result.subject))
    assert not wrong
