"""Conversation-driven triggers: the weekly curious-ask and follow-through on
something the merchant already asked for."""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft

NOUN = {"restaurants": "dish", "dentists": "treatment", "gyms": "class", "pharmacies": "product"}


def _guess_from_reviews(c: Ctx) -> str | None:
    """A service name the merchant's own happy customers keep mentioning, e.g.
    'balayage' from "Priya is the best for balayage". Names only, no counts."""
    pos = F.review_theme(c.m, "pos")
    quote = (pos or {}).get("common_quote", "").lower()
    if not quote:
        return None
    for word in c.cat.get("voice", {}).get("vocab_allowed") or []:
        if len(word) > 3 and word.lower() in quote:
            return word.lower()
    return None


def curious_ask_due(c: Ctx) -> Draft:
    """Ask one easy question, anchored on the merchant's own call volume, with
    the likely answers already listed so a one-word reply is enough."""
    noun = NOUN.get(c.slug, "service")
    name = F.biz_name(c.m)
    views, calls, _ = F.perf_numbers(c.m)
    offers = F.active_offers(c.m)
    guess = _guess_from_reviews(c)

    opener = c.say(f"Hi {c.sal}! Weekly check-in from Vera.", f"Hi {c.sal}! Vera ka weekly check-in.")
    stats = ""
    if views and calls:
        stats = f"{name} got {F.num(calls)} calls from {F.num(views)} profile views in the last 30 days"
        extras = []
        avg_calls = F.g(c.cat, "peer_stats", "avg_calls_30d")
        if avg_calls and calls >= 2 * avg_calls:
            extras.append(f"more than double the {F.num(avg_calls)}-call average for {F.peer_scope(c.cat)}")
            c.cite("category.peer_stats.avg_calls_30d")
        elif avg_calls and calls > avg_calls:
            extras.append(f"above the {F.num(avg_calls)}-call average for {F.peer_scope(c.cat)}")
            c.cite("category.peer_stats.avg_calls_30d")
        elif F.has_signal(c.m, "above_peer_median_calls"):
            extras.append("that's above the peer median")
        vd = F.delta(c.m, "views_pct")
        if vd is not None and vd >= 0.05:
            extras.append(f"views are up {F.pct(vd)} this week")
            c.cite("merchant.performance.delta_7d.views_pct")
        elif F.has_signal(c.m, "growing_views_7d"):
            extras.append("views are still climbing this week")
        stats += (" — " + ", and ".join(extras) if extras else "") + "."
        c.cite("merchant.performance + signals")

    # the likely answer, backed by the merchant's own reviews
    evidence = ""
    pos = F.review_theme(c.m, "pos")
    if guess and pos and pos.get("common_quote"):
        n = pos.get("occurrences_30d")
        praise = (
            f"{n} reviews this month praise your {F.theme(pos['theme'])}"
            if n
            else f"your reviews keep praising your {F.theme(pos['theme'])}"
        )
        evidence = f"My bet is {guess} — {praise}, and one says \"{pos['common_quote']}\"."
        c.cite("merchant.review_themes")

    options = [f"'{o}'" for o in offers[:2]]
    if guess and not evidence:
        options.append(f"something like {guess}")
        c.cite("merchant.review_themes (service named in praise)")
    if evidence and options:
        question = f"Which {noun} were most of those callers asking about — {', '.join(options)}, or something else?"
    elif len(options) == 1:
        question = f"Which {noun} were most of those callers asking about — still {options[0]}, or something new?"
    elif options:
        listed = ", ".join(options[:-1]) + " or " + options[-1]
        question = f"Which {noun} were most of those callers asking about — {listed}?"
    else:
        question = f"Which {noun} were most of those callers asking about this week?"
    if not stats:
        question = f"Quick one — which {noun} got asked for most at {name} this week?"

    if offers:
        give = c.say(
            "Reply with just the name — I'll turn it into a Google post + a ready WhatsApp reply for price enquiries. 5 minutes, zero effort from you.",
            "Bas naam bhej dijiye — main usse Google post + price enquiries ke liye ready WhatsApp reply bana dungi. 5 minute, aapki taraf se zero mehnat.",
        )
    else:
        c.cite("merchant.offers (none active)")
        give = c.say(
            "There's no live offer on your listing yet, so reply with just the name — I'll make it your first offer + a Google post. 5 minutes, zero effort from you.",
            "Aapki listing par abhi koi live offer nahi hai — bas naam bhejiye, main usse aapka pehla offer + Google post bana dungi. 5 minute, zero mehnat.",
        )
    body = " ".join(x for x in [opener, stats, question, evidence, give] if x)
    return Draft(
        body,
        "open_ended",
        rationale="Weekly curious-ask: anchored on the merchant's own 30-day calls/views (and visible signals), with the likely answers pre-listed from live offers and the service customers praise, so a one-word reply is enough; reciprocity (post + reply template).",
        lever="asking_the_merchant+reciprocity",
        next_action={
            "type": "collect",
            "topic": "in-demand service",
            "guess": guess,
            "artifact": "Google post + WhatsApp price reply for the named service.",
            "confirm_en": "publish the post",
        },
    )


