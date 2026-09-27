"""Review themes and new competitors."""

from __future__ import annotations

from .. import facts as F
from ..core import Ctx, Draft, because, yes_close


def _conversion_line(c: Ctx) -> str:
    views, calls, ctr = F.perf_numbers(c.m)
    if views is None or calls is None:
        return ""
    return f"{F.num(views)} people viewed your listing in the last 30 days and {F.num(calls)} called" + (
        f" (CTR {ctr})" if ctr else ""
    )


def review_theme_emerged(c: Ctx) -> Draft:
    p = c.payload
    theme = p.get("theme")
    rt = F.review_theme(c.m, theme=theme) if theme else F.review_theme(c.m, "neg")
    if not theme and not rt:
        return _no_review_pattern(c)
    theme = theme or rt.get("theme")
    occ = p.get("occurrences_30d") or (rt or {}).get("occurrences_30d")
    quote = p.get("common_quote") or (rt or {}).get("common_quote")
    trend = p.get("trend")
    c.cite("trigger.payload")

    lead = (
        f"{c.sal}, {occ} reviews this month now mention {F.theme(theme)}"
        if occ
        else f"{c.sal}, a pattern in your reviews: {F.theme(theme)}"
    )
    lead += " — and it's rising." if trend == "rising" else "."
    if quote:
        lead += f' Latest one: "{quote}".'
    stakes = ""
    conv = _conversion_line(c)
    if conv:
        stakes = f"That matters because {conv} — and newer reviews are what those viewers read first."
        c.cite("merchant.performance")
    pos = F.review_theme(c.m, "pos")
    frame = ""
    if pos and pos.get("theme") != theme:
        frame = (
            f"People still praise your {F.theme(pos['theme'])}, so this is an ops fix, not a reputation problem — yet."
        )
    ask = yes_close(
        c,
        "draft calm public replies to these reviews (you approve before posting)",
        "In reviews ke liye shaant public replies draft kar doon (post karne se pehle aap approve karenge)",
    )
    body = " ".join(x for x in [lead, stakes, frame, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Emerging negative review theme from the payload (count, trend, verbatim quote), tied to the merchant's own views/calls so the cost is concrete; one effortless fix.",
        lever="loss_aversion+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "review replies",
            "artifact": f"Draft reply: \"Thank you for the honest feedback — you're right, that's not the experience we want. We've tightened our {F.theme(theme).replace('late deliveries', 'delivery timings')} and would love to make it up to you on your next order. — Team {F.biz_name(c.m)}\"",
            "confirm_en": "post the replies",
        },
    )


def _no_review_pattern(c: Ctx) -> Draft:
    """The review check fired but no recurring theme exists: say so plainly,
    then use the moment to grow fresh reviews (what profile viewers read)."""
    name = F.biz_name(c.m)
    lead = f"{c.sal}, I went through {F.possessive(name)} recent reviews — no recurring complaint, which is good news."
    views, calls, _ = F.perf_numbers(c.m)
    stakes = ""
    if views:
        stakes = f"Next step is more of them: {F.num(views)} people viewed your profile in the last 30 days"
        stakes += f" and {F.num(calls)} called" if calls is not None else ""
        stakes += ", and recent reviews are the first thing they read before deciding."
        c.cite("merchant.performance")
    ask = yes_close(
        c,
        f"draft a 2-line WhatsApp you can send happy {F.people(c.slug)} asking for a quick Google review",
        f"Khush {F.people(c.slug)} ke liye 2-line WhatsApp review request draft kar doon",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, stakes, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Review-theme check with no recurring negative theme on file: honest all-clear, then the next lever (review volume) tied to the merchant's own views/calls; one effortless ask.",
        lever="reassurance+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "review requests",
            "artifact": f'"Thank you for visiting {name}! If you liked it, a 30-second Google review helps us a lot: <your Google review link>. — Team {name}"',
            "confirm_en": "send it to your recent customers",
        },
    )


