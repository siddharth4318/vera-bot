"""Account lifecycle: renewal, win-back after expiry, dormancy and Google
verification."""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft, because, best_hook, cap, hook_text, yes_close
from .fallback import generic


def _gap_names(c: Ctx) -> list[str]:
    gaps = []
    if not F.active_offers(c.m):
        gaps.append("no live offer")
    if F.has_signal(c.m, "unverified_gbp"):
        gaps.append("an unverified Google listing")
    stale = F.signal_value(c.m, "stale_posts")
    if stale:
        gaps.append(f"a last post {stale.rstrip('d')} days old")
    return gaps


def renewal_due(c: Ctx) -> Draft:
    p = c.payload
    signal_days = F.signal_value(c.m, "renewal_due_soon")
    visible_days = p.get("days_remaining")
    if visible_days is None and signal_days:
        visible_days = int(re.sub(r"\D", "", signal_days) or 0) or None
    days = visible_days if visible_days is not None else F.g(c.m, "subscription", "days_remaining")
    plan = p.get("plan", F.g(c.m, "subscription", "plan")) or "magicpin"
    amt = p.get("renewal_amount")
    c.cite("trigger.payload renewal")
    last = F.last_vera_turn(c.m)
    ignored = last and last.get("engagement") == "merchant_no_reply"

    if days is not None and days > 45:
        # a renewal nudge months early would read as a sales push; lead with value instead
        lead = f"{c.sal}, no renewal pressure — your {plan} plan has a good while to run. I'd rather use that time to make it pay off."
    elif days is not None:
        when = (
            "ends today"
            if days == 0
            else "ends tomorrow" if days == 1 else f"ended {-days} days ago" if days < 0 else f"ends in {days} days"
        )
        lead = f"{c.sal}, your {plan} plan {when}" + (f" ({F.money(amt)} to renew)" if amt else "") + "."
    else:
        lead = f"{c.sal}, your {plan} plan renewal is coming up" + (f" ({F.money(amt)})" if amt else "") + "."
    if ignored and (days is None or days <= 45):
        rest = lead.split(", ", 1)[1]
        lead = f"{c.sal}, not repeating the renewal reminder — one honest point instead. " + rest[:1].upper() + rest[1:]
    views, calls, ctr = F.perf_numbers(c.m)
    val = ""
    if views is not None and calls is not None:
        weak = F.has_signal(c.m, "ctr_below_peer_median") or (F.perf(c.m, "ctr") or 0) < (
            F.g(c.cat, "peer_stats", "avg_ctr") or 0
        )
        joint = "but only" if weak else "and"
        val = f"In the last 30 days your listing got {F.num(views)} views {joint} {F.num(calls)} calls" + (
            f" (CTR {ctr})" if ctr else ""
        )
        drop = F.delta(c.m, "calls_pct")
        val += f", and calls are down {F.pct(abs(drop))} this week." if drop is not None and drop <= -0.2 else "."
        c.cite("merchant.performance" + (" + delta_7d" if drop is not None and drop <= -0.2 else ""))
    gaps = _gap_names(c)
    early = days is not None and days > 45
    fix = ""
    if gaps:
        fix = (
            f"The one thing holding it back: your profile shows {' and '.join(gaps)}."
            if early
            else f"Renewal is only worth it if we fix why: your profile shows {' and '.join(gaps)}."
        )
        c.cite("merchant.signals / offers")
    if early:
        ask = yes_close(c, "send a 2-step fix plan for this week", "Is hafte ka 2-step fix plan bhej doon", eta="2 min")
    else:
        ask = yes_close(
            c,
            "send a 2-step fix plan along with the renewal details",
            "2-step fix plan aur renewal details saath mein bhej doon",
            eta="2 min",
        )
    body = " ".join(x for x in [lead, val, fix, ask] if x)
    h = best_hook(c, exclude=("renewal",), prefer=("no_offer", "unverified", "stale_posts"))
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            "Renewal is the why-now, handled without a hard sell",
            "an earlier reminder went unanswered, so this one leads with value" if ignored else "",
            "no pressure framing because renewal is months away" if early else "",
            "their own views/calls/CTR" if val else "",
            f"the flagged gaps ({', '.join(gaps)})" if gaps else "",
        ),
        lever="loss_aversion+reciprocity",
        next_action={
            "type": "deliver",
            "topic": "renewal",
            "artifact": f"Plan: (1) {(h.fix if h and h.fix else 'fix the top listing gap')} this week, (2) a weekly Google post. Renewal: {plan}"
            + (f" at {F.money(amt)}" if amt else "")
            + " — I'll share the payment step with your confirmation.",
            "confirm_en": "confirm renewal",
        },
    )


