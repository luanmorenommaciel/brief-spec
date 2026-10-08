#!/usr/bin/env python3
"""Train the dependency-free Naive Bayes fallback used when no rule is decisive."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from briefspec.work_types import model_features

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "briefspec" / "data"
TRAINING = (DATA / "classification-corpus.jsonl", DATA / "classification-heldout.jsonl")
OUTPUT = DATA / "classifier-model.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--check", action="store_true", help="fail if the model is stale")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for path in TRAINING
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    documents = [(model_features(str(row["prompt"])), str(row["type"])) for row in rows]
    totals = Counter(feature for features, _ in documents for feature in set(features))
    vocabulary = sorted(feature for feature, count in totals.items() if count >= args.min_count)
    allowed = set(vocabulary)
    labels = sorted({label for _, label in documents})
    class_docs = Counter(label for _, label in documents)
    counts: dict[str, Counter[str]] = {label: Counter() for label in labels}
    for features, label in documents:
        counts[label].update(feature for feature in features if feature in allowed)
    model: dict[str, object] = {
        "kind": "brief-spec-classifier-model",
        "schema_version": "1.0",
        "algorithm": "multinomial-naive-bayes",
        "alpha": args.alpha,
        "training_rows": len(rows),
        "priors": {},
        "unseen": {},
        "weights": {},
    }
    priors: dict[str, float] = {}
    unseen: dict[str, float] = {}
    weights: dict[str, dict[str, float]] = {}
    for label in labels:
        total = sum(counts[label].values())
        denominator = total + args.alpha * len(vocabulary)
        priors[label] = round(math.log(class_docs[label] / len(documents)), 4)
        unseen[label] = round(math.log(args.alpha / denominator), 4)
        weights[label] = {
            feature: round(math.log((counts[label][feature] + args.alpha) / denominator), 4)
            for feature in vocabulary
            if counts[label][feature]
        }
    model["priors"] = priors
    model["unseen"] = unseen
    model["weights"] = weights
    content = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            raise SystemExit("classifier-model.json is stale; run scripts/train-classifier.py")
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"{OUTPUT}: {len(vocabulary)} features, {len(rows)} rows, {OUTPUT.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