def competitor_opened(c: Ctx) -> Draft:
    p = c.payload
    name, dist, their, opened = (
        p.get("competitor_name"),
        p.get("distance_km"),
        p.get("their_offer"),
        p.get("opened_date"),
    )
    mine = F.active_offers(c.m)
    price_gap = None
    if name:
        c.cite("trigger.payload competitor")
        noun = c.cat.get("display_name", "").split(" ")[0].rstrip("s").lower() or "competitor"
        lead = (
            f"{c.sal}, a new {noun} — {name} — opened {dist} km from you"
            + (f" on {F.day_month(opened)}" if opened else "")
            + "."
        )
        if their:
            words = [w for w in their.split(" @")[0].split() if len(w) > 3]
            my_same = F.offer_matching(mine, *words) if words else None
            their_price, my_price = F.price_in(their), F.price_in(my_same)
            if my_same and their_price and my_price:
                price_gap = int(my_price.strip("₹").replace(",", "")) - int(their_price.strip("₹").replace(",", ""))
                lead += (
                    f" They're advertising {their} — ₹{price_gap} under your {my_price}."
                    if price_gap > 0
                    else f" They're advertising {their}."
                )
            else:
                lead += f" They're advertising {their}."
    else:
        lead = f"{c.sal}, competitor alert for {F.locality(c.m) or 'your area'} — a new listing is competing for the same searches. Details are thin, but the playbook doesn't depend on them."

    cheaper = f"₹{price_gap} cheaper" if price_gap and price_gap > 0 else "cheaper"
    judg = (
        f"I wouldn't price-match — people comparing on Google pick the profile that looks more active and trusted, not the one that's {cheaper}."
        if their
        else "I wouldn't discount in response — people comparing on Google pick the profile that looks more active and trusted."
    )

    exposure = ""
    views, calls, ctr = F.perf_numbers(c.m)
    weak = []
    stale = F.signal_value(c.m, "stale_posts")
    if stale:
        weak.append(f"your last post is {stale.rstrip('d')} days old")
    if F.has_signal(c.m, "ctr_below_peer_median") and ctr:
        weak.append(f"your CTR ({ctr}) is already below the peer median")
    if not mine:
        weak.append("there's no live offer on your listing")
    if weak:
        exposure = "That's where they'll win searchers: " + " and ".join(weak) + "."
        c.cite("merchant.signals / offers")
    pos = F.review_theme(c.m, "pos")
    if not weak and (pos or mine):
        edge = []
        if pos and pos.get("occurrences_30d"):
            edge.append(f"{pos['occurrences_30d']} reviews this month praise your {F.theme(pos['theme'])}")
            c.cite("merchant.review_themes")
        if mine:
            edge.append(f"'{mine[0]}' is live")
            c.cite("merchant.offers")
        if edge:
            exposure = (
                "Your edge is already there — " + " and ".join(edge) + ". Make both impossible to miss."
                if len(edge) > 1
                else "Your edge is already there — " + edge[0] + ". Make it impossible to miss."
            )
    conv = ""
    if views and calls is not None:
        conv = (
            f"Of your {F.num(views)} profile views last month, {F.num(calls)} became calls — every point of CTR you lose now is a patient who books with them."
            if c.slug == "dentists"
            else f"Of your {F.num(views)} profile views last month, {F.num(calls)} became calls — that's the number to protect."
        )
        c.cite("merchant.performance")
    quote = (pos or {}).get("common_quote")
    pin = (
        f'pin the review that says "{quote}"'
        if quote
        else ("pin your best patient review" if c.slug == "dentists" else "pin your best review")
    )
    pin_hi = f'"{quote}" wala review pin kar doon' if quote else "best review pin kar doon"
    ask = yes_close(
        c,
        f"post a fresh Google update and {pin} this week",
        f"Is hafte ek fresh Google update post karke {pin_hi}",
    )
    body = " ".join(x for x in [lead, judg, exposure, conv, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            "Competitor opening as a loss-aversion hook with contrarian judgment (don't discount)",
            (
                "the payload's competitor name, distance and price"
                if name
                else "no competitor details in the payload, so none are invented"
            ),
            "the profile weaknesses from their own signals" if exposure else "",
            "their own views-to-calls conversion" if conv else "",
        ),
        lever="loss_aversion+judgment",
        next_action={
            "type": "deliver",
            "topic": "profile defense",
            "artifact": f'Google post: "{F.biz_name(c.m)}, {F.locality(c.m) or ""} — book on WhatsApp in 1 minute, minimal waiting."\nPinned: your most-praised recent review.',
            "confirm_en": "publish",
        },
    )