def winback_eligible(c: Ctx) -> Draft:
    p = c.payload
    days = p.get("days_since_expiry", F.g(c.m, "subscription", "days_since_expiry"))
    dip = p.get("perf_dip_pct")
    added = p.get("lapsed_customers_added_since_expiry")
    c.cite("trigger.payload winback")
    lead = (
        f"{c.sal}, it's been {days} days since {F.possessive(F.biz_name(c.m))} plan paused"
        if days
        else f"{c.sal}, since {F.possessive(F.biz_name(c.m))} plan paused"
    )
    bits = []
    if dip is not None and dip < 0:
        bits.append(f"performance is down {F.pct(dip)}")
    if added:
        bits.append(f"{added} clients have lapsed since")
    lead += (" — " + " and ".join(bits) + ".") if bits else "."
    views, calls, ctr = F.perf_numbers(c.m)
    now = ""
    if views is not None and calls is not None:
        now = f"Right now {F.num(views)} people a month see your listing, and only {F.num(calls)} call" + (
            f" (CTR {ctr})." if ctr else "."
        )
        c.cite("merchant.performance")
    back = {
        "salons": "back in your chairs",
        "restaurants": "back at your tables",
        "gyms": "back on the floor",
        "dentists": "back in the chair",
        "pharmacies": "back at your counter",
    }.get(c.slug, "back")
    nopitch = f"Not asking you to renew today — let's just get those {F.people(c.slug)} {back} first."
    who = f"those {added} clients" if added else "your lapsed clients"
    ask = yes_close(
        c,
        f"send you {who} + a ready 'we miss you' message",
        f"{who.replace('those', 'Woh').replace('your lapsed clients', 'Lapsed clients ki list')} + ek ready 'we miss you' message bhej doon",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, now, nopitch, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Win-back: quantify what the pause is costing with the payload's own numbers and the live views/calls, then offer free value (the lapsed list) before any renewal ask.",
        lever="loss_aversion+reciprocity",
        next_action={
            "type": "deliver",
            "topic": "winback list",
            "artifact": f"Win-back message: \"Hi! It's been a while — we've missed you at {F.biz_name(c.m)}. Reply here and we'll hold a slot for you this week.\"",
            "confirm_en": "send it to the lapsed clients",
        },
    )


def dormant_with_vera(c: Ctx) -> Draft:
    p = c.payload
    days = p.get("days_since_last_merchant_message")
    last_topic = p.get("last_topic")
    # free value first: the most actionable trend item for this category
    item = next(
        (
            it
            for it in c.cat.get("digest") or []
            if it.get("kind") == "trend" and any(ch.isdigit() for ch in it.get("title", ""))
        ),
        None,
    ) or F.digest_item(c.cat, None, kinds=("trend", "tech", "seasonal"))
    gap = f" since we last spoke about your {F.humanize(last_topic)}" if last_topic else ""
    if days:
        opener = f"{c.sal}, it's been {days} days{gap} — no sales pitch this time, just one useful thing."
    elif c.m.get("conversation_history"):
        opener = f"{c.sal}, it's been a while since we spoke — no sales pitch, just one useful thing."
    else:
        opener = f"{c.sal}, one useful thing for {F.biz_name(c.m)} this week — no ask attached."
    if days:
        c.cite("trigger.payload")
    views, calls, _ = F.perf_numbers(c.m)
    why = (
        f"Your listing had {F.num(views)} views but {F.num(calls)} calls last month, so small changes that lift calls matter."
        if views and calls is not None
        else ""
    )
    if why:
        c.cite("merchant.performance")
    if item:
        c.cite(f"category.digest[{item.get('id')}]")
        source = str(item.get("source", "")).split(",")[0].strip()
        fact = f"{item.get('title').rstrip('.')} ({source})."
        act = str(item.get("actionable", "")).rstrip(".")
        est = F.g(c.m, "identity", "established_year")
        cond = re.search(r"\s*if you've crossed (\d+) months?", act)
        if cond:
            act = act[: cond.start()]
            if est:
                act += f" — you qualify, you've been open since {est}"
                c.cite("merchant.identity.established_year")
        mid = f"For {F.biz_name(c.m)} it's a one-line change: {act[:1].lower() + act[1:]}." if act else ""
        ask = c.say(
            "Want me to show you exactly where to make it? Reply YES.",
            "Kahan aur kaise karna hai, main dikha doon? Bas YES bhejiye.",
        )
    else:
        h = best_hook(c)
        if not h:
            return generic(c)
        fact = cap(hook_text(c, h)) + "."
        mid = ""
        ask = yes_close(c, "send the 1-step fix", "1-step fix bhej doon")
    body = " ".join(x for x in [opener, fact, why, mid, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            "Dormant merchant: lead with free, useful value (reciprocity) instead of an ask",
            f"{days} days since the last conversation" if days else "",
            f"last topic was {F.humanize(last_topic)}" if last_topic else "",
            f"a sourced category tip ({(item or {}).get('source')})" if item else "",
            "their own views/calls" if why else "",
        ),
        lever="reciprocity+curiosity",
        next_action={
            "type": "deliver",
            "topic": (item or {}).get("title", "listing fix"),
            "artifact": f"Step: {(item or {}).get('actionable', 'update your Google description')}. I've drafted the exact text for your listing.",
            "confirm_en": "apply it",
        },
    )


def gbp_unverified(c: Ctx) -> Draft:
    p = c.payload
    up = p.get("estimated_uplift_pct")
    path = p.get("verification_path")
    views = F.perf(c.m, "views")
    c.cite("trigger.payload verification")
    lead = f"{c.sal}, {F.biz_name(c.m)} is still unverified on Google."
    mid = ""
    if up and views:
        extra = int(views * up)
        mid = f"Verified listings see roughly {F.pct(up)} more visibility — on your {F.num(views)} monthly views that's ~{F.num(extra)} more people finding you."
        c.cite("merchant.performance.views")
    how = ""
    if path:
        how = f"It takes one {F.humanize(path).replace('postcard or phone call', 'phone call or postcard')} — I'll handle the paperwork."
    ask = yes_close(c, "start the verification now", "Verification abhi shuru kar doon", eta="2 min")
    body = " ".join(x for x in [lead, mid, how, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Unverified listing: quantified upside on the merchant's own view count; effort fully externalized.",
        lever="loss_aversion+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "verification",
            "artifact": "Verification request started. Google will call or send a postcard with a 5-digit code — just forward it to me here.",
            "confirm_en": "confirm the phone number on the listing",
        },
    )
