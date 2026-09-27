"""Triggers driven by the merchant's own numbers: dips, spikes, expected
seasonal lulls and milestones.

Week-on-week deltas are quoted only when the trigger payload carries them; the
merchant's 30-day views / calls / CTR anchor everything else.
"""

from __future__ import annotations

import re

from .. import artifacts as A
from .. import facts as F
from ..core import Ctx, Draft, because, best_hook, yes_close
from .fallback import generic

FIXES = {
    "no_offer": None,  # built from the catalog below
    "unverified": (
        "start your Google verification (one phone call)",
        "Google verification shuru kar doon (bas ek phone call)",
    ),
    "stale_posts": ("draft 3 fresh Google posts for you to approve", "3 fresh Google posts draft kar doon"),
    "ctr_below_peer": (
        "rewrite your listing's first line + photo order to lift CTR",
        "listing ki pehli line aur photos ka order sudhaar doon",
    ),
    "neg_review": ("draft replies to those reviews", "un reviews ke replies draft kar doon"),
}


def _payload_metric(c: Ctx):
    p = c.payload
    if p.get("metric") and p.get("delta_pct") is not None:
        return p["metric"], p["delta_pct"], p.get("vs_baseline")
    return None, None, None


def _state_direction(c: Ctx) -> float | None:
    """The merchant's own week-on-week move (used to sanity-check the alert):
    a real drop in either metric counts as a dip, a real rise as a spike."""
    deltas = [d for d in (F.delta(c.m, "calls_pct"), F.delta(c.m, "views_pct")) if d is not None]
    if not deltas:
        return None
    worst, best = min(deltas), max(deltas)
    if worst <= -0.10:
        return worst
    if best >= 0.10:
        return best
    return worst if abs(worst) >= abs(best) else best


def _own_swing(c: Ctx, falling: bool) -> str:
    """'calls down 40% week on week' from the merchant's own 7-day numbers."""
    parts = []
    for key, label in (("calls_pct", "calls"), ("views_pct", "profile views")):
        d = F.delta(c.m, key)
        if d is None:
            continue
        if (falling and d <= -0.10) or (not falling and d >= 0.10):
            parts.append(f"{label} {'down' if d < 0 else 'up'} {F.pct(abs(d))}")
    if parts:
        c.cite("merchant.performance.delta_7d")
    return " and ".join(parts)


def _gaps(c: Ctx, limit: int = 2) -> list[str]:
    """Checkable gaps flagged on the merchant's profile."""
    out = []
    if not F.active_offers(c.m):
        out.append("no live offer")
    if F.has_signal(c.m, "unverified_gbp"):
        out.append("an unverified Google listing")
    stale = F.signal_value(c.m, "stale_posts")
    if stale:
        out.append(f"a last Google post {stale.rstrip('d')} days old")
    if F.has_signal(c.m, "ctr_below_peer_median"):
        out.append("a below-median CTR")
    return out[:limit]


def _fix_and_next(c: Ctx, hook) -> tuple[str, str, dict]:
    if hook is None or hook.key == "no_offer":
        sug = F.suggested_offer(c.cat, c.m)
        c.cite("category.offer_catalog")
        if F.g(c.m, "subscription", "status") == "expired":
            # offers only show on an active plan — say so instead of promising the impossible
            c.cite("merchant.subscription (expired)")
            return (
                f"reactivate your plan and put magicpin's standard '{sug}' offer live the same day",
                f"Plan reactivate karke usi din magicpin ka standard '{sug}' offer live kar doon",
                {
                    "type": "deliver",
                    "topic": "offer setup",
                    "artifact": A.offer_setup(c),
                    "confirm_en": "reactivate and publish",
                },
            )
        if c.slug == "dentists":
            return (
                f"put '{sug}' live as your first-visit offer (magicpin's standard for clinics) — the easiest way for a new patient to book a check-up",
                f"'{sug}' ko first-visit offer ki tarah listing par live kar doon (clinics ke liye magicpin ka standard)",
                {"type": "deliver", "topic": "offer setup", "artifact": A.offer_setup(c), "confirm_en": "publish it"},
            )
        why = {
            "salons": " as your walk-in offer",
            "gyms": " so trial-seekers have a reason to call",
            "restaurants": " so people searching for a meal have a reason to order",
            "pharmacies": " for your regulars",
        }.get(c.slug, "")
        return (
            f"put magicpin's standard '{sug}' offer live on your listing today{why}",
            f"Aaj hi magicpin ka standard '{sug}' offer aapki listing par live kar doon",
            {"type": "deliver", "topic": "offer setup", "artifact": A.offer_setup(c), "confirm_en": "publish it"},
        )
    en, hi = FIXES.get(hook.key) or ("send a 3-step recovery plan", "3-step recovery plan bhej doon")
    return (
        en,
        hi[:1].upper() + hi[1:],
        {
            "type": "deliver",
            "topic": A.HOOK_TOPIC.get(hook.key, hook.key),
            "artifact": A.for_hook(c, hook.key),
            "confirm_en": A.HOOK_CONFIRM.get(hook.key, "publish it"),
        },
    )


