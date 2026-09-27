"""Compose a message for each of the 30 canonical test pairs and write submission.jsonl.

python scripts/generate_submission.py [--dataset expanded] [--show]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.dataset import Dataset, dataset_dir  # noqa: E402
from vera.composer import compose  # noqa: E402

# the dataset's own "today"; keeps relative dates (days left, weekday) stable
SIM_NOW = datetime(2026, 4, 26, 10, 30, tzinfo=timezone.utc)
CONTRACT_KEYS = ("body", "cta", "send_as", "suppression_key", "rationale")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", help="path to the expanded dataset")
    parser.add_argument("--out", default=str(ROOT / "submission.jsonl"))
    parser.add_argument("--show", action="store_true", help="print every message")
    args = parser.parse_args()

    data = Dataset(dataset_dir(args.dataset))
    rows = []
    for pair in data.test_pairs():
        trigger = data.trigger(pair["trigger_id"])
        category, merchant, customer = data.contexts_for(trigger)
        result = compose(
            category, merchant, trigger, customer, now=SIM_NOW, roster=data.roster(merchant["merchant_id"])
        )
        rows.append({"test_id": pair["test_id"], **{k: result[k] for k in CONTRACT_KEYS}})
        if args.show:
            print(
                f"\n[{pair['test_id']}] {trigger['kind']} -> {merchant['identity']['name']} ({result['send_as']}, {result['cta']})"
            )
            print(result["body"])

    with open(args.out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(rows)} messages to {args.out}")


if __name__ == "__main__":
    main()
