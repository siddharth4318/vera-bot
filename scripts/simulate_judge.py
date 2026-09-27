"""Run a judge-style session against a running bot and check the behaviour we care about.

    uvicorn app:app --port 8080 &
    python scripts/simulate_judge.py [--url http://127.0.0.1:8080] [--dataset expanded]

It mirrors the phases in challenge-testing-brief.md: warmup, context pushes,
ticks, merchant replies, the three replay scenarios and a mid-test context
update. It doesn't score message quality — use the official judge_simulator.py
with an LLM key for that.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dataset import Dataset, dataset_dir  # noqa: E402

QUALIFYING = ("would you", "do you", "can you tell", "what if", "how about")
AUTO_REPLY = "Thank you for contacting us! Our team will respond shortly."
REQUIRED_ACTION_FIELDS = (
    "conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id",
    "template_name", "template_params", "body", "cta", "suppression_key", "rationale",
)  # fmt: skip


class Bot:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.latencies: list[float] = []

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(self.url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        start = time.time()
        try:
            with request.urlopen(req, timeout=30) as resp:
                status, payload = resp.status, json.loads(resp.read())
        except error.HTTPError as exc:
            status, payload = exc.code, json.loads(exc.read() or b"{}")
        self.latencies.append(time.time() - start)
        return status, payload

    def push(self, scope: str, context_id: str, payload: dict, version: int = 1) -> tuple[int, dict]:
        return self.call(
            "POST", "/v1/context", {"scope": scope, "context_id": context_id, "version": version, "payload": payload}
        )

    def reply(self, conversation_id: str, merchant_id: str, message: str, customer_id: str | None = None) -> dict:
        role = "customer" if customer_id else "merchant"
        body = {"conversation_id": conversation_id, "merchant_id": merchant_id, "customer_id": customer_id,
                "from_role": role, "message": message, "received_at": "2026-04-26T10:00:00Z", "turn_number": 2}  # fmt: skip
        return self.call("POST", "/v1/reply", body)[1]


class Report:
    def __init__(self):
        self.failures = 0

    def check(self, ok: bool, label: str) -> None:
        print(("  PASS  " if ok else "  FAIL  ") + label)
        self.failures += not ok


def warmup(bot: Bot, data: Dataset, report: Report) -> None:
    print("\nwarmup")
    bot.call("POST", "/v1/teardown", {})
    for slug, category in data.categories.items():
        bot.push("category", slug, category)
    for merchant in data.all("merchants"):
        bot.push("merchant", merchant["merchant_id"], merchant)
    for customer in data.all("customers"):
        bot.push("customer", customer["customer_id"], customer)
    _, health = bot.call("GET", "/v1/healthz")
    expected = {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}
    report.check(health["contexts_loaded"] == expected, f"contexts loaded {health['contexts_loaded']}")

    meera = data.merchant("m_001_drmeera_dentist_delhi")
    status, body = bot.push("merchant", meera["merchant_id"], meera)
    report.check(status == 200 and body.get("noop"), "identical re-push is an idempotent no-op")
    changed = {**meera, "performance": {**meera["performance"], "views": 1}}
    status, _ = bot.push("merchant", meera["merchant_id"], changed)
    report.check(status == 409, "same version with different data is rejected")
    status, _ = bot.push("merchant", meera["merchant_id"], meera, version=0)
    report.check(status == 409, "older version is rejected")
    status, _ = bot.call("POST", "/v1/context", {"scope": "nope", "context_id": "x", "version": 1, "payload": {}})
    report.check(status == 400, "unknown scope is a 400")


def test_window(bot: Bot, data: Dataset, report: Report) -> list[dict]:
    print("\ntest window")
    trigger_ids = []
    for trigger in data.all("triggers"):
        bot.push("trigger", trigger["id"], trigger)
        trigger_ids.append(trigger["id"])

    actions = []
    for tick, start in enumerate(range(0, len(trigger_ids), 9)):
        now = f"2026-04-26T{10 + tick:02d}:00:00Z"
        _, body = bot.call("POST", "/v1/tick", {"now": now, "available_triggers": trigger_ids[start : start + 9]})
        actions += body["actions"]

    report.check(
        all(all(k in a for k in REQUIRED_ACTION_FIELDS) for a in actions), f"{len(actions)} actions, all fields present"
    )
    bodies = [a["body"] for a in actions]
    report.check(len(bodies) == len(set(bodies)), "no duplicate message bodies")
    report.check(not any("http" in b for b in bodies), "no URLs in messages")
    _, again = bot.call("POST", "/v1/tick", {"now": "2026-04-26T23:00:00Z", "available_triggers": trigger_ids[:9]})
    sent = {a["trigger_id"] for a in actions}
    report.check(not any(a["trigger_id"] in sent for a in again["actions"]), "suppression keys are not reused")
    return actions


def replay_scenarios(bot: Bot, actions: list[dict], report: Report) -> None:
    print("\nreplay scenarios")
    meera_conv = next(a for a in actions if a["merchant_id"].startswith("m_001") and not a["customer_id"])
    steps = [
        bot.reply(meera_conv["conversation_id"], meera_conv["merchant_id"], AUTO_REPLY)["action"] for _ in range(4)
    ]
    report.check(steps[:3] == ["send", "wait", "end"], f"auto-reply in one conversation: {steps}")

    steps = [bot.reply(f"conv_auto_{i}", "m_010_sunrisepharm_pharmacy_lucknow", AUTO_REPLY)["action"] for i in range(4)]
    report.check("end" in steps, f"auto-reply across conversation ids: {steps}")

    for message in ("What kind of posts?", "Mostly whitening I think"):
        bot.reply("conv_intent", "m_001_drmeera_dentist_delhi", message)
    final = bot.reply("conv_intent", "m_001_drmeera_dentist_delhi", "Ok, let's do it. What's next?")
    text = (final.get("body") or "").lower()
    report.check(
        final["action"] == "send" and not any(q in text for q in QUALIFYING), "commitment switches to action mode"
    )

    first = bot.reply(
        "conv_hostile", "m_005_pizzajunction_restaurant_delhi", "You people are useless, why do you keep bothering me"
    )
    second = bot.reply("conv_hostile", "m_005_pizzajunction_restaurant_delhi", "can you also help me file my GST?")
    report.check(first["action"] == "send" and "STOP" in first["body"], "hostility gets one de-escalation line")
    report.check("CA" in second.get("body", ""), "off-topic request is declined and redirected")
    stop = bot.reply("conv_stop", "m_009_apollo_pharmacy_jaipur", "Stop messaging me. This is useless spam.")
    report.check(stop["action"] == "end", "explicit opt-out ends the conversation")


def adaptive_injection(bot: Bot, data: Dataset, report: Report) -> None:
    print("\nmid-test context update")
    bharat = data.merchant("m_002_bharat_dentist_mumbai")
    bharat["performance"]["delta_7d"]["calls_pct"] = -0.62
    bot.push("merchant", bharat["merchant_id"], bharat, version=2)
    trigger = {"id": "trg_live_dip", "scope": "merchant", "kind": "perf_dip", "merchant_id": bharat["merchant_id"],
               "payload": {}, "urgency": 4, "suppression_key": "live_dip"}  # fmt: skip
    bot.push("trigger", trigger["id"], trigger)
    _, body = bot.call("POST", "/v1/tick", {"now": "2026-04-27T10:00:00Z", "available_triggers": [trigger["id"]]})
    report.check(
        bool(body["actions"]) and "62%" in body["actions"][0]["body"], "new merchant version is used immediately"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--dataset")
    args = parser.parse_args()

    bot, data, report = Bot(args.url), Dataset(dataset_dir(args.dataset)), Report()
    warmup(bot, data, report)
    actions = test_window(bot, data, report)
    replay_scenarios(bot, actions, report)
    adaptive_injection(bot, data, report)

    print(f"\n{len(bot.latencies)} calls, slowest {max(bot.latencies) * 1000:.0f} ms, {report.failures} failure(s)")
    sys.exit(1 if report.failures else 0)


if __name__ == "__main__":
    main()