def perf_dip(c: Ctx, after_spike_alert: bool = False) -> Draft:
    metric, d, base = _payload_metric(c)
    own = _state_direction(c)
    if metric is None and own is not None and own > 0 and not after_spike_alert:
        return perf_spike(c, after_dip_alert=True)  # the alert and the numbers disagree: say so honestly
    c.cite("trigger.payload" if metric else "merchant.performance")

    if metric:
        lead = f"{c.sal}, {metric} to {F.biz_name(c.m)} fell {F.pct(d)} this week"
        lead += f" against a baseline of {F.num(base)}." if base else "."
    elif after_spike_alert:
        swing = _own_swing(c, falling=True)
        lead = f"{c.sal}, I re-checked {F.biz_name(c.m)} after a spike flag — the real story is a dip" + (
            f": {swing} week on week." if swing else "."
        )
    else:
        swing = _own_swing(c, falling=True)
        lead = (
            f"{c.sal}, {F.biz_name(c.m)} is in a dip this week — your own 7-day numbers show {swing}."
            if swing
            else f"{c.sal}, a dip alert just fired on {F.biz_name(c.m)}."
        )
    snapshot = F.perf_line(c.m, "Your listing had ") + "." if F.perf_line(c.m) else ""

    gaps = _gaps(c)
    hook = best_hook(
        c,
        exclude=("dip_calls", "dip_views", "renewal", "lapsed"),
        prefer=("no_offer", "unverified", "stale_posts", "ctr_below_peer"),
    )
    diag = ""
    if gaps:
        diag = (
            f"Your profile shows {' and '.join(gaps)}"
            + (" — patients" if c.slug == "dentists" else " — people")
            + " searching nearby find nothing to act on."
        )
        c.cite("merchant.signals / offers")
    weekly = F.weekly_views(c.m)
    loss = f"That's ~{F.num(weekly)} profile views a week going nowhere." if weekly and gaps else ""
    en, hi, nxt = _fix_and_next(c, hook)
    ask = yes_close(c, en, hi)
    body = " ".join(x for x in [lead, snapshot, diag, loss, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            "Dip is the why-now",
            (
                "the payload's own delta and baseline"
                if metric
                else ("the merchant's own 7-day swing" if "merchant.performance.delta_7d" in c.used else "")
            ),
            "their 30-day views/calls/CTR" if snapshot else "",
            f"the flagged gaps ({', '.join(gaps)})" if gaps else "",
            f"one fix for the most fixable gap ({hook.key if hook else 'no live offer'})",
        ),
        lever="loss_aversion+effort_externalization",
        next_action=nxt,
    )


