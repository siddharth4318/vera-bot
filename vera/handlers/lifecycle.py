"""Account lifecycle: renewal, win-back after expiry, dormancy and Google
verification."""

from __future__ import annotations

from .. import facts as F
from ..core import Ctx, Draft, best_hook, cap, hook_text, yes_close
from .fallback import generic


def renewal_due(c: Ctx) -> Draft:
    p = c.payload
    days = p.get("days_remaining", F.g(c.m, "subscription", "days_remaining"))
    plan = p.get("plan", F.g(c.m, "subscription", "plan"))
    amt = p.get("renewal_amount")
    c.cite("trigger.payload renewal")
    views, calls = F.perf(c.m, "views"), F.perf(c.m, "calls")
    last = F.last_vera_turn(c.m)
    ignored = last and last.get("engagement") == "merchant_no_reply"
    lead = f"{c.sal}, your {plan} plan ends in {days} days" + (f" ({F.money(amt)})" if amt else "") + "."
    if ignored:
        lead = (
            f"{c.sal}, not repeating the renewal reminder — one honest point instead."
            + f" Your {plan} plan ends in {days} days."
        )
    h = best_hook(c, exclude=("renewal",), prefer=("dip_calls", "no_offer", "unverified"))
    val = ""
    if views is not None and calls is not None:
        val = f"Last 30 days your listing got {F.num(views)} views and {F.num(calls)} calls."
        c.cite("merchant.performance")
    fix = ""
    if h:
        fix = f"Before you decide, I'd rather fix why {h.text} — that's what renewal should buy you."
        c.cite(h.cite)
    ask = yes_close(
        c,
        "send a 2-step fix plan along with the renewal details",
        "2-step fix plan aur renewal details saath mein bhej doon",
        eta="2 min",
    )
    body = " ".join(x for x in [lead, val, fix, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Renewal deadline + ignored previous reminder → no hard sell; anchor on the merchant's real numbers and a concrete fix so renewal has a reason.",
        lever="loss_aversion+reciprocity",
        next_action={
            "type": "deliver",
            "topic": "renewal",
            "artifact": f"Plan: (1) {h.fix or 'fix the top listing gap'} this week, (2) weekly Google post. Renewal: {plan}"
            + (f" at {F.money(amt)}" if amt else "")
            + " — I'll share the payment step with your confirmation.",
            "confirm_en": "confirm renewal",
        },
    )


def winback_eligible(c: Ctx) -> Draft:
    p = c.payload
    days = p.get("days_since_expiry", F.g(c.m, "subscription", "days_since_expiry"))
    dip = p.get("perf_dip_pct", F.delta(c.m, "calls_pct"))
    added = p.get("lapsed_customers_added_since_expiry")
    n, label = F.lapsed_count(c.m)
    c.cite("trigger.payload winback / merchant.customer_aggregate")
    lead = (
        f"{c.sal}, it's been {days} days since {F.biz_name(c.m)}'s plan paused"
        if days
        else f"{c.sal}, since {F.biz_name(c.m)}'s plan paused"
    )
    bits = []
    if dip is not None and dip < 0:
        bits.append(f"calls are down {F.pct(dip)}")
    if added:
        bits.append(
            f"{added} more clients have slipped past 90 days without a visit"
            + (f" ({F.num(n)} total now)" if n else "")
        )
    lead += (" — " + " and ".join(bits) + ".") if bits else "."
    nopitch = "Not asking you to renew today."
    offer = F.catalog_offer(c.cat, keywords=("spa", "cleaning", "trial", "check"))
    ask = yes_close(
        c,
        f"show you those {added or 'lapsed'} names + a ready 'we miss you' message"
        + (f" built around '{offer}'" if offer else ""),
        f"Woh {added or 'lapsed'} naam + ek ready 'we miss you' message bhej doon"
        + (f" ('{offer}' ke saath)" if offer else ""),
        eta="5 min",
    )
    body = " ".join(x for x in [lead, nopitch, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Win-back: quantify what the pause is costing (loss aversion) and offer free value (the lapsed list) before any renewal ask.",
        lever="loss_aversion+reciprocity",
        next_action={
            "type": "deliver",
            "topic": "winback list",
            "artifact": f"Win-back message: \"Hi! It's been a while — we've missed you at {F.biz_name(c.m)}. "
            + (f"This week: {offer}. " if offer else "")
            + 'Reply here to grab a slot."',
            "confirm_en": "send it to the lapsed clients",
        },
    )


def dormant_with_vera(c: Ctx) -> Draft:
    p = c.payload
    days = p.get("days_since_last_merchant_message")
    last_topic = p.get("last_topic")
    # give free value — pick the most actionable digest item (trend/tech) for this category
    item = None
    for it in c.cat.get("digest") or []:
        if it.get("kind") in ("trend",) and any(ch.isdigit() for ch in it.get("title", "")):
            item = it
            break
    item = item or F.digest_item(c.cat, None, kinds=("trend", "tech", "seasonal"))
    opener = f"{c.sal}, no sales pitch this time — just one useful thing."
    if last_topic and "subscription" in str(last_topic):
        c.cite("trigger.payload.last_topic")
    if item:
        c.cite(f"category.digest[{item.get('id')}]")
        fact = f"{item.get('title').rstrip('.')} ({item.get('source')})."
        act = item.get("actionable", "")
        est = F.g(c.m, "identity", "established_year")
        qual = f" (you've been open since {est}, so you qualify)" if est and "6 months" in act else ""
        if qual:
            c.cite("merchant.identity.established_year")
        mid = (
            f"For {F.biz_name(c.m)} it's a small change: {act[:1].lower() + act[1:].rstrip('.')}{qual}." if act else ""
        )
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
    body = " ".join(x for x in [opener, fact, mid, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=f"Dormant {days or '?'}d after a pricing/renewal topic → lead with free, verifiable value (reciprocity) instead of repeating the ignored ask.",
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
