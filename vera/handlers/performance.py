"""Triggers driven by the merchant's own numbers: dips, spikes, expected
seasonal lulls and milestones."""

from __future__ import annotations

from .. import artifacts as A
from .. import facts as F
from ..core import Ctx, Draft, best_hook, yes_close
from .fallback import generic


def _metric_from_payload_or_state(c: Ctx, want_sign: int):
    p = c.payload
    if p.get("metric") and p.get("delta_pct") is not None:
        return p["metric"], p["delta_pct"], p.get("vs_baseline"), p.get("window", "7d")
    best = None
    for key, label in (("calls_pct", "calls"), ("views_pct", "views"), ("ctr_pct", "CTR")):
        d = F.delta(c.m, key)
        if d is None:
            continue
        if best is None or d * want_sign > best[1] * want_sign:
            best = (label, d)
    if best:
        return best[0], best[1], None, "7d"
    return None, None, None, None


def perf_dip(c: Ctx, after_spike_alert: bool = False) -> Draft:
    metric, d, base, _ = _metric_from_payload_or_state(c, -1)
    if metric is None:
        return generic(c)
    c.cite(f"merchant/trigger delta {metric}={d}")
    if d is not None and d >= 0:
        if after_spike_alert:
            return generic(c)
        return perf_spike(c, after_dip_alert=True)
    if after_spike_alert:
        # the trigger said "spike" but the merchant's own numbers say otherwise
        lead = f"{c.sal}, I re-checked {F.biz_name(c.m)}'s numbers after a spike flag — the real story is a dip: {metric} fell {F.pct(d)} this week"
    else:
        lead = f"{c.sal}, {metric} to {F.biz_name(c.m)} fell {F.pct(d)} this week"
    if base:
        lead += f" against your usual ~{F.num(base)}"
    lead += "."
    # diagnosis — pick the most fixable cause, never more than one
    h = best_hook(
        c,
        exclude=("dip_calls", "dip_views", "renewal", "lapsed"),
        prefer=("no_offer", "unverified", "stale_posts", "ctr_below_peer", "neg_review"),
    )
    diag, ask = "", ""
    if h and h.key == "no_offer":
        sug = F.catalog_offer(c.cat)
        diag = f"The likeliest cause I can see: {h.text} — searchers see nothing to act on."
        ask = yes_close(c, f"put '{sug}' live on your listing today", f"Aaj hi '{sug}' aapki listing par live kar doon")
        c.cite(h.cite)
        c.cite("category.offer_catalog")
        nxt = {"type": "deliver", "topic": "offer setup", "artifact": A.offer_setup(c), "confirm_en": "publish it"}
    elif h:
        diag = f"What I'd fix first: {h.text}."
        c.cite(h.cite)
        fixes = {
            "unverified": (
                "start your Google verification (one phone call)",
                "Google verification shuru kar doon (bas ek phone call)",
            ),
            "stale_posts": ("draft 3 fresh Google posts for you to approve", "3 fresh Google posts draft kar doon"),
            "ctr_below_peer": (
                "rewrite your listing's first line + photos order to lift CTR",
                "listing ki pehli line aur photos ka order sudhaar doon",
            ),
            "neg_review": ("draft replies to those reviews", "un reviews ke replies draft kar doon"),
        }
        en, hi = fixes.get(h.key, ("send a 3-step recovery plan", "3-step recovery plan bhej doon"))
        ask = yes_close(c, en, hi.capitalize())
        nxt = {
            "type": "deliver",
            "topic": A.HOOK_TOPIC.get(h.key, h.key),
            "artifact": A.for_hook(c, h.key),
            "confirm_en": A.HOOK_CONFIRM.get(h.key, "publish it"),
        }
    else:
        ask = yes_close(c, "send a 3-step recovery plan", "3-step recovery plan bhej doon")
        nxt = {
            "type": "deliver",
            "topic": "recovery plan",
            "artifact": A.recovery_plan(c),
            "confirm_en": "start step 1",
        }
    body = " ".join(x for x in [lead, diag, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=f"{metric} dip {F.pct(d)} is the why-now; paired with the single most fixable cause from merchant state instead of listing every issue.",
        lever="loss_aversion+effort_externalization",
        next_action=nxt,
    )


def perf_spike(c: Ctx, after_dip_alert: bool = False) -> Draft:
    metric, d, base, _ = _metric_from_payload_or_state(c, +1)
    if metric is None or d is None:
        return generic(c)
    if d <= 0:
        return generic(c) if after_dip_alert else perf_dip(c, after_spike_alert=True)
    c.cite(f"delta {metric}={d}")
    driver = c.payload.get("likely_driver")
    verb = "are up" if d >= 0.10 else "ticked up"
    if after_dip_alert:
        lead = f"{c.sal}, I re-checked {F.biz_name(c.m)}'s numbers after a dip flag — this week is actually steady: {metric} {verb} {F.pct(d)}"
    else:
        lead = f"{c.sal}, good news — {metric} {verb} {F.pct(d)} this week"
    if base:
        lead += f" (vs your usual ~{F.num(base)})"
    lead += "."
    why = ""
    if driver:
        why = f"The timing lines up with your {F.humanize(driver)} — that's real demand, not noise."
        c.cite("trigger.payload.likely_driver")
    # convert momentum: use the live offer or the planning thread
    offers = F.active_offers(c.m)
    last_vera = (F.last_vera_turn(c.m) or {}).get("body", "")
    price = F.price_in(last_vera) if driver and "kids" in driver else None
    if price:
        conv = f"Strike while it's warm: a follow-up post with the {price} camp details and a 'book a trial' button."
        what = ("publish that follow-up post today", "Aaj hi woh follow-up post publish kar doon")
    elif offers:
        conv = f"Best way to convert the extra traffic: pin '{offers[0]}' to the top of your listing this week."
        what = ("pin it + post a matching Google update", "Pin karke matching Google update post kar doon")
        c.cite("merchant.offers")
    else:
        h = best_hook(c, prefer=("unverified", "no_offer"))
        if h and h.key == "unverified":
            conv = "One catch: your listing is still unverified, so part of this traffic leaks — verifying locks it in."
            what = ("start verification (one call)", "Verification shuru kar doon (bas ek call)")
            c.cite(h.cite)
        else:
            sug = F.catalog_offer(c.cat)
            conv = "The gap: there's no offer live to turn these visits into bookings."
            what = (f"put '{sug}' live on your listing today", f"Aaj hi '{sug}' listing par live kar doon")
    ask = yes_close(c, what[0], what[1])
    body = " ".join(x for x in [lead, why, conv, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Positive swing is the hook; message converts momentum into one concrete action rather than just congratulating.",
        lever="momentum+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "momentum post",
            "artifact": (
                A.verification_steps(c)
                if "verif" in what[0]
                else A.three_posts(c).split("\n")[0].replace("1) ", "Google post: ")
            ),
            "confirm_en": "publish it",
        },
    )


def seasonal_perf_dip(c: Ctx) -> Draft:
    p = c.payload
    metric = p.get("metric", "views")
    d = p.get("delta_pct", F.delta(c.m, f"{metric}_pct"))
    beat = None
    for b in c.cat.get("seasonal_beats") or []:
        if "acquisition" in b.get("note", "") or "lowest" in b.get("note", ""):
            beat = b
    item = F.digest_item(c.cat, None, kinds=("seasonal",))
    lead = f"{c.sal}, {metric} are down {F.pct(d)} this week — before you worry: "
    if beat:
        lead += f"{beat['month_range']} is the {beat['note'].split(' — ')[0]} for {c.cat.get('display_name', c.slug).lower()}, so this is the season, not your listing."
        c.cite("category.seasonal_beats")
    else:
        lead += "this is the expected seasonal lull, not your listing."
    ca = c.m.get("customer_aggregate") or {}
    members, churn = ca.get("total_active_members"), ca.get("monthly_churn_pct")
    peer_churn = F.g(c.cat, "peer_stats", "monthly_churn_pct")
    focus = ""
    if members and churn:
        lost = int(members * churn)
        focus = (
            f"The number that matters now is retention: your churn is {F.pct(churn)}/month"
            + (f" vs {F.pct(peer_churn)} for {F.peer_scope(c.cat)}" if peer_churn else "")
            + f" — on {F.num(members)} members that's ~{lost} people a month."
        )
        c.cite(
            "merchant.customer_aggregate.total_active_members, monthly_churn_pct; category.peer_stats.monthly_churn_pct"
        )
    spend = ""
    if item and item.get("actionable"):
        spend = f"Ad spend: {item['actionable'][:1].lower() + item['actionable'][1:].rstrip('.')}."
        c.cite(f"category.digest[{item.get('id')}]")
    ask = yes_close(
        c,
        "draft a 4-week 'summer streak' challenge to keep current members showing up",
        "Current members ke liye 4-week 'summer streak' challenge draft kar doon",
    )
    body = " ".join(x for x in [lead, focus, spend, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Expected seasonal dip: pre-empt panic, redirect attention to the metric the merchant can move (churn vs peer), one retention action.",
        lever="anxiety_preemption+specificity",
        next_action={
            "type": "deliver",
            "topic": "retention challenge",
            "artifact": "Summer Streak (4 weeks): attend 3x/week → name on the leaderboard wall; 12/12 sessions → free body-composition check. Launch message for members drafted.",
            "confirm_en": "send the launch message to members",
        },
    )


def milestone_reached(c: Ctx) -> Draft:
    p = c.payload
    metric, now_v, goal = p.get("metric"), p.get("value_now"), p.get("milestone_value")
    rp = F.review_theme(c.m, "pos")
    if metric and now_v is not None and goal:
        c.cite("trigger.payload milestone")
        gap = goal - now_v
        mlabel = F.humanize(metric).replace("review count", "reviews")
        if gap > 0:
            lead = (
                f"{c.sal}, {F.biz_name(c.m)} is {gap} {mlabel} away from {F.num(goal)} on Google ({F.num(now_v)} now)."
            )
        else:
            lead = f"{c.sal}, {F.biz_name(c.m)} just crossed {F.num(goal)} {mlabel} on Google."
        peer = F.g(c.cat, "peer_stats", "avg_review_count")
        peer_line = ""
        if peer and "review" in metric and now_v > peer:
            peer_line = f"That's already above the {F.num(peer)} average for {F.peer_scope(c.cat)}."
            c.cite("category.peer_stats.avg_review_count")
        fast = ""
        if rp:
            fast = f"Fastest route: your {F.theme(rp['theme'])} regulars — {rp['occurrences_30d']} reviews this month already praise it."
            c.cite("merchant.review_themes")
        ask = yes_close(
            c,
            "make a small table card + a WhatsApp line asking happy regulars for a review",
            "Happy regulars se review maangne ke liye table card + WhatsApp line bana doon",
        )
        body = " ".join(x for x in [lead, peer_line, fast, ask] if x)
    else:
        # derive a *meaningful* milestone from state: only celebrate a number
        # that is at/above the peer benchmark; otherwise use the business
        # anniversary; otherwise fall back to the generic reasoner.
        best = None
        peer = c.cat.get("peer_stats") or {}
        for key, label, pk in (
            ("calls", "calls", "avg_calls_30d"),
            ("directions", "direction requests", "avg_directions_30d"),
            ("views", "profile views", "avg_views_30d"),
        ):
            v, pv = F.perf(c.m, key), peer.get(pk)
            if not v or not pv or v < pv:
                continue
            for t in (10000, 5000, 3000, 2000, 1000, 500, 250, 100, 50, 25):
                if v >= t:
                    best = (label, v, t, pv)
                    break
            if best:
                break
        est = F.g(c.m, "identity", "established_year")
        if best:
            c.cite(f"merchant.performance.{best[0]} vs category.peer_stats")
            lead = (
                f"{c.sal}, milestone: {F.biz_name(c.m)} crossed {F.num(best[2])} {best[0]} in the last 30 days "
                f"({F.num(best[1])}) — above the {F.num(best[3])} average for {F.peer_scope(c.cat)}."
            )
        elif est and c.now and c.now.year - int(est) >= 2:
            yrs = c.now.year - int(est)
            c.cite("merchant.identity.established_year")
            lead = f"{c.sal}, {F.biz_name(c.m)} completes {yrs} years in {F.locality(c.m) or F.city(c.m)} this year (since {est}) — that's a story customers like hearing."
        else:
            return generic(c)
        h = best_hook(c, prefer=("no_offer", "unverified", "stale_posts"))
        conv = ""
        if h:
            conv = f"To turn that into bookings, one gap to close: {h.text}."
            c.cite(h.cite)
        what = (
            "post a 'thank you, "
            + ("regulars" if best else f"{yrs} years")
            + "' Google update"
            + (" and fix that gap in one go" if h else "")
        )
        ask = yes_close(
            c,
            what,
            "Ek 'thank you' Google update post karke"
            + (" woh gap bhi theek kar doon" if h else " customers ko bata doon"),
        )
        body = " ".join(x for x in [lead, conv, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Milestone framed as a near-miss/achievement with a concrete gap; social proof from own reviews; one low-effort action.",
        lever="goal_gradient+social_proof",
        next_action={
            "type": "deliver",
            "topic": "review ask",
            "artifact": f'Table card: "Loved your visit to {F.biz_name(c.m)}? A 20-second Google review helps us a lot 🙏 — scan the QR."\nWhatsApp line: "Thanks for coming in! If you enjoyed it, a quick Google review would mean a lot to our team."',
            "confirm_en": "print-ready PDF + WhatsApp broadcast",
        },
    )