def perf_spike(c: Ctx, after_dip_alert: bool = False) -> Draft:
    metric, d, base = _payload_metric(c)
    own = _state_direction(c)
    if metric is None and own is not None and own < 0 and not after_dip_alert:
        return perf_dip(c, after_spike_alert=True)
    if metric is not None and d is not None and d <= 0:
        return perf_dip(c, after_spike_alert=True)
    c.cite("trigger.payload" if metric else "merchant.performance")

    if metric:
        verb = "are up" if d >= 0.10 else "ticked up"
        lead = f"{c.sal}, good news — {metric} {verb} {F.pct(d)} this week"
        lead += f" against a baseline of {F.num(base)}." if base else "."
    elif after_dip_alert:
        moves = []
        for key, label in (("views_pct", "views"), ("calls_pct", "calls")):
            dv = F.delta(c.m, key)
            if dv is not None:
                moves.append(f"{label} {'+' if dv >= 0 else '-'}{F.pct(abs(dv))}")
        if moves:
            c.cite("merchant.performance.delta_7d")
        lead = f"{c.sal}, I re-checked {F.biz_name(c.m)} after a dip flag — this week is actually holding up" + (
            f" ({', '.join(moves)} week on week)." if moves else "."
        )
    else:
        swing = _own_swing(c, falling=False) or ", ".join(
            f"{label} +{F.pct(dv)}"
            for label, dv in (("calls", F.delta(c.m, "calls_pct")), ("views", F.delta(c.m, "views_pct")))
            if dv is not None and dv > 0
        )
        lead = (
            f"{c.sal}, good news — {F.biz_name(c.m)} is getting more attention: {swing} week on week."
            if swing
            else f"{c.sal}, good news — {F.biz_name(c.m)} is getting more attention this week."
        )
    snapshot = F.perf_line(c.m, "Your listing had ") + "." if F.perf_line(c.m) else ""

    driver = c.payload.get("likely_driver")
    why = ""
    if driver:
        why = f"The timing lines up with your {F.humanize(driver)} — that's real demand, not noise."
        c.cite("trigger.payload.likely_driver")

    offers = F.active_offers(c.m)
    conv, what, used, nxt = _spike_play(c, offers, driver)
    ask = yes_close(c, what[0], what[1])
    body = " ".join(x for x in [lead, snapshot, why, conv, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            "Positive swing is the hook, turned into one concrete action instead of congratulations",
            "the payload's delta and baseline" if metric else "",
            "their 30-day views/calls/CTR" if snapshot else "",
            "the likely driver from the payload" if driver else "",
            used,
        ),
        lever="momentum+effort_externalization",
        next_action=nxt,
    )


def _refill_item(cat: dict) -> dict | None:
    for it in cat.get("digest") or []:
        if "subscription" in it.get("id", "") or "refill reminder" in it.get("summary", "").lower():
            return it
    return None


def _spike_play(c: Ctx, offers: list[str], driver) -> tuple[str, tuple[str, str], str, dict]:
    """How to cash in extra attention: (line, (ask_en, ask_hi), rationale part, next_action)."""
    post = {
        "type": "deliver",
        "topic": "momentum post",
        "artifact": A.three_posts(c).split("\n")[0].replace("1) ", "Google post: "),
        "confirm_en": "publish it",
    }
    chronic = F.g(c.m, "customer_aggregate", "chronic_rx_count")
    item = _refill_item(c.cat) if c.slug == "pharmacies" else None
    if item and chronic and not driver:
        ratio = re.search(r"(\d+(?:\.\d+)?)x", item.get("title", ""))
        line = f"Turn the extra attention into repeat business: put your {F.num(chronic)} chronic-Rx customers on WhatsApp refill reminders"
        line += f" — pharmacies that do keep {ratio.group(1)}x more of them ({item.get('source')})." if ratio else "."
        c.cite("merchant.customer_aggregate.chronic_rx_count")
        c.cite(f"category.digest[{item.get('id')}]")
        return (
            line,
            ("set up those refill reminders this week", "Is hafte woh refill reminders set up kar doon"),
            "their chronic-Rx base and the digest's refill-reminder finding",
            {
                "type": "deliver",
                "topic": "refill reminders",
                "artifact": item.get("actionable", ""),
                "confirm_en": "switch them on",
            },
        )
    if driver and offers:
        return (
            f"Strike while it's warm: a follow-up post with a 'book a trial' button, and pin '{offers[0]}' next to it.",
            ("publish that follow-up post today", "Aaj hi woh follow-up post publish kar doon"),
            f"their live offer ('{offers[0]}') pinned next to a follow-up post",
            post,
        )
    if offers:
        c.cite("merchant.offers")
        return (
            f"Best way to convert the extra traffic: pin '{offers[0]}' to the top of your listing this week.",
            ("pin it + post a matching Google update", "Pin karke matching Google update post kar doon"),
            f"their live offer ('{offers[0]}')",
            post,
        )
    if F.has_signal(c.m, "unverified_gbp"):
        c.cite("merchant.signals.unverified_gbp")
        return (
            "One catch: your listing is still unverified, so part of this traffic leaks — verifying locks it in.",
            ("start verification (one call)", "Verification shuru kar doon (bas ek call)"),
            "verification, since the listing is unverified",
            {"type": "deliver", "topic": "verification", "artifact": A.verification_steps(c), "confirm_en": "start it"},
        )
    en, hi, nxt = _fix_and_next(c, None)
    paused = F.g(c.m, "subscription", "days_since_expiry") if F.g(c.m, "subscription", "status") == "expired" else None
    return (
        (
            f"The gap: your plan paused {paused} days ago, so no offer is live to turn these visits into bookings."
            if paused
            else "The gap: there's no live offer to turn these visits into bookings."
        ),
        (en, hi),
        "a catalog offer suggestion, since nothing is live",
        nxt,
    )


