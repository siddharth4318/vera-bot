import re

from bot import compose
from vera.composer import compose as compose_full


def trigger(kind, payload=None, **extra):
    return {
        "id": f"t_{kind}",
        "scope": "merchant",
        "kind": kind,
        "merchant_id": "m_test",
        "payload": payload or {},
        **extra,
    }


def test_contract_shape(dentists, clinic):
    out = compose(dentists, clinic, trigger("perf_dip", suppression_key="k1"))
    assert set(out) == {"body", "cta", "send_as", "suppression_key", "rationale"}
    assert out["send_as"] == "vera"
    assert out["suppression_key"] == "k1"


def test_deterministic(dentists, clinic):
    t = trigger("perf_dip")
    assert compose(dentists, clinic, t) == compose(dentists, clinic, t)


def test_perf_dip_uses_real_numbers_and_one_fix(dentists, clinic):
    body = compose(dentists, clinic, trigger("perf_dip"))["body"]
    assert "Dr. Asha" in body
    assert "40%" in body
    assert "Dental Cleaning @ ₹299" in body  # suggested from the catalog, since no offer is live
    assert body.count("YES") == 1


def test_research_digest_cites_source_and_cohort(dentists, clinic):
    body = compose(dentists, clinic, trigger("research_digest", {"top_item_id": "d_fluoride"}))["body"]
    for fact in ("JIDA Oct 2026, p.14", "2,100", "38%", "60 high-risk adults"):
        assert fact in body


def test_no_invented_numbers(dentists, clinic):
    """Every number in the message must come from the inputs."""
    body = compose(dentists, clinic, trigger("research_digest", {"top_item_id": "d_fluoride"}))["body"]
    source = str(dentists) + str(clinic)
    for number in re.findall(r"\d[\d,]*", body):
        assert number.replace(",", "") in source.replace(",", ""), number


def test_unknown_kind_falls_back_gracefully(dentists, clinic):
    out = compose(dentists, clinic, trigger("something_brand_new", {"foo": 3}))
    assert out["body"].startswith("Dr. Asha")


def test_hinglish_closer_for_hindi_speaking_merchant(dentists, clinic):
    body = compose(dentists, clinic, trigger("perf_dip"))["body"]
    assert any(word in body for word in ("kijiye", "bhejiye", "kaafi"))


def test_customer_message_respects_language_and_live_offers(dentists, clinic):
    clinic["offers"] = [{"title": "Dental Cleaning @ ₹299", "status": "active"}]
    customer = {
        "customer_id": "c1",
        "identity": {"name": "Priya", "language_pref": "hi-en mix"},
        "relationship": {"last_visit": "2026-05-12"},
        "preferences": {"preferred_slots": "weekday_evening"},
        "consent": {"scope": ["recall_reminders"]},
    }
    t = {
        "id": "t_recall",
        "scope": "customer",
        "kind": "recall_due",
        "customer_id": "c1",
        "payload": {"service_due": "6_month_cleaning", "last_service_date": "2026-05-12", "due_date": "2026-11-12",
                    "available_slots": [{"label": "Wed 5 Nov, 6pm"}, {"label": "Thu 6 Nov, 5pm"}]},
    }  # fmt: skip
    out = compose(dentists, clinic, t, customer)
    assert out["send_as"] == "merchant_on_behalf"
    assert out["cta"] == "multi_choice_slot"
    assert "Priya" in out["body"] and "Wed 5 Nov, 6pm" in out["body"] and "₹299" in out["body"]
    assert "reply kijiye" in out["body"]


def test_customer_without_consent_is_blocked(dentists, clinic):
    t = {"id": "t3", "scope": "customer", "kind": "recall_due", "customer_id": "c", "payload": {}}
    customer = {
        "customer_id": "c",
        "identity": {"name": "X"},
        "preferences": {"reminder_opt_in": False},
        "consent": {"scope": []},
    }
    out = compose_full(dentists, clinic, t, customer)
    assert out["_consent_ok"] is False


def test_refill_cross_checks_recall_alert():
    pharmacy = {
        "slug": "pharmacies",
        "voice": {},
        "digest": [{"id": "d_recall", "kind": "alert", "title": "Recall: atorvastatin batches"}],
    }
    shop = {"merchant_id": "m_p", "identity": {"name": "Care Pharmacy", "languages": ["en"]},
            "offers": [{"title": "Senior Citizen 15% OFF", "status": "active"}]}  # fmt: skip
    customer = {"customer_id": "c", "identity": {"name": "Mr. Sharma", "language_pref": "english", "senior_citizen": True},
                "preferences": {"channel": "whatsapp_via_son"}, "consent": {"scope": ["refill_reminders"]}}  # fmt: skip
    t = {"id": "t", "scope": "customer", "kind": "chronic_refill_due", "customer_id": "c",
         "payload": {"molecule_list": ["metformin", "atorvastatin"], "stock_runs_out_iso": "2026-04-28"}}  # fmt: skip
    body = compose(pharmacy, shop, t, customer)["body"]
    assert "Sharma ji's 2 regular medicines" in body
    assert "atorvastatin batches are under a voluntary recall" in body
    assert "senior-citizen 15% discount" in body


def test_taboo_words_and_urls_are_scrubbed(dentists, clinic):
    from vera.composer import scrub

    body, notes = scrub("This is guaranteed. See https://example.com now.", dentists)
    assert "guaranteed" not in body and "http" not in body
    assert "url_removed" in notes


def test_open_request_is_acknowledged_before_a_new_topic(dentists, clinic):
    clinic["conversation_history"] = [
        {"ts": "2026-04-24T10:12:00Z", "from": "vera", "body": "Want me to draft 3 posts you can review?"},
        {"ts": "2026-04-24T10:18:00Z", "from": "merchant", "body": "Yes please, focus on whitening", "engagement": "intent_action"},
    ]  # fmt: skip
    body = compose(dentists, clinic, trigger("perf_dip"))["body"]
    assert body.startswith(
        "Dr. Asha, quick note — the 3 posts on whitening you asked for (24 Apr) are coming separately."
    )


def test_milestone_without_payload_celebrates_a_real_peer_comparison(dentists, clinic):
    clinic["performance"]["ctr"] = 0.05  # vs 3.0% peer average
    out = compose(dentists, clinic, trigger("milestone_reached"))
    assert "your CTR is 5.0%, ahead of the 3.0% average for metro solo practices" in out["body"]


def test_rationale_never_claims_an_offer_that_is_not_live(dentists, clinic):
    customer = {
        "customer_id": "c1",
        "identity": {"name": "Reyansh", "language_pref": "en"},
        "relationship": {"last_visit": "2026-04-01", "visits_total": 5, "first_visit": "2025-09-01"},
        "consent": {"scope": ["promotional_offers"]},
    }
    t = trigger("customer_lapsed_soft", scope="customer", customer_id="c1")
    out = compose(dentists, clinic, t, customer)
    assert "check-up slot" in out["body"]
    assert "no offer quoted because none is live" in out["rationale"]


def test_weekly_views_use_the_real_window(dentists, clinic):
    clinic["performance"].update(views=980, window_days=30)
    assert "~229 profile views a week" in compose(dentists, clinic, trigger("perf_dip"))["body"]
