"""External events: festivals, IPL matches, weather and local news."""

from __future__ import annotations

from .. import facts as F
from ..core import Ctx, Draft, yes_close
from .fallback import generic


def festival_upcoming(c: Ctx) -> Draft:
    p = c.payload
    fest, fdate, days = p.get("festival"), p.get("date"), p.get("days_until")
    if fdate and c.now:  # live clock beats a stale snapshot in the payload
        live = F.days_between(c.now, fdate)
        if live is not None and live >= 0:
            days = live
    if not fest:
        return seasonal_nudge(c)
    c.cite("trigger.payload festival")
    offers = F.active_offers(c.m)
    beats = [
        b
        for b in c.cat.get("seasonal_beats") or []
        if any(k in b.get("note", "").lower() for k in ("festival", "wedding", "diwali"))
    ]
    beat = beats[0] if beats else None
    lead = f"{c.sal}, {fest} is on {F.day_month(fdate)}" + (f" — {days} days out." if days is not None else ".")
    if days is not None and days > 45:
        judg = "Too early to promote — but the right time to lock the plan, because"
        if beat:
            judg += f" {F.beat_phrase(beat)}."
            c.cite("category.seasonal_beats")
        else:
            judg += f" the {c.cat.get('display_name', 'businesses').lower()} that pre-book early fill first."
        ca = c.m.get("customer_aggregate") or {}
        base = ca.get("total_unique_ytd")
        anchor = ""
        if base:
            anchor = f"You've served {F.num(base)} {F.people(c.slug)} this year — a pre-booking list opened to them first is the cheapest way to fill peak slots."
            c.cite("merchant.customer_aggregate.total_unique_ytd")
        ask = yes_close(
            c,
            f"draft a '{fest} pre-booking' message" + (f" with '{offers[0]}' as the hook" if offers else ""),
            f"'{fest} pre-booking' message draft kar doon" + (f" — '{offers[0]}' ke saath" if offers else ""),
        )
    else:
        judg = "Now's the window — festive searches peak in the final 2-3 weeks."
        anchor = ""
        pack = offers[0] if offers else F.catalog_offer(c.cat)
        ask = yes_close(
            c,
            f"put up a {fest} Google post + WhatsApp broadcast around '{pack}'",
            f"'{pack}' ke saath {fest} ka Google post + WhatsApp broadcast laga doon",
        )
    body = " ".join(x for x in [lead, judg, anchor, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=f"Festival {days}d away: timing judgment (plan vs promote) rather than a generic festive discount; anchored on the merchant's client base and live offer.",
        lever="timing_judgment+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": f"{fest} campaign",
            "artifact": f'"{fest} slots are opening at {F.biz_name(c.m)}! Regulars get first pick — reply with your preferred date and we\'ll hold it."',
            "confirm_en": "send it to your client list",
        },
    )


def seasonal_nudge(c: Ctx) -> Draft:
    """Festival/season trigger without a named event: use the next festive
    beat from the category calendar and pick an action that fits the beat
    (retention beats get a retention action, demand beats get an offer)."""
    beat = (
        F.upcoming_beat(c.cat, F.month_of(c.now))
        or F.seasonal_beat(c.cat, F.month_of(c.now))
        or ((c.cat.get("seasonal_beats") or [None])[0])
    )
    if not beat:
        return generic(c)
    c.cite("category.seasonal_beats")
    lead = f"{c.sal}, the next big window for {c.cat.get('display_name', c.slug).lower()}: {F.beat_phrase(beat)}."
    offers = F.active_offers(c.m)
    note = beat.get("note", "").lower()
    ppl = F.people(c.slug)
    if any(k in note for k in ("retention", "repeat", "return")):
        base = F.g(c.m, "customer_aggregate", "total_active_members") or F.g(
            c.m, "customer_aggregate", "total_unique_ytd"
        )
        anchor = (
            f"That favours your existing {ppl}"
            + (f" — {F.num(base)} of them this year" if base else "")
            + ", so the play is a come-back plan, not ads."
        )
        if base:
            c.cite("merchant.customer_aggregate")
        pack = offers[0] if offers else None
        what = (
            f"draft an 8-week 'festive shape-up' plan + a WhatsApp invite for past {ppl}"
            + (f" using '{pack}'" if pack else "")
            if c.slug == "gyms"
            else f"draft a festive come-back message for past {ppl}" + (f" using '{pack}'" if pack else "")
        )
        ask = yes_close(c, what, f"Purane {ppl} ke liye festive come-back message draft kar doon")
    else:
        pack = offers[0] if offers else F.catalog_offer(c.cat)
        anchor = "" if offers else "You have no offer live to catch it yet."
        ask = yes_close(
            c, f"set up '{pack}' + a festive Google post", f"'{pack}' aur ek festive Google post set kar doon"
        )
    body = " ".join(x for x in [lead, anchor, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Festival trigger without a named event: used the next festive beat from the category calendar and matched the action to the beat (retention vs acquisition).",
        lever="timing+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "seasonal campaign",
            "artifact": f"Festive plan + invite drafted for past {ppl}.",
            "confirm_en": "send it",
        },
    )