def _reach_line(c: Ctx, audience: str) -> str:
    views, calls, _ = F.perf_numbers(c.m)
    if not views or calls is None:
        return ""
    c.cite("merchant.performance")
    return f"You already pull {F.num(views)} profile views and {F.num(calls)} calls a month — this puts the new offer in front of {audience}."


def _thali(c: Ctx) -> str:
    base = F.offer_matching(F.active_offers(c.m), "thali")
    price = int(F.price_in(base).strip("₹").replace(",", "")) if base and F.price_in(base) else None
    short = F.biz_name(c.m).split(" ")[0]
    loc = F.locality(c.m) or "you"
    if price:
        c.cite("merchant.offers (live thali price)")
        art = (
            f"{short} Corporate Thali — offices near {loc} (suggested pricing, edit freely)\n"
            f"• 10+ thalis a day: ₹{round(price * 0.9)} each (10% off your ₹{price} walk-in)\n"
            f"• 25+ thalis: ₹{round(price * 0.85)} each (15% off) + free delivery\n"
            f"• 50+ thalis: ₹{round(price * 0.8)} each (20% off) + monthly billing\n"
            "• Order by 11am on WhatsApp, delivered by lunch"
        )
    else:
        art = "Corporate lunch package: 3 volume tiers, order by 11am on WhatsApp, delivered by lunch."
    lead = f"{c.sal}, here's the first cut of the corporate thali — edit anything:"
    reach = _reach_line(c, f"every office team searching for lunch near {loc}")
    hist = " ".join(t.get("body", "") for t in c.m.get("conversation_history") or [])
    per_day = re.search(r"(\d+)\s*orders/day", hist)
    if per_day:
        reach = (
            f"At ~{per_day.group(1)} thali orders a day now, a single 25-plate office order more than doubles weekday lunch. "
            + reach
        ).strip()
        c.cite("merchant.conversation_history (orders/day)")
    ask = c.say(
        "Shall I turn this into a Google post + a 3-line WhatsApp for office admins? Reply YES.",
        "Ise Google post + office admins ke liye 3-line WhatsApp mein badal doon? Bas YES bhejiye.",
    )
    return f"{lead}\n\n{art}\n\n" + " ".join(x for x in [reach, ask] if x)


