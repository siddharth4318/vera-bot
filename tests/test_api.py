import pytest
from fastapi.testclient import TestClient

import app as server


@pytest.fixture
def client(dentists, clinic):
    server.teardown()
    c = TestClient(server.app)
    c.post("/v1/context", json={"scope": "category", "context_id": "dentists", "version": 1, "payload": dentists})
    c.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 1, "payload": clinic})
    return c


def push_trigger(client, trigger_id="t1", kind="perf_dip", **extra):
    trigger = {"id": trigger_id, "scope": "merchant", "kind": kind, "merchant_id": "m_test", "payload": {},
               "urgency": 3, "suppression_key": f"sk_{trigger_id}", **extra}  # fmt: skip
    client.post("/v1/context", json={"scope": "trigger", "context_id": trigger_id, "version": 1, "payload": trigger})


def test_healthz_counts_contexts(client):
    assert client.get("/v1/healthz").json()["contexts_loaded"] == {
        "category": 1,
        "merchant": 1,
        "customer": 0,
        "trigger": 0,
    }


def test_context_versioning(client, clinic):
    same = client.post(
        "/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 1, "payload": clinic}
    )
    assert same.status_code == 200 and same.json()["noop"]
    older = client.post(
        "/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 0, "payload": clinic}
    )
    assert older.status_code == 409 and older.json()["current_version"] == 1
    bad = client.post("/v1/context", json={"scope": "nope", "context_id": "x", "version": 1, "payload": {}})
    assert bad.status_code == 400


def test_tick_sends_once_per_suppression_key(client):
    push_trigger(client)
    first = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["t1"]}).json()
    again = client.post("/v1/tick", json={"now": "2026-04-26T10:05:00Z", "available_triggers": ["t1"]}).json()
    assert len(first["actions"]) == 1 and again["actions"] == []
    assert first["actions"][0]["conversation_id"].startswith("conv_m_test")


def test_per_merchant_cap_per_tick(client):
    push_trigger(client, "t1", urgency=2)
    push_trigger(client, "t2", kind="gbp_unverified", urgency=4)
    push_trigger(client, "t3", kind="gbp_unverified", urgency=3)
    push_trigger(client, "t4", kind="renewal_due", urgency=1)
    actions = client.post(
        "/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["t1", "t2", "t3", "t4"]}
    ).json()["actions"]
    # highest urgency first, never two of the same kind, at most two per merchant
    assert [a["trigger_id"] for a in actions] == ["t2", "t1"]
    assert len({a["conversation_id"] for a in actions}) == 2


def test_reply_continues_the_ticked_conversation(client):
    push_trigger(client)
    action = client.post("/v1/tick", json={"available_triggers": ["t1"]}).json()["actions"][0]
    reply = client.post("/v1/reply", json={"conversation_id": action["conversation_id"], "merchant_id": "m_test",
                                           "from_role": "merchant", "message": "yes please"}).json()  # fmt: skip
    assert reply["action"] == "send" and "Dental Cleaning" in reply["body"]


def test_opted_out_merchant_gets_no_more_messages(client):
    push_trigger(client, "t1")
    push_trigger(client, "t2", kind="gbp_unverified")
    action = client.post("/v1/tick", json={"available_triggers": ["t1"]}).json()["actions"][0]
    client.post("/v1/reply", json={"conversation_id": action["conversation_id"], "merchant_id": "m_test",
                                   "from_role": "merchant", "message": "stop messaging me"})  # fmt: skip
    assert client.post("/v1/tick", json={"available_triggers": ["t2"]}).json()["actions"] == []


def test_context_push_repairs_windows_mojibake(client):
    garbled = "Dental Cleaning @ ₹299 — today".encode().decode("cp1252")
    body = {"scope": "trigger", "context_id": "tm", "version": 1,
            "payload": {"id": "tm", "kind": "perf_dip", "merchant_id": "m_test", "payload": {"note": garbled}}}  # fmt: skip
    assert client.post("/v1/context", json=body).json()["accepted"]
    from app import store

    assert store.get("trigger", "tm")["payload"]["note"] == "Dental Cleaning @ ₹299 — today"
