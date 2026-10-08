from __future__ import annotations

import json
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

from briefspec.models import ClassificationOrigin, WorkType
from briefspec.work_types import CLASSIFIER_ADAPTER_VERSION, classify_task

DEFAULT_CORPUS = Path(__file__).resolve().parent / "data" / "classification-corpus.jsonl"


def load_corpus(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        source: Any = resources.files("briefspec").joinpath("data", DEFAULT_CORPUS.name)
    else:
        source = path
    rows: list[dict[str, Any]] = []
    valid_types = {item.value for item in WorkType}
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict) or not str(row.get("prompt", "")).strip():
            raise ValueError(f"{source}:{number}: each row needs a non-empty prompt")
        if row.get("type") not in valid_types:
            raise ValueError(f"{source}:{number}: type must be one of {sorted(valid_types)}")
        rows.append(row)
    if not rows:
        raise ValueError(f"{source}: corpus is empty")
    return rows


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score the local classifier against labeled prompts."""
    types = [item.value for item in WorkType]
    confusion: dict[str, Counter[str]] = {name: Counter() for name in types}
    mismatches: list[dict[str, Any]] = []
    fallbacks = 0
    by_lang: dict[str, list[bool]] = {}
    by_tag: dict[str, list[bool]] = {}
    for row in rows:
        result = classify_task(str(row["prompt"]))
        expected = str(row["type"])
        confusion[expected][result.work_type] += 1
        correct = result.work_type == expected
        if result.origin == ClassificationOrigin.FALLBACK.value:
            fallbacks += 1
        by_lang.setdefault(str(row.get("lang", "en")), []).append(correct)
        for tag in row.get("tags", []) or []:
            by_tag.setdefault(str(tag), []).append(correct)
        if not correct:
            mismatches.append(
                {
                    "id": row.get("id"),
                    "prompt": row["prompt"],
                    "expected": expected,
                    "actual": result.work_type,
                    "origin": result.origin,
                    "rules": list(result.rule_ids),
                }
            )
    total = len(rows)
    per_type: dict[str, dict[str, Any]] = {}
    for name in types:
        true_positive = confusion[name][name]
        predicted = sum(confusion[other][name] for other in types)
        actual = sum(confusion[name].values())
        per_type[name] = {
            "support": actual,
            "precision": round(true_positive / predicted, 3) if predicted else None,
            "recall": round(true_positive / actual, 3) if actual else None,
        }
    correct_total = sum(confusion[name][name] for name in types)
    return {
        "classifier_version": CLASSIFIER_ADAPTER_VERSION,
        "total": total,
        "correct": correct_total,
        "accuracy": round(correct_total / total, 3),
        "fallback_rate": round(fallbacks / total, 3),
        "per_type": per_type,
        "by_language": {
            lang: round(sum(values) / len(values), 3) for lang, values in sorted(by_lang.items())
        },
        "by_tag": {
            tag: round(sum(values) / len(values), 3) for tag, values in sorted(by_tag.items())
        },
        "confusion": {name: dict(confusion[name]) for name in types},
        "mismatches": mismatches,
    }


def render_report(report: dict[str, Any], *, limit: int = 20) -> str:
    lines = [
        f"Classifier {report['classifier_version']}: {report['correct']}/{report['total']} correct "
        f"({report['accuracy']:.1%}), fallback to general {report['fallback_rate']:.1%}",
        "",
        "type            support  precision  recall",
    ]
    for name, values in report["per_type"].items():
        precision = "-" if values["precision"] is None else f"{values['precision']:.2f}"
        recall = "-" if values["recall"] is None else f"{values['recall']:.2f}"
        lines.append(f"{name:<15} {values['support']:>7}  {precision:>9}  {recall:>6}")
    if report["by_language"]:
        lines.append("")
        lines.append(
            "by language: "
            + ", ".join(f"{lang} {value:.1%}" for lang, value in report["by_language"].items())
        )
    if report["mismatches"]:
        lines.append("")
        lines.append(f"First {min(limit, len(report['mismatches']))} mismatches:")
        for item in report["mismatches"][:limit]:
            lines.append(f"  {item['id']}: expected {item['expected']}, got {item['actual']}")
            lines.append(f"    {item['prompt'][:110]}")
    return "\n".join(lines)