def _kids_camp(c: Ctx) -> str:
    prev = (F.last_vera_turn(c.m) or {}).get("body", "")
    price = F.price_in(prev)
    offers = F.active_offers(c.m)
    trial = F.offer_matching(offers, "month", "trial") or (offers[0] if offers else None)
    pos = F.review_theme(c.m, theme="small_classes")
    fee = (
        f"{price} for the full camp (as I suggested)"
        if price
        else "your call — I'd keep it under one month's adult fee"
    )
    art = (
        f"Kids Yoga Summer Camp — {F.biz_name(c.m)}, {F.locality(c.m)}\n"
        "• Ages 7–12 · 4 weeks · 3 classes a week (45 min)\n"
        f"• Fee: {fee}\n"
        "• Max 10 kids per batch" + (" — small classes are what your reviews praise" if pos else "") + "\n"
        "• Week 4: parents' demo session" + (f" — parents who attend get your '{trial}' offer" if trial else "")
    )
    c.cite("trigger.payload.intent_topic")
    if trial:
        c.cite("merchant.offers (parent upsell)")
    why = "Parents book summer camps early, so the sooner this is live the fuller the batches"
    if trial:
        why += f"; the demo day turns kids' parents into adult members through '{trial}'"
    t2p = F.g(c.m, "customer_aggregate", "trial_to_paid_pct")
    if t2p and trial:
        why += f" — your trial-to-paid rate is already {F.pct(t2p)}"
        c.cite("merchant.customer_aggregate.trial_to_paid_pct")
    elif F.has_signal(c.m, "high_retention"):
        why += " — and with retention as high as yours, those members stay"
        c.cite("merchant.signals.high_retention")
    why += "."
    lead = f"{c.sal}, here's the camp — ready to publish, and every number is yours to change:"
    ask = c.say(
        "Reply YES and I'll post it on Google today + send you the Insta carousel copy.",
        "YES bhejiye — aaj hi Google par post kar dungi + Insta carousel copy bhi.",
    )
    return f"{lead}\n\n{art}\n\n{why} {ask}"


def active_planning_intent(c: Ctx) -> Draft:
    p = c.payload
    topic = str(p.get("intent_topic", ""))
    last = p.get("merchant_last_message") or (F.last_merchant_turn(c.m) or {}).get("body", "")
    offers = F.active_offers(c.m)
    c.cite("trigger.payload.intent_topic + merchant_last_message")
    if "thali" in topic or "corporate" in topic:
        body = _thali(c)
    elif "yoga" in topic or "kids" in topic or "camp" in topic:
        body = _kids_camp(c)
    else:
        lead = f'{c.sal}, picking up your question — "{last}" — here\'s a first draft plan:'
        offer = offers[0] if offers else F.suggested_offer(c.cat, c.m)
        art = f"1) Offer: {offer}\n2) Google post + WhatsApp broadcast\n3) Review results after 7 days"
        reach = _reach_line(c, "the people already finding you on Google")
        ask = c.say("Reply YES to publish step 1 today.", "YES bhejiye, step 1 aaj hi live kar dungi.")
        body = f"{lead}\n\n{art}\n\n" + " ".join(x for x in [reach, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Merchant already expressed intent — skip qualification, hand over a complete, editable artifact built on their live offer price (proposals labelled as suggestions), tie it to their own reach, then one publish CTA.",
        lever="effort_externalization+momentum",
        next_action={
            "type": "deliver",
            "topic": F.humanize(topic) or "plan",
            "artifact": _planning_followup(c, topic),
            "confirm_en": "publish it",
        },
    )


def _planning_followup(c: Ctx, topic: str) -> str:
    name, loc = F.biz_name(c.m), F.locality(c.m) or ""
    if "thali" in topic or "corporate" in topic:
        return (
            f'Google post: "Office lunch sorted — {name} corporate thali for teams near {loc}. Order by 11am, delivered by 1pm."\n'
            f'WhatsApp for office admins: "Hi! {name} here ({loc}). We now deliver fresh thalis for office teams — volume pricing from 10 plates, '
            f'order by 11am on WhatsApp. Want the menu for this week?"'
        )
    if "yoga" in topic or "kids" in topic:
        return (
            f'Google post: "Kids Yoga Summer Camp at {name}, {loc} — ages 7–12, small batches, 4 weeks. Limited seats, WhatsApp to book a trial."\n'
            f"Insta carousel: (1) Camp name + dates (2) What kids learn (3) Small-batch promise (4) Fee + 'Book a trial' CTA"
        )
    return f"Google post + WhatsApp broadcast drafted for {name} around '{(F.active_offers(c.m) or [F.suggested_offer(c.cat, c.m)])[0]}'."
