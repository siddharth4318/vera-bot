"""
Vera message engine — HTTP API used by the magicpin evaluation harness.

    POST /v1/context   push/replace a versioned context (idempotent)
    POST /v1/tick      decide which triggers deserve a message right now
    POST /v1/reply     continue a conversation
    GET  /v1/healthz   liveness + loaded-context counts
    GET  /v1/metadata  team / approach
    POST /v1/teardown  wipe state at end of test

Run:  uvicorn app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FTimeout
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from vera import facts as F
from vera import llm
from vera.composer import compose
from vera.conversation import ConversationEngine
from vera.store import SCOPES, ContextStore

START = time.time()
VERSION = "1.0.0"
app = FastAPI(title="Vera message engine", version=VERSION)

store = ContextStore()
convs = ConversationEngine(store)
sent_keys: set[str] = set()  # suppression keys already used
sent_bodies: dict[str, set] = {}  # merchant_id -> bodies sent (anti-repeat)
MAX_ACTIONS = 20
PER_MERCHANT_PER_TICK = 2  # separate conversations; never two of the same kind
TICK_BUDGET_S = float(os.getenv("VERA_TICK_BUDGET_S", "22"))
_pool = ThreadPoolExecutor(max_workers=8)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
@app.get("/v1/healthz")
@app.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": store.counts()}


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": os.getenv("VERA_TEAM_NAME", "Team Siddharth"),
        "team_members": [m.strip() for m in os.getenv("VERA_TEAM_MEMBERS", "Siddharth Jain").split(",")],
        "model": llm.model_label(),
        "approach": (
            "Deterministic decision engine: per-trigger handlers + a merchant-state hook ranker pick ONE signal, "
            "compose from verified context facts only (provenance logged in rationale), guardrails for taboos/URLs/consent/repetition; "
            "optional temperature-0 LLM polish that is rejected if it adds any number not in context. "
            "Stateful reply engine: auto-reply detection across conversations, instant action-mode on commitment, graceful exits."
        ),
        "contact_email": os.getenv("VERA_CONTACT_EMAIL", "itssiddharth.18@gmail.com"),
        "version": VERSION,
        "submitted_at": os.getenv("VERA_SUBMITTED_AT", "2026-09-27T00:00:00Z"),
    }


# --------------------------------------------------------------------------- #
@app.post("/v1/context")
async def push_context(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_json"})
    scope, cid, ver, payload = body.get("scope"), body.get("context_id"), body.get("version"), body.get("payload")
    if scope not in SCOPES:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {SCOPES}"},
        )
    if not cid or not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "invalid_payload",
                "details": "context_id and object payload required",
            },
        )
    try:
        ver = int(ver if ver is not None else 1)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_version"})
    payload = F.fix_text(payload)  # undo cp1252-decoded UTF-8 ('â‚¹' -> '₹') before storing
    status, cur = store.put(scope, cid, ver, payload)
    if status == "stale":
        return JSONResponse(
            status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": cur}
        )
    if status == "conflict":
        return JSONResponse(
            status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": cur}
        )
    if status == "noop":
        # identical re-post of the same version: idempotent no-op, state unchanged (brief §2.1)
        return {"accepted": True, "ack_id": f"ack_{cid}_v{ver}", "stored_at": now_iso(), "noop": True}
    return {"accepted": True, "ack_id": f"ack_{cid}_v{ver}", "stored_at": now_iso()}


# --------------------------------------------------------------------------- #
def _conv_id(m: dict, trg: dict, cust_id: str | None) -> str:
    mid = str(m.get("merchant_id", "m"))
    short = "_".join(mid.split("_")[:2])
    who = ("_" + "_".join(str(cust_id).split("_")[:2])) if cust_id else ""
    tail = re.sub(r"[^a-zA-Z0-9]+", "_", str(trg.get("suppression_key") or trg.get("id") or ""))[-40:].strip("_")
    return f"conv_{short}{who}_{trg.get('kind', 'msg')}_{tail}"


def _plan(tick_now: datetime | None, trigger_ids: list[str]) -> list[tuple]:
    """Choose which triggers to act on. Restraint rules:
    - unknown trigger/merchant/category -> skip
    - suppression_key already used -> skip
    - merchant opted out -> skip
    - customer-scope without customer context or consent -> skip
    - at most two sends per merchant (or merchant+customer) per tick, each in its
      own conversation and of a different kind; highest urgency first
    """
    cands = []
    for tid in trigger_ids:
        trg = store.get("trigger", tid)
        if not trg:
            continue
        trg = dict(trg)
        trg.setdefault("id", tid)
        mid = trg.get("merchant_id") or (trg.get("payload") or {}).get("merchant_id")
        m = store.get("merchant", mid)
        if not m:
            continue
        cat = store.get("category", m.get("category_slug")) or {}
        if not cat:
            continue
        if mid in convs.opted_out:
            continue
        sk = trg.get("suppression_key") or f"{trg.get('kind')}:{mid}:{trg.get('customer_id')}"
        if sk in sent_keys:
            continue
        cust = None
        if trg.get("scope") == "customer" or trg.get("customer_id"):
            cust = store.get("customer", trg.get("customer_id"))
            if not cust:
                continue
        cands.append((-(trg.get("urgency") or 0), tid, trg, m, cat, cust))
    cands.sort(key=lambda x: (x[0], x[1]))
    chosen, per_key, kinds = [], {}, set()
    for _, _tid, trg, m, cat, cust in cands:
        key = (m.get("merchant_id"), cust.get("customer_id") if cust else None)
        kind_key = (key, trg.get("kind"))
        if per_key.get(key, 0) >= PER_MERCHANT_PER_TICK or kind_key in kinds:
            continue
        per_key[key] = per_key.get(key, 0) + 1
        kinds.add(kind_key)
        chosen.append((trg, m, cat, cust))
        if len(chosen) >= MAX_ACTIONS:
            break
    return chosen


def _roster(merchant_id: str | None) -> list[dict]:
    return [c for c in store.all("customer").values() if c.get("merchant_id") == merchant_id]


def _build_action(trg, m, cat, cust, tick_now):
    r = compose(cat, m, trg, cust, now=tick_now, roster=_roster(m.get("merchant_id")))
    if not r.get("_consent_ok", True) or "repeat_of_history" in r.get("_guardrails", []):
        return None
    r = llm.maybe_polish(r, cat, m, trg, cust)
    return r


@app.post("/v1/tick")
async def tick(request: Request):
    t0 = time.time()
    try:
        body = await request.json()
    except Exception:
        body = {}
    tick_now = F.parse_dt(body.get("now"))
    trig_ids = body.get("available_triggers") or []
    if not trig_ids:  # no hint from the caller: consider every stored trigger
        trig_ids = sorted(store.all("trigger").keys())
    plan = _plan(tick_now, trig_ids)
    futures = [(p, _pool.submit(_build_action, *p, tick_now)) for p in plan]
    actions = []
    for (trg, m, cat, cust), fut in futures:
        remaining = max(0.5, TICK_BUDGET_S - (time.time() - t0))
        try:
            r = fut.result(timeout=remaining)
        except FTimeout:
            r = compose(cat, m, trg, cust, now=tick_now)  # deterministic fallback
        except Exception:
            continue
        if not r:
            continue
        mid = m.get("merchant_id")
        if r["body"] in sent_bodies.get(mid, set()):
            continue
        cust_id = cust.get("customer_id") if cust else None
        conv_id = _conv_id(m, trg, cust_id)
        action = {
            "conversation_id": conv_id,
            "merchant_id": mid,
            "customer_id": cust_id,
            "send_as": r["send_as"],
            "trigger_id": trg.get("id"),
            "template_name": r["template_name"],
            "template_params": r["template_params"],
            "body": r["body"],
            "cta": r["cta"],
            "suppression_key": r["suppression_key"],
            "rationale": r["rationale"],
        }
        sent_keys.add(r["suppression_key"])
        sent_bodies.setdefault(mid, set()).add(r["body"])
        lang = F.customer_lang(cust) if cust else F.merchant_lang(cat, m)
        lang = "hinglish" if lang in ("hi", "hinglish") else "en"
        convs.open(conv_id, {**action, "_kind": trg.get("kind")}, r.get("_next") or {}, lang)
        actions.append(action)
    return {"actions": actions}


# --------------------------------------------------------------------------- #
@app.post("/v1/reply")
async def reply(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"action": "end", "rationale": "malformed request"})
    try:
        return convs.reply(F.fix_text(body))
    except Exception as e:  # never surface a 500 mid-conversation
        return {
            "action": "wait",
            "wait_seconds": 1800,
            "rationale": f"internal recovery ({type(e).__name__}); backing off",
        }


@app.post("/v1/teardown")
def teardown():
    store.clear()
    convs.reset()
    sent_keys.clear()
    sent_bodies.clear()
    return {"ok": True}


@app.get("/")
def root():
    return {
        "service": "vera-message-engine",
        "version": VERSION,
        "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"],
    }
