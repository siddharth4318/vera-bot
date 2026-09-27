"""Chat with Vera in your terminal — no server, no API key.

Pick a trigger from the challenge dataset, read the message Vera would send,
then reply as the merchant (or customer) and see how Vera handles it.

    python scripts/chat.py                      # uses ../dataset (the challenge pack)
    python scripts/chat.py --dataset path/to/dataset

Type 'back' to pick another trigger, 'quit' to exit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vera import facts as F  # noqa: E402
from vera.composer import compose  # noqa: E402
from vera.conversation import ConversationEngine  # noqa: E402
from vera.store import ContextStore  # noqa: E402

NOW = datetime(2026, 4, 26, 10, 30, tzinfo=timezone.utc)  # the dataset's "today"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load(root: Path) -> ContextStore:
    store = ContextStore()
    for f in (root / "categories").glob("*.json"):
        cat = read(f)
        store.put("category", cat["slug"], 1, cat)
    for name, scope, key in (
        ("merchants_seed.json", "merchant", "merchant_id"),
        ("customers_seed.json", "customer", "customer_id"),
        ("triggers_seed.json", "trigger", "id"),
    ):
        data = read(root / name)
        for item in data.get(scope + "s", []):
            store.put(scope, item[key], 1, item)
    return store


def pick_trigger(store: ContextStore) -> dict | None:
    triggers = sorted(store.all("trigger").values(), key=lambda t: t["id"])
    print("\nTriggers:")
    for i, t in enumerate(triggers, 1):
        m = store.get("merchant", t.get("merchant_id")) or {}
        who = F.biz_name(m)
        cust = store.get("customer", t.get("customer_id"))
        if cust:
            who += f" → {F.g(cust, 'identity', 'name')}"
        print(f"  {i:>2}. {t['kind']:<28} {who}")
    choice = input("\nPick a number (or 'quit'): ").strip().lower()
    if choice in ("q", "quit", "exit"):
        return None
    try:
        return triggers[int(choice) - 1]
    except (ValueError, IndexError):
        print("Not a valid number.")
        return pick_trigger(store)


def show(who: str, text: str):
    print(f"\n{who}:\n  " + text.replace("\n", "\n  "))


def run(store: ContextStore):
    convs = ConversationEngine(store)
    while True:
        trg = pick_trigger(store)
        if trg is None:
            return
        m = store.get("merchant", trg.get("merchant_id"))
        cat = store.get("category", m.get("category_slug"))
        cust = store.get("customer", trg.get("customer_id"))
        roster = [c for c in store.all("customer").values() if c.get("merchant_id") == m.get("merchant_id")]
        out = compose(cat, m, trg, cust, now=NOW, roster=roster)

        sender = f"Vera (on behalf of {F.biz_name(m)})" if out["send_as"] == "merchant_on_behalf" else "Vera"
        show(sender, out["body"])
        print(f"\n  [cta: {out['cta']}]  [why: {out['rationale'][:160]}...]")
        if not out.get("_consent_ok", True):
            print("  (Blocked in production: this customer hasn't opted in.)")

        conv_id = f"chat_{trg['id']}"
        lang = F.customer_lang(cust) if cust else F.merchant_lang(cat, m)
        lang = "hinglish" if lang in ("hi", "hinglish") else "en"
        convs.open(
            conv_id,
            {
                **out,
                "merchant_id": m["merchant_id"],
                "customer_id": trg.get("customer_id"),
                "trigger_id": trg["id"],
                "_kind": trg["kind"],
            },
            out.get("_next") or {},
            lang,
        )
        you = "You (customer)" if cust and out["send_as"] == "merchant_on_behalf" else "You (merchant)"
        turn = 1
        while True:
            text = input(f"\n{you}: ").strip()
            if text.lower() in ("quit", "exit"):
                return
            if text.lower() == "back":
                break
            if not text:
                continue
            turn += 1
            r = convs.reply(
                {
                    "conversation_id": conv_id,
                    "merchant_id": m["merchant_id"],
                    "customer_id": trg.get("customer_id"),
                    "from_role": "customer" if you.endswith("(customer)") else "merchant",
                    "message": text,
                    "received_at": NOW.isoformat(),
                    "turn_number": turn,
                }
            )
            if r.get("action") == "send":
                show("Vera", r.get("body", ""))
            elif r.get("action") == "wait":
                print(f"\n  (Vera waits {r.get('wait_seconds')}s — {r.get('rationale', '')})")
            else:
                print(f"\n  (Vera ends the conversation — {r.get('rationale', '')})")
                break


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # ₹ and emoji on Windows consoles
    here = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=str(here.parent / "dataset"))
    root = Path(parser.parse_args().dataset)
    if not (root / "triggers_seed.json").exists():
        sys.exit(f"Dataset not found at {root}. Pass --dataset path/to/challenge/dataset")
    run(load(root))


if __name__ == "__main__":
    main()
