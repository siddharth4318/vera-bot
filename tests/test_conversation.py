import pytest

from vera.conversation import ConversationEngine, detect_lang

AUTO = "Thank you for contacting us! Our team will respond shortly."
QUALIFYING = ("would you", "do you", "can you tell", "what if", "how about")


@pytest.fixture
def engine(store):
    return ConversationEngine(store)


def say(engine, conv, message, role="merchant"):
    return engine.reply({"conversation_id": conv, "merchant_id": "m_test", "from_role": role, "message": message})


def test_auto_reply_backs_off_then_exits(engine):
    assert [say(engine, "c1", AUTO)["action"] for _ in range(3)] == ["send", "wait", "end"]


def test_auto_reply_is_tracked_across_conversations(engine):
    assert [say(engine, f"c{i}", AUTO)["action"] for i in range(3)] == ["send", "wait", "end"]


def test_opt_out_ends(engine):
    assert say(engine, "c", "Not interested, stop messaging me")["action"] == "end"


def test_commitment_goes_straight_to_action(engine):
    out = say(engine, "c", "ok lets do it, whats next?")
    assert out["action"] == "send"
    assert "draft" in out["body"].lower()
    assert not any(q in out["body"].lower() for q in QUALIFYING)


def test_confirm_then_thanks_closes(engine):
    say(engine, "c", "yes")
    assert "Confirm" in say(engine, "c", "confirm")["body"]
    assert say(engine, "c", "thanks")["action"] == "end"


@pytest.mark.parametrize(("message", "seconds"), [("busy, message me tomorrow", 86400), ("in a meeting, 30 min", 1800)])
def test_deferral_waits(engine, message, seconds):
    out = say(engine, "c", message)
    assert out == {"action": "wait", "wait_seconds": seconds, "rationale": out["rationale"]}


def test_off_topic_is_declined_politely(engine):
    body = say(engine, "c", "Can you help me with my GST filing?")["body"]
    assert "CA" in body and "YES" in body


def test_hostility_gets_one_line_then_exit(engine):
    first = say(engine, "c", "this is useless, why are you bothering me")
    assert first["action"] == "send" and "STOP" in first["body"]
    assert say(engine, "c", "fine whatever")["action"] == "end"


def test_never_repeats_itself(engine):
    say(engine, "c", "yes")
    say(engine, "c", "confirm")
    bodies = [say(engine, "c", "ok")] + [say(engine, "c", "ok") for _ in range(3)]
    sent = [b["body"] for b in bodies if b["action"] == "send"]
    assert len(sent) == len(set(sent))


def test_language_follows_the_merchant():
    assert detect_lang("haan bhej do", "en") == "hinglish"
    assert detect_lang("Please send me the draft today", "hinglish") == "en"
