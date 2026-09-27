"""External events: festivals, IPL matches, weather and local news."""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft, because, yes_close
from .fallback import generic


def festival_upcoming(c: Ctx) -> Draft:
    p = c.payload
    fest, fdate, days = p.get("festival"), p.get("date"), p.get("days_until")
    if days is None and fdate and c.now:  # only compute when the payload doesn't say
        live = F.days_between(c.now, fdate)
        days = live if live is not None and live >= 0 else None
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
    fit_words = (
        "bridal",
        "spa",
        "facial",
        "package",
        "combo",
        "family",
        "brunch",
        "annual",
        "couple",
        "cleaning",
        "whitening",
    )
    seasonal = F.offer_matching(offers, *fit_words)
    if seasonal:  # lead with the live offer that suits a festive visit, not just the first one
        offers = [seasonal] + [o for o in offers if o != seasonal]
    lead = f"{c.sal}, {fest} is on {F.day_month(fdate)}" + (f" — {days} days out." if days is not None else ".")
    if days is not None and days > 45:
        judg = "Too early to promote — but the right time to lock the plan, because"
        if beat:
            judg += f" {F.beat_phrase(beat)}."
            c.cite("category.seasonal_beats")
        else:
            judg += f" the {c.cat.get('display_name', 'businesses').lower()} that pre-book early fill first."
        views, calls, _ = F.perf_numbers(c.m)
        anchor = ""
        if views and calls:
            anchor = (
                f"Your listing is already pulling {F.num(views)} views and {F.num(calls)} calls a month"
                + (" (above the peer median for calls)" if F.has_signal(c.m, "above_peer_median_calls") else "")
                + f" — open a {fest} pre-booking list to those {F.people(c.slug)} first and the peak slots fill before the rush."
            )
            c.cite("merchant.performance + signals")
        hook_en = hook_hi = ""
        if beat and "bridal" in beat.get("note", "") and not F.offer_matching(offers, "bridal"):
            bridal = F.catalog_offer(c.cat, keywords=("bridal",))
            if bridal and "bridal" in bridal.lower():  # the season is bridal-led: lead with a bridal offer
                hook_en = f" built around a '{bridal}' offer (magicpin's standard)"
                hook_hi = f" — '{bridal}' offer ke saath (magicpin ka standard)"
                c.cite("category.offer_catalog (bridal)")
        if not hook_en and offers:
            hook_en, hook_hi = f" with '{offers[0]}' as the hook", f" — '{offers[0]}' ke saath"
        ask = yes_close(
            c, f"draft a '{fest} pre-booking' message{hook_en}", f"'{fest} pre-booking' message draft kar doon{hook_hi}"
        )
    else:
        judg = "Now's the window — festive searches peak in the final 2-3 weeks."
        views, _, _ = F.perf_numbers(c.m)
        anchor = f"Your {F.num(views)} monthly profile views are the audience for it." if views else ""
        pack = offers[0] if offers else F.suggested_offer(c.cat, c.m)
        ask = yes_close(
            c,
            f"put up a {fest} Google post + WhatsApp broadcast around '{pack}'",
            f"'{pack}' ke saath {fest} ka Google post + WhatsApp broadcast laga doon",
        )
    body = " ".join(x for x in [lead, judg, anchor, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            (
                f"Festival {days} days away (payload), with a timing judgment (plan vs promote) instead of a generic festive discount"
                if days is not None
                else "Festival from the payload, with a timing judgment instead of a generic discount"
            ),
            "the category's seasonal beat" if any("seasonal_beats" in u for u in c.used) else "",
            "the merchant's own views/calls" if any("performance" in u for u in c.used) else "",
            "their live offer as the hook" if offers else "",
        ),
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
    views, calls, _ = F.perf_numbers(c.m)
    if any(k in note for k in ("retention", "repeat", "return")):
        anchor = f"That favours your existing {ppl}, so the play is a come-back plan, not ads"
        active = F.g(c.m, "customer_aggregate", "total_active_members")
        ytd = F.g(c.m, "customer_aggregate", "total_unique_ytd")
        if active:
            anchor += f" — you have {F.num(active)} active {ppl} to build it around"
            c.cite("merchant.customer_aggregate.total_active_members")
        elif ytd:
            anchor += (
                f" — {F.num(ytd)} people have come through your doors this year, and they're the easiest to bring back"
            )
            c.cite("merchant.customer_aggregate.total_unique_ytd")
        if views and calls is not None and (active or ytd):
            anchor += f". New faces will find it too: {F.num(views)} profile views and {F.num(calls)} calls last month"
            c.cite("merchant.performance")
        elif views:
            anchor += f" — {F.num(views)} people viewed your profile last month, so the audience is already there"
            c.cite("merchant.performance.views")
        anchor += "."
        pack = offers[0] if offers else None
        what = (
            f"draft an 8-week 'festive shape-up' plan + a WhatsApp invite for past {ppl}"
            + (f" using '{pack}'" if pack else "")
            if c.slug == "gyms"
            else f"draft a festive come-back message for past {ppl}" + (f" using '{pack}'" if pack else "")
        )
        ask = yes_close(c, what, f"Purane {ppl} ke liye festive come-back message draft kar doon")
    else:
        if offers:
            pack = offers[0]
            anchor = f"Your '{pack}' is the natural hook — lead with it early."
            c.cite("merchant.offers")
        else:
            pack = F.suggested_offer(c.cat, c.m)
            anchor = "There's no live offer on your listing to catch it yet"
            if views:
                anchor += f", and {F.num(views)} people viewed your profile in the last 30 days"
                c.cite("merchant.performance.views")
            anchor += "."
            c.cite("merchant.offers (none active)")
        label_en = f"'{pack}'" if offers else f"magicpin's standard '{pack}' offer"
        label_hi = f"'{pack}'" if offers else f"magicpin ka standard '{pack}' offer"
        ask = yes_close(
            c, f"set up {label_en} + a festive Google post", f"{label_hi} aur ek festive Google post set kar doon"
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
    offers = F.active_offers(c.m)
    views, _, _ = F.perf_numbers(c.m)
    weekday_offer = F.offer_matching(offers, "tue-thu", "tue", "weekday", "mon-thu")

    match_dt = F.parse_dt(p.get("match_time_iso")) or c.now
    day = match_dt.strftime("%A") if match_dt else ""
    if weeknight is False:
        kind = f"a {day} match" if day in ("Saturday", "Sunday") else "a weekend match"
        lead = f"{c.sal}, {match} at {venue} tonight{', ' + tm if tm else ''} — and it's {kind}."
        judg = "Counter-intuitive call: skip the dine-in match promo tonight."
        drop = re.search(r"covers down (\d+)%", (item or {}).get("summary", ""))
        if item and drop:
            src = item.get("source") or "magicpin order data"
            if day == "Saturday":
                judg += f" Saturday IPL nights ran {drop.group(1)}% below a normal Saturday for restaurant covers ({src}) — people host home-watch parties instead."
            else:
                judg += f" On IPL Saturdays, restaurant covers ran {drop.group(1)}% below a normal Saturday ({src}) — a {day or 'weekend'} match keeps people home the same way."
            c.cite(f"category.digest[{item.get('id')}]")
        elif item and "Saturday" in item.get("summary", "") + item.get("title", ""):
            judg += " Weekend matches pull people into home-watch parties instead of restaurants."
            c.cite(f"category.digest[{item.get('id')}]")
        save = (
            f"Your '{weekday_offer}' doesn't run tonight anyway — keep it for the next weeknight match."
            if weekday_offer
            else ""
        )
        play = "Play delivery instead — tonight people order in, they don't walk in."
        when = "an hour before the start" if tm else "before the toss"
        combo = F.catalog_offer(c.cat, keywords=("match",))
        combo = combo if combo and "match" in combo.lower() else None
        label = f"a delivery-only version of the '{combo}'" if combo else "a delivery-only match-night combo"
        if combo:
            c.cite("category.offer_catalog (match-night combo)")
        ask = yes_close(
            c,
            f"set up {label} + an Insta story to go live {when}",
            f"Match se ek ghanta pehle {label} + Insta story live kar doon",
        )
        body = " ".join(x for x in [lead, judg, save, play, ask] if x)
        rat = "Weekend match (payload is_weeknight=false): covers fall on weekend match nights, so the bot recommends against the obvious dine-in promo, notes the live weekday offer can't run tonight, and redirects to delivery."
    else:
        lead = f"{c.sal}, {match} at {venue} tonight{', ' + tm if tm else ''}."
        promo = weekday_offer or (offers[0] if offers else None)
        judg = "Weeknight matches pull people out — tonight is worth a push."
        lift = re.search(r"Weeknight matches drive \+(\d+)% covers", (item or {}).get("summary", ""))
        if item and lift:
            judg = f"Weeknight matches drive +{lift.group(1)}% covers ({item.get('source') or 'magicpin order data'}) — tonight is worth a push."
            c.cite(f"category.digest[{item.get('id')}]")
        what = (
            f"push '{promo}' as a match-night post + WhatsApp blast an hour before the start"
            if promo
            else "put up a match-night post + WhatsApp blast an hour before the start"
        )
        ask = yes_close(c, what, f"Match se ek ghanta pehle '{promo or 'match-night'}' post + WhatsApp blast kar doon")
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
            "artifact": f'Insta story: "{match} tonight 🏏 Watching at home? {F.biz_name(c.m)} delivers — order before the toss!"',
            "confirm_en": "schedule it for an hour before the match",
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
