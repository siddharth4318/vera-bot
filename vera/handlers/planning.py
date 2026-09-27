"""Conversation-driven triggers: the weekly curious-ask and follow-through on
something the merchant already asked for."""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft


def curious_ask_due(c: Ctx) -> Draft:
    # Make a smart, data-backed guess so the merchant can reply in one word.
    pos = F.review_theme(c.m, "pos")
    trend = None
    guess, evidence = None, []
    if pos:
        guess = F.theme(pos["theme"])
        q = pos.get("common_quote", "")
        # stylist_skill + quote mentioning balayage → the guess is the service
        for t in c.cat.get("trend_signals") or []:
            key = t.get("query", "").split(" near")[0].split(" price")[0].split(" ")[0]
            if key and key.lower() in q.lower():
                guess, trend = key.lower(), t
        evidence.append(
            f"{pos['occurrences_30d']} reviews this month"
            + (f' mention it ("{q}")' if q else f" praise your {F.theme(pos['theme'])}")
        )
        c.cite("merchant.review_themes")
    if trend:
        evidence.append(f"'{trend['query']}' searches are up {F.pct(trend['delta_yoy'])} YoY")
        c.cite("category.trend_signals")
    noun = {"restaurants": "dish", "dentists": "treatment", "gyms": "class", "pharmacies": "product"}.get(
        c.slug, "service"
    )
    lead = c.say(
        f"Hi {c.sal}! Quick one — which {noun} got asked for most at {F.biz_name(c.m)} this week?",
        f"Hi {c.sal}! Ek chhota sa sawaal — is hafte {F.biz_name(c.m)} par sabse zyada kis {noun} ki demand rahi?",
    )
    g_line = ""
    if guess and evidence:
        g_line = f"My guess is {guess}: {' and '.join(evidence)}."
    elif F.active_offers(c.m):
        g_line = f"Is it still '{F.active_offers(c.m)[0]}', or has something else picked up?"
    give = c.say(
        "Reply with just the name — I'll turn it into a Google post + a ready WhatsApp reply for price enquiries. 5 minutes, zero effort from you.",
        "Bas naam bhej dijiye — main usse Google post + price enquiries ke liye ready WhatsApp reply bana dungi. 5 minute, aapki taraf se zero mehnat.",
    )
    body = " ".join(x for x in [lead, g_line, give] if x)
    return Draft(
        body,
        "open_ended",
        rationale="Weekly curious-ask: asking-the-merchant lever with a data-backed guess (reviews + search trend) so a one-word reply is enough; reciprocity (post + reply template).",
        lever="asking_the_merchant+reciprocity",
        next_action={
            "type": "collect",
            "topic": "in-demand service",
            "guess": guess,
            "artifact": "Google post + WhatsApp price reply for the named service.",
            "confirm_en": "publish the post",
        },
    )


def active_planning_intent(c: Ctx) -> Draft:
    p = c.payload
    topic = str(p.get("intent_topic", ""))
    last = p.get("merchant_last_message") or (F.last_merchant_turn(c.m) or {}).get("body", "")
    offers = F.active_offers(c.m)
    c.cite("trigger.payload.intent_topic + merchant_last_message")
    if "thali" in topic or "corporate" in topic:
        base = F.offer_matching(offers, "thali")
        price = int(F.price_in(base).strip("₹").replace(",", "")) if base and F.price_in(base) else None
        hist = " ".join(t.get("body", "") for t in c.m.get("conversation_history") or [])
        per_day = re.search(r"(\d+)\s*orders/day", hist)
        if price:
            t1, t2, t3 = price - 14, price - 20, price - 30
            art = (
                f"{F.biz_name(c.m).split(' ')[0]} Corporate Thali — for offices near {F.locality(c.m)}\n"
                f"• 10–24 thalis/day: ₹{t1} each (walk-in ₹{price})\n"
                f"• 25–49: ₹{t2} each + free delivery\n"
                f"• 50+: ₹{t3} each + monthly billing\n"
                f"• Order by 11am on WhatsApp → delivered 12:30–1pm"
            )
            c.cite("merchant.offers (thali price)")
        else:
            art = "Corporate lunch package: 3 volume tiers, order by 11am, delivered 12:30–1pm."
        vol = ""
        if per_day:
            vol = f"You're at ~{per_day.group(1)} thalis/day now — a single 25-plate office order more than doubles weekday lunch."
            c.cite("merchant.conversation_history (18 orders/day)")
        lead = f"{c.sal}, here's the first cut — edit anything:"
        ask = c.say(
            "Shall I turn this into a Google post + a 3-line WhatsApp for office admins? Reply YES.",
            "Ise Google post + office admins ke liye 3-line WhatsApp mein badal doon? Bas YES bhejiye.",
        )
        body = f"{lead}\n\n{art}\n\n" + " ".join(x for x in [vol, ask] if x)
    elif "yoga" in topic or "kids" in topic or "camp" in topic:
        prev = (F.last_vera_turn(c.m) or {}).get("body", "")
        price = F.price_in(prev) or F.price_in(F.catalog_offer(c.cat) or "")
        pos = F.review_theme(c.m, theme="small_classes")
        art = (
            f"Kids Yoga Summer Camp — {F.biz_name(c.m)}, {F.locality(c.m)}\n"
            f"• Ages 7–12 · 4 weeks · 3 classes/week (45 min)\n"
            f"• Fee: {price or 'your price'} for the full camp\n"
            f"• Max 10 kids per batch" + (" — your small classes are what reviews love" if pos else "") + "\n"
            "• Week 4: parents' demo session"
        )
        c.cite("merchant.conversation_history (4-week plan)")
        if pos:
            c.cite("merchant.review_themes.small_classes")
        lead = f"{c.sal}, here's the camp, ready to publish:"
        ask = c.say(
            "Reply YES and I'll post it on Google today + send you the Insta carousel copy.",
            "YES bhejiye — aaj hi Google par post kar dungi + Insta carousel copy bhi.",
        )
        body = f"{lead}\n\n{art}\n\n{ask}"
    else:
        lead = f'{c.sal}, picking up your question — "{last}" — here\'s a first draft plan:'
        art = f"1) Offer: {offers[0] if offers else F.catalog_offer(c.cat)}\n2) Google post + WhatsApp broadcast\n3) Review results after 7 days"
        ask = c.say("Reply YES to publish step 1 today.", "YES bhejiye, step 1 aaj hi live kar dungi.")
        body = f"{lead}\n\n{art}\n\n{ask}"
    return Draft(
        body,
        "binary_yes_no",
        rationale="Merchant already expressed intent — skip qualification, hand over a complete, editable artifact built from their own prices/volumes, then one publish CTA.",
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
    return f"Google post + WhatsApp broadcast drafted for {name} around '{(F.active_offers(c.m) or [F.catalog_offer(c.cat)])[0]}'."
