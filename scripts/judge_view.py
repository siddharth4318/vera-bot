"""Check every number in a message against what the scoring judge can actually see.

The official judge_simulator.py does not show the LLM scorer the whole merchant
context; it builds a short summary (see LLMScorer.score). Numbers that come from
deeper context (customer aggregates, review themes, digest items the trigger
doesn't point at) look unverifiable to it and get scored as fabrication.

    python scripts/judge_view.py [--dataset expanded] [--seeds] [--show]

Reports, per message, the numbers the judge can verify and the ones it can't.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dataset import Dataset, dataset_dir  # noqa: E402
from vera.composer import compose  # noqa: E402

SIM_NOW = datetime(2026, 4, 26, 10, 30, tzinfo=timezone.utc)

# numbers attached to these words describe Vera's own work ("ready in 10 min", "3-line draft"), not claims
ACTION_UNITS = r"(?:\s?-?\s?(?:days? (?:of|window)|min|mins|minute|minutes|hour|hours|hrs|line|lines|step|steps|week|weeks|page|pages|post|posts|sec|seconds|word|words|plate|plates|thalis|kids|classes|class|tier|tiers))\b"
NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(%?)")


def judge_view(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    """Exactly the fields judge_simulator.py puts in the scoring prompt."""
    ident = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    return {
        "category": category.get("slug"),
        "voice": category.get("voice", {}).get("tone"),
        "taboos": category.get("voice", {}).get("vocab_taboo", [])[:5],
        "merchant": ident.get("name"),
        "owner": ident.get("owner_first_name"),
        "locality": ident.get("locality"),
        "languages": ident.get("languages", []),
        "performance": {"views": perf.get("views"), "calls": perf.get("calls"), "ctr": perf.get("ctr")},
        "signals": merchant.get("signals", []),
        "active_offers": [o.get("title") for o in merchant.get("offers", []) if o.get("status") == "active"],
        "trigger_kind": trigger.get("kind"),
        "trigger_payload": trigger.get("payload", {}),
        "urgency": trigger.get("urgency"),
        "customer": (customer or {}).get("identity") if customer else None,
    }


def _visible_numbers(view: dict) -> set[float]:
    text = json.dumps(view, ensure_ascii=False)
    nums = {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    extra = set()
    for n in nums:
        if 0 < n < 1:  # fractions shown as percentages in prose
            extra.update({round(n * 100, 1), round(n * 100)})
    ctr = view["performance"].get("ctr")
    if ctr:
        extra.add(round(ctr * 100, 1))
    return nums | extra


def _derivable(value: float, base: set[float]) -> bool:
    if value in base:
        return True
    small = [b for b in base if b not in (2026, 2025)]
    for a, b in itertools.permutations(small, 2):
        for derived in (a - b, a * b, a / b if b else None, a + b):
            if derived is not None and abs(derived - value) <= max(0.51, abs(value) * 0.01):
                return True
    return False


def check(body: str, view: dict) -> tuple[list[str], list[str]]:
    base = _visible_numbers(view)
    verified, unverified = [], []
    stripped = re.sub(r"\d[\d,]*" + ACTION_UNITS, " ", body, flags=re.I)
    stripped = re.sub(r"\blast 30 days\b", " ", stripped)  # the performance window itself
    # clock times ("order by 11am", "12:30–1pm") are logistics in a proposal, not claims
    stripped = re.sub(r"\b\d{1,2}(?::\d{2})?(?:\s?[–-]\s?\d{1,2}(?::\d{2})?)?\s?(?:am|pm)\b", " ", stripped, flags=re.I)
    for raw, _pct in NUMBER.findall(stripped):
        raw = raw.rstrip(",")
        value = float(raw.replace(",", ""))
        if 2024 <= value <= 2027 and not _pct:  # a year, not a claim
            continue
        (verified if _derivable(value, base) else unverified).append(raw)
    return verified, unverified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset")
    parser.add_argument(
        "--seeds", action="store_true", help="only the 25 seed triggers (what judge_simulator.py scores)"
    )
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    data = Dataset(dataset_dir(args.dataset))
    triggers = data.all("triggers")
    if args.seeds:
        triggers = [t for t in triggers if int(t["id"].split("_")[1]) <= 25]

    total_ok = total_bad = scored = 0
    for trigger in triggers:
        category, merchant, customer = data.contexts_for(trigger)
        out = compose(category, merchant, trigger, customer, now=SIM_NOW, roster=data.roster(merchant["merchant_id"]))
        ok, bad = check(out["body"], judge_view(category, merchant, trigger, customer))
        scored += 1
        total_ok += len(ok)
        total_bad += len(bad)
        if args.show or bad:
            flag = "  " if not bad else "!!"
            print(f"{flag} {trigger['id']:<48} verified={len(ok):<2} unverifiable={bad}")
            if args.show:
                print("   ", out["body"][:400])
    print(
        f"\n{scored} messages · {total_ok / scored:.1f} verifiable numbers/message · {total_bad} unverifiable numbers in total"
    )


if __name__ == "__main__":
    main()