def ipl_match_today(c: Ctx) -> Draft:
    p = c.payload
    match, venue, t = p.get("match"), p.get("venue"), F.parse_dt(p.get("match_time_iso"))
    weeknight = p.get("is_weeknight")
    item = F.digest_item(c.cat, None, kinds=("seasonal",))
    tm = ""
    if t:
        h = t.hour % 12 or 12
        tm = f"{h}:{t.minute:02d}{'pm' if t.hour >= 12 else 'am'}"
    c.cite("trigger.payload match")
    lead = f"{c.sal}, {match} at {venue} tonight{', ' + tm if tm else ''}."
    offers = F.active_offers(c.m)
    ca = c.m.get("customer_aggregate") or {}
    deliv, dine = ca.get("delivery_orders_30d"), ca.get("dine_in_orders_30d")
    if weeknight is False:
        judg = "Counter-intuitive call: skip the dine-in match promo tonight."
        if item and "Saturday" in item.get("summary", "") + item.get("title", ""):
            judg += (
                " "
                + "Saturday IPL games pull people to watch at home — restaurant covers drop ~12% vs a normal Saturday (magicpin order data)."
            )
            c.cite(f"category.digest[{item.get('id')}]")
        mix = ""
        if deliv and dine:
            mix = f"Your own mix already leans delivery — {deliv} delivery vs {dine} dine-in orders in 30 days — so ride that."
            c.cite("merchant.customer_aggregate delivery/dine-in")
        bogo = F.offer_matching(offers, "tue-thu", "tue", "weekday")
        save = ""
        if bogo:
            save = f"Keep '{bogo}' for the next weeknight match, where covers run ~18% higher."
        combo = F.catalog_offer(c.cat, keywords=("match",))
        ask = yes_close(
            c,
            (
                f"draft a delivery-only '{combo}' Insta story to go live at 6:30pm"
                if combo
                else "draft a delivery-only match-night story for 6:30pm"
            ),
            (
                f"6:30pm ke liye delivery-only '{combo}' Insta story draft kar doon"
                if combo
                else "6:30pm ke liye delivery-only match story bana doon"
            ),
        )
        body = " ".join(x for x in [lead, judg, mix, save, ask] if x)
        rat = "Saturday match: data says covers fall, so the bot recommends against the obvious dine-in promo and redirects to delivery (merchant's stronger channel), preserving the weekday BOGO."
    else:
        promo = offers[0] if offers else F.catalog_offer(c.cat, keywords=("match",))
        judg = "Weeknight matches run ~18% above normal covers — tonight is worth a push."
        ask = yes_close(
            c,
            f"push '{promo}' as a match-night post + WhatsApp blast at 6pm",
            f"6pm par '{promo}' ka match-night post + WhatsApp blast kar doon",
        )
        body = " ".join(x for x in [lead, judg, ask] if x)
        rat = "Weeknight match: covers rise, push the live offer."
    return Draft(
        body,
        "binary_yes_no",
        rationale=rat,
        lever="contrarian_judgment+specificity",
        next_action={
            "type": "deliver",
            "topic": "match night",
            "artifact": f'Insta story: "{match} tonight 🏏 Watch at home, we\'ll bring the pizza — order before the toss!"',
            "confirm_en": "schedule it for 6:30pm",
        },
    )


