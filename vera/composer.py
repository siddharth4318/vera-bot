"""
compose(category, merchant, trigger, customer=None, now=None, roster=None) -> dict

Deterministic entry point. Routing:
    trigger.kind  -> exact handler
                  -> alias / keyword route (for kinds we haven't seen before)
                  -> generic reasoner
then a guardrail pass (taboos, URLs, repetition, consent) and packaging.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import facts as F
from .core import Ctx, Draft
from .handlers import customer as cust_handlers
from .handlers import events, knowledge, lifecycle, performance, planning, reputation
from .handlers.fallback import generic

# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

MERCHANT_HANDLERS = {
    "research_digest": knowledge.research_digest,
    "research_digest_release": knowledge.research_digest,
    "category_research_digest_release": knowledge.research_digest,
    "regulation_change": knowledge.regulation_change,
    "compliance_alert": knowledge.regulation_change,
    "cde_opportunity": knowledge.cde_opportunity,
    "category_seasonal": knowledge.category_seasonal,
    "perf_dip": performance.perf_dip,
    "perf_spike": performance.perf_spike,
    "seasonal_perf_dip": performance.seasonal_perf_dip,
    "milestone_reached": performance.milestone_reached,
    "review_theme_emerged": reputation.review_theme_emerged,
    "competitor_opened": reputation.competitor_opened,
    "renewal_due": lifecycle.renewal_due,
    "winback_eligible": lifecycle.winback_eligible,
    "dormant_with_vera": lifecycle.dormant_with_vera,
    "gbp_unverified": lifecycle.gbp_unverified,
    "festival_upcoming": events.festival_upcoming,
    "ipl_match_today": events.ipl_match_today,
    "curious_ask_due": planning.curious_ask_due,
    "scheduled_recurring": planning.curious_ask_due,
    "active_planning_intent": planning.active_planning_intent,
    "supply_alert": knowledge.supply_alert,
    "weather_heatwave": events.weather_event,
    "local_news_event": events.local_event,
}

CUSTOMER_HANDLERS = {
    "recall_due": cust_handlers.recall_due,
    "appointment_tomorrow": cust_handlers.appointment_tomorrow,
    "customer_lapsed_soft": cust_handlers.customer_lapsed,
    "customer_lapsed_hard": cust_handlers.customer_lapsed,
    "trial_followup": cust_handlers.trial_followup,
    "chronic_refill_due": cust_handlers.chronic_refill_due,
    "wedding_package_followup": cust_handlers.wedding_followup,
    "bridal_followup": cust_handlers.wedding_followup,
}

KEYWORD_ROUTES = [  # for unseen kinds
    (
        ("news", "traffic", "closure", "closed", "strike", "bandh", "event", "protest", "metro", "road"),
        events.local_event,
    ),
    (("research", "digest", "journal", "study"), knowledge.research_digest),
    (("regulat", "compliance", "circular", "audit"), knowledge.regulation_change),
    (("recall", "supply", "batch", "shortage"), knowledge.supply_alert),
    (("webinar", "cde", "training", "workshop", "conference"), knowledge.cde_opportunity),
    (("weather", "heat", "rain", "monsoon", "cold_wave", "flood"), events.weather_event),
    (("festival", "holiday", "diwali", "holi", "eid", "christmas"), events.festival_upcoming),
    (("match", "ipl", "cricket", "sport"), events.ipl_match_today),
    (("competitor", "rival", "new_listing"), reputation.competitor_opened),
    (("review", "rating"), reputation.review_theme_emerged),
    (("spike", "surge", "jump"), performance.perf_spike),
    (("dip", "drop", "decline", "fall"), performance.perf_dip),
    (("milestone", "crossed"), performance.milestone_reached),
    (("renew", "expir", "subscription"), lifecycle.renewal_due),
    (("dormant", "inactive", "silent"), lifecycle.dormant_with_vera),
    (("winback", "win_back"), lifecycle.winback_eligible),
    (("verif", "gbp", "profile_incomplete"), lifecycle.gbp_unverified),
    (("season", "trend"), knowledge.category_seasonal),
    (("curious", "ask", "question"), planning.curious_ask_due),
    (("planning", "intent"), planning.active_planning_intent),
]

CUSTOMER_KEYWORD_ROUTES = [
    (("appointment", "booking", "reminder"), cust_handlers.appointment_tomorrow),
    (("lapse", "winback", "inactive", "churn"), cust_handlers.customer_lapsed),
    (("refill", "chronic", "medicine"), cust_handlers.chronic_refill_due),
    (("trial",), cust_handlers.trial_followup),
    (("wedding", "bridal"), cust_handlers.wedding_followup),
    (("recall", "due", "slot", "checkup"), cust_handlers.recall_due),
]


def route(kind: str, customer_facing: bool):
    k = (kind or "").lower()
    if customer_facing:
        if k in CUSTOMER_HANDLERS:
            return CUSTOMER_HANDLERS[k]
        for kws, fn in CUSTOMER_KEYWORD_ROUTES:
            if any(w in k for w in kws):
                return fn
        return cust_handlers.generic_customer
    if k in MERCHANT_HANDLERS:
        return MERCHANT_HANDLERS[k]
    for kws, fn in KEYWORD_ROUTES:
        if any(w in k for w in kws):
            return fn
    return generic


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #

URL_RE = re.compile(r"https?://\S+|www\.\S+")


def scrub(body: str, cat: dict) -> tuple[str, list[str]]:
    notes = []
    if URL_RE.search(body):
        body = URL_RE.sub("", body)
        notes.append("url_removed")
    for t in F.g(cat, "voice", "vocab_taboo") or []:
        t0 = re.sub(r"\s*\(.*?\)", "", str(t)).strip()
        if t0 and re.search(re.escape(t0), body, flags=re.I):
            body = re.sub(re.escape(t0), "", body, flags=re.I)
            notes.append(f"taboo_removed:{t0}")
    # internal jargon -> words a merchant actually uses
    for pat, rep in (
        (r"\bGBP description\b", "Google profile description"),
        (r"\bon GBP\b", "on Google profiles"),
        (r"\bGBP\b", "Google profile"),
        (r"\bmagicpin internal\b", "magicpin data"),
    ):
        if re.search(pat, body):
            body = re.sub(pat, rep, body)
            notes.append("jargon_softened")
    body = re.sub(r"[ \t]{2,}", " ", body).strip()
    return body, notes


# these handlers already build on the merchant's open request themselves
HANDLES_OPEN_LOOP = {"active_planning_intent", "supply_alert", "cde_opportunity"}


def _acknowledge_open_loop(c: Ctx, body: str, customer_facing: bool) -> str:
    """If the merchant said yes to something Vera still owes, say it's coming
    before starting a new topic — dropping a live thread reads as a bot."""
    if customer_facing or c.trg.get("kind") in HANDLES_OPEN_LOOP:
        return body
    owed = F.owed_item(c.m)
    if not owed:
        return body
    thing, focus = owed
    when = F.day_month((F.last_merchant_turn(c.m) or {}).get("ts"))
    plural = bool(re.search(r"\b([2-9]|\d{2,})\b", thing)) or thing.endswith("s")
    note = f"{thing}{f' on {focus}' if focus else ''} you asked for{f' ({when})' if when else ''}"
    note = f"quick note — {note} {'are' if plural else 'is'} coming separately."
    for opener in (f"Hi {c.sal}! ", f"{c.sal}, "):
        if body.startswith(opener):
            rest = body[len(opener) :]
            c.cite("merchant.conversation_history (open request acknowledged)")
            if opener.endswith("! "):
                return f"{opener}{note[:1].upper()}{note[1:]} {rest[:1].upper()}{rest[1:]}"
            return f"{opener}{note} {rest[:1].upper()}{rest[1:]}"
    return body


def consent_ok(cust: dict | None) -> bool:
    if not cust:
        return True
    if F.g(cust, "preferences", "reminder_opt_in") is False:
        return False
    scope = F.g(cust, "consent", "scope")
    return scope is None or len(scope) > 0


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: dict | None = None,
    now: datetime | None = None,
    roster: list[dict] | None = None,
) -> dict:
    category, merchant, trigger = F.fix_text(category or {}), F.fix_text(merchant or {}), F.fix_text(trigger or {})
    customer = F.fix_text(customer) if customer else customer
    customer_facing = bool(customer) and (trigger.get("scope") == "customer" or trigger.get("customer_id"))
    c = Ctx(category, merchant, trigger, customer if customer_facing else None, now, roster or [])
    fn = route(trigger.get("kind", ""), customer_facing)
    try:
        d: Draft = fn(c)
    except Exception as e:  # never fail a send because one field was odd
        c.used.append(f"handler_error:{type(e).__name__}")
        d = cust_handlers.generic_customer(c) if customer_facing else generic(c)

    body = _acknowledge_open_loop(c, d.body, customer_facing)
    body, notes = scrub(body, category)

    # never resend something Vera already said to this merchant; the API layer skips these
    prior = {t.get("body", "").strip() for t in (merchant.get("conversation_history") or []) if t.get("from") == "vera"}
    if body in prior:
        notes.append("repeat_of_history")

    send_as = "merchant_on_behalf" if customer_facing else "vera"
    sup = (
        trigger.get("suppression_key")
        or f"{trigger.get('kind', 'msg')}:{merchant.get('merchant_id')}:{(customer or {}).get('customer_id', '')}"
    )
    kind = trigger.get("kind", "generic")
    consent = consent_ok(customer) if customer_facing else True

    rationale = d.rationale
    if d.lever:
        rationale += f" Levers: {d.lever.replace('_', ' ').replace('+', ', ')}."
    if c.used:
        rationale += " Facts: " + "; ".join(c.used[:6]) + "."
    if not consent:
        rationale += " BLOCKED: customer has no opt-in consent — do not send."

    first = F.salutation(c.slug, merchant) if not customer_facing else (F.cust_names(customer)[1] or "there")
    sentences = re.split(r"(?<=[.!?])\s+", body)
    params = [
        first,
        " ".join(sentences[1:-1])[:500] if len(sentences) > 2 else body[:500],
        sentences[-1] if sentences else "",
    ]

    return {
        "body": body,
        "cta": d.cta,
        "send_as": send_as,
        "suppression_key": sup,
        "rationale": rationale.strip(),
        "template_name": f"{'merchant' if customer_facing else 'vera'}_{kind}_v1",
        "template_params": params,
        # internal fields, not part of the API contract
        "_next": d.next_action,
        "_consent_ok": consent,
        "_guardrails": notes,
    }
