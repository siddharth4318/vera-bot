"""
Optional multi-turn interface (challenge-brief §7.4).

    respond(state, merchant_message) -> {action, body?, cta?, wait_seconds?, rationale}

`state` = {"conversation_id", "merchant_id", "customer_id"?, "from_role"?,
           "category": {...}, "merchant": {...}, "customer": {...}?,
           "pending": {...}?}  -- contexts are loaded into a private store.
"""

from __future__ import annotations

from vera.conversation import ConversationEngine
from vera.store import ContextStore

_store = ContextStore()
_engine = ConversationEngine(_store)


def respond(state: dict, merchant_message: str) -> dict:
    m = state.get("merchant") or {}
    if m:
        _store.put("merchant", m.get("merchant_id", state.get("merchant_id", "m")), 1, m)
    if state.get("category"):
        _store.put("category", state["category"].get("slug", "cat"), 1, state["category"])
    cid = state.get("conversation_id", "conv_local")
    if state.get("pending") and cid not in _engine.convs:
        _engine.open(
            cid,
            {
                "merchant_id": state.get("merchant_id"),
                "customer_id": state.get("customer_id"),
                "send_as": "merchant_on_behalf" if state.get("customer_id") else "vera",
                "body": "",
            },
            state["pending"],
            state.get("lang", "en"),
        )
    return _engine.reply(
        {
            "conversation_id": cid,
            "merchant_id": state.get("merchant_id") or m.get("merchant_id"),
            "customer_id": state.get("customer_id"),
            "from_role": state.get("from_role", "merchant"),
            "message": merchant_message,
        }
    )