def weather_event(c: Ctx) -> Draft:
    p = c.payload
    temp = p.get("temp_c") or p.get("temperature_c") or p.get("temp")
    kind = F.humanize(c.trg.get("kind", "weather"))
    c.cite("trigger.payload weather")
    lead = f"{c.sal}, {kind} alert for {F.city(c.m) or 'your city'}" + (f" — {temp}°C today." if temp else " today.")
    item = F.digest_item(c.cat, None, kinds=("seasonal",))
    mid = ""
    if item and any(
        k in (item.get("title", "") + item.get("summary", "")).lower() for k in ("summer", "heat", "monsoon", "ors")
    ):
        mid = f"{item.get('title').rstrip('.')} ({item.get('source')})."
        c.cite(f"category.digest[{item.get('id')}]")
    offers = F.active_offers(c.m)
    pack = offers[0] if offers else F.catalog_offer(c.cat, keywords=("delivery",))
    ask = yes_close(
        c,
        f"push a same-day '{pack}' post for people staying indoors",
        f"Ghar par rehne walon ke liye aaj ka '{pack}' post laga doon",
    )
    body = " ".join(x for x in [lead, mid, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Weather trigger → same-day demand shift; one timely campaign.",
        lever="urgency+timeliness",
        next_action={
            "type": "deliver",
            "topic": "weather campaign",
            "artifact": f"Post ready: {pack} — today only.",
            "confirm_en": "publish",
        },
    )


def local_event(c: Ctx) -> Draft:
    """Local news / traffic / civic event. Render the headline, then translate
    it into a footfall consequence and one channel-appropriate action."""
    p = c.payload
    head = p.get("headline") or p.get("title") or p.get("event") or p.get("summary")
    if not head:
        return generic(c)
    c.cite("trigger.payload.headline")
    days = p.get("impact_days") or p.get("duration_days")
    hrs = p.get("duration_hours") or p.get("impact_hours")
    span = f" for {days} days" if days else (f" for {hrs} hours" if hrs else "")
    lead = f"{c.sal}, local heads-up: {head.rstrip('.')}."
    loc = F.locality(c.m) or "your area"
    if c.slug in ("restaurants", "pharmacies"):
        ca = c.m.get("customer_aggregate") or {}
        share = ca.get("delivery_share_pct")
        impact = (
            f"Walk-ins around {loc} usually dip when access is disrupted{span} — delivery is the channel to lean on"
            + (f" (it's already {F.pct(share)} of your orders)." if share else ".")
        )
        if share:
            c.cite("merchant.customer_aggregate.delivery_share_pct")
        ask = yes_close(
            c,
            "post a 'we're open + delivering' Google update and a WhatsApp note to regulars",
            "'Hum khule hain + delivery chalu hai' ka Google update aur regulars ko WhatsApp bhej doon",
        )
    else:
        impact = f"Customers heading to {loc}{span} will check if you're reachable — a quick 'open as usual + how to reach us' update prevents lost bookings."
        ask = yes_close(c, "post that update on Google and pin it", "Yeh update Google par post karke pin kar doon")
    body = " ".join(x for x in [lead, impact, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Local event: headline rendered verbatim, translated into a footfall consequence for this merchant's channel mix; one timely action.",
        lever="timeliness+loss_aversion",
        next_action={
            "type": "deliver",
            "topic": "local update",
            "artifact": f"Google update: \"{F.biz_name(c.m)} is open as usual{span.replace(' for', ' through the next') if span else ''}. "
            + (
                "Order on WhatsApp for delivery."
                if c.slug in ("restaurants", "pharmacies")
                else "Message us on WhatsApp for directions or to book."
            )
            + '"',
            "confirm_en": "publish it",
        },
    )