def seasonal_perf_dip(c: Ctx) -> Draft:
    p = c.payload
    metric = p.get("metric", "views")
    d = p.get("delta_pct")
    note = F.humanize(p.get("season_note", ""))
    note = re.sub(
        r"^post resolution window (\w+) (\w+)$",
        lambda m: f"post-resolution lull ({m.group(1).title()}–{m.group(2).title()})",
        note,
    )
    c.cite("trigger.payload")

    lead = (
        f"{c.sal}, {metric} are down {F.pct(d)} this week — before you worry, "
        if d is not None
        else f"{c.sal}, before you worry about this week's dip — "
    )
    lead += (
        f"this is the {note}, not your listing." if note else "this is the expected seasonal lull, not your listing."
    )

    views, calls, ctr = F.perf_numbers(c.m)
    proof = ""
    if ctr and calls is not None:
        proof = f"The people who do find you still act: CTR {ctr}"
        proof += ", above the peer benchmark" if F.has_signal(c.m, "above_peer_ctr") else ""
        proof += f", {F.num(calls)} calls in the last 30 days."
        c.cite("merchant.performance + signal above_peer_ctr")
    members = F.g(c.m, "customer_aggregate", "total_active_members")
    who = f"your {F.num(members)} active members" if members else f"the {F.people(c.slug)} you already have"
    if members:
        c.cite("merchant.customer_aggregate.total_active_members")
    nxt = "Sept–Oct" if F.month_of(c.now) in (None, 3, 4, 5, 6, 7) else "the next acquisition window"
    judgment = (
        f"So here's the play: zero ad spend into a slow season — save it for {nxt} — and spend this quarter on {who}."
    )
    gap = ""
    if F.has_signal(c.m, "no_recent_post"):
        gap = "A 4-week 'summer streak' challenge keeps them showing up through the lull, and a Google post about it fixes your no-recent-post gap in the same move."
        c.cite("merchant.signals.no_recent_post")
    else:
        gap = "A 4-week 'summer streak' challenge keeps them showing up through the lull."
    ask = yes_close(
        c,
        "draft the challenge + the Google post",
        "Challenge + Google post dono draft kar doon",
    )
    body = " ".join(x for x in [lead, proof, judgment, gap, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Expected seasonal dip: pre-empt panic with the payload's own season note, prove the listing still converts with the merchant's CTR and calls, redirect effort to retention.",
        lever="anxiety_preemption+specificity",
        next_action={
            "type": "deliver",
            "topic": "retention challenge",
            "artifact": "Summer Streak (4 weeks): attend 3x/week → name on the leaderboard wall; 12/12 sessions → free body-composition check. Launch message for members + Google post drafted.",
            "confirm_en": "send the launch message to members",
        },
    )


def _earned_milestone(c: Ctx) -> str:
    views, calls, ctr = F.perf_numbers(c.m)
    peer = c.cat.get("peer_stats") or {}
    scope = F.peer_scope(c.cat)
    best = None  # (ratio, sentence)
    for mine, avg, label, show in (
        (F.perf(c.m, "ctr"), peer.get("avg_ctr"), "CTR", lambda v: F.ctr_str(v)),
        (calls, peer.get("avg_calls_30d"), "calls", lambda v: F.num(v)),
        (views, peer.get("avg_views_30d"), "profile views", lambda v: F.num(v)),
    ):
        if mine and avg and mine > avg * 1.1:
            ratio = mine / avg
            if label == "CTR":
                text = f"your CTR is {show(mine)}, ahead of the {show(avg)} average for {scope}"
            else:
                text = f"{show(mine)} {label} in the last 30 days, ahead of the {show(avg)} average for {scope}"
            if not best or ratio > best[0]:
                best = (ratio, text)
    if best:
        c.cite("merchant.performance + category.peer_stats")
        return f"{c.sal}, a number worth celebrating: {best[1]}."
    est = F.g(c.m, "identity", "established_year")
    year = c.now.year if c.now else None
    if est and year and year - est >= 3:
        c.cite("merchant.identity.established_year")
        where = F.locality(c.m)
        return f"{c.sal}, a milestone worth marking: {year - est} years of {F.biz_name(c.m)}" + (
            f" in {where}." if where else "."
        )
    return ""


def milestone_reached(c: Ctx) -> Draft:
    p = c.payload
    metric, now_v, goal = p.get("metric"), p.get("value_now"), p.get("milestone_value")
    views, calls, _ = F.perf_numbers(c.m)
    offers = F.active_offers(c.m)
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
        why = ""
        peer = F.g(c.cat, "peer_stats", "avg_review_count")
        if "review" in str(metric) and peer and now_v and now_v > peer:
            why = f"You're already past the {F.num(peer)}-review average for {F.peer_scope(c.cat)}; {F.num(goal)} puts real distance between you and the next listing."
            c.cite("category.peer_stats.avg_review_count")
        elif views and "review" in str(metric):
            why = f"With {F.num(views)} profile views a month, review count is one of the first things those people compare."
            c.cite("merchant.performance.views")
        fast = ""
        if gap > 0:
            pos = F.review_theme(c.m, "pos")
            fans = ""
            if pos and pos.get("occurrences_30d"):
                fans = f" — {pos['occurrences_30d']} reviews this month already praise your {F.theme(pos['theme'])}"
                c.cite("merchant.review_themes")
            crowd = (
                f"your '{offers[0]}' crowd is the obvious place to start{fans}."
                if offers
                else f"start with the regulars who already love you{fans}."
            )
            fast = f"Fastest route: ask {gap} happy regulars this week — {crowd}"
        ask = yes_close(
            c,
            "make a small table card + a WhatsApp line asking happy regulars for a review",
            "Happy regulars se review maangne ke liye table card + WhatsApp line bana doon",
        )
        body = " ".join(x for x in [lead, why, fast, ask] if x)
    else:
        # no milestone in the payload: celebrate something real — beating the
        # category's peer average, or years in business — never a made-up threshold
        lead = _earned_milestone(c)
        if not lead:
            return generic(c)
        h = best_hook(c, prefer=("no_offer", "unverified", "stale_posts", "ctr_below_peer"))
        outcome = "orders" if c.slug == "restaurants" else "bookings"
        if " years of " in lead:
            conv = f"Worth marking — and one gap still holds the listing back: {h.text}." if h else ""
        else:
            conv = f"To turn that attention into {outcome}, one gap to close: {h.text}." if h else ""
        if h:
            c.cite(h.cite)
        en, hi, nxt = _fix_and_next(c, h)
        ask = yes_close(c, en, hi)
        body = " ".join(x for x in [lead, conv, ask] if x)
        return Draft(
            body,
            "binary_yes_no",
            rationale="Milestone on the merchant's own 30-day numbers, paired with the single gap that stops the extra attention converting, and one effortless fix.",
            lever="goal_gradient+effort_externalization",
            next_action=nxt,
        )
    return Draft(
        body,
        "binary_yes_no",
        rationale="Milestone framed as a near-miss or achievement on the merchant's own numbers, with one low-effort action to close it.",
        lever="goal_gradient+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "review ask",
            "artifact": f'Table card: "Loved your visit to {F.biz_name(c.m)}? A 20-second Google review helps us a lot 🙏 — scan the QR."\nWhatsApp line: "Thanks for coming in! If you enjoyed it, a quick Google review would mean a lot to our team."',
            "confirm_en": "print-ready PDF + WhatsApp broadcast",
        },
    )
