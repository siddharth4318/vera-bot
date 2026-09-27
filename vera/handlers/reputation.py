"""Review themes and new competitors."""

from __future__ import annotations

from .. import facts as F
from ..core import Ctx, Draft, yes_close
from .fallback import generic


def review_theme_emerged(c: Ctx) -> Draft:
    p = c.payload
    theme = p.get("theme")
    rt = F.review_theme(c.m, theme=theme) if theme else F.review_theme(c.m, "neg")
    if not theme and not rt:
        return generic(c)
    theme = theme or rt.get("theme")
    occ = p.get("occurrences_30d") or (rt or {}).get("occurrences_30d")
    quote = p.get("common_quote") or (rt or {}).get("common_quote")
    trend = p.get("trend")
    c.cite("trigger.payload / merchant.review_themes")
    lead = (
        f"{c.sal}, {occ} reviews this month now mention {F.theme(theme)}"
        if occ
        else f"{c.sal}, a pattern in your reviews: {F.theme(theme)}"
    )
    lead += " — and it's rising." if trend == "rising" else "."
    if quote:
        lead += f' Latest: "{quote}".'
    pos = F.review_theme(c.m, "pos")
    frame = ""
    if pos and pos.get("theme") != theme:
        frame = f"Your {F.theme(pos['theme'])} is still your strongest theme ({pos['occurrences_30d']} positive), so this is an ops fix, not a reputation problem — yet."
        c.cite("merchant.review_themes (positive)")
    ask = yes_close(
        c,
        "draft calm public replies to these reviews (you approve before posting)",
        "In reviews ke liye shaant public replies draft kar doon (post karne se pehle aap approve karenge)",
    )
    body = " ".join(x for x in [lead, frame, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Emerging negative review theme with a verbatim quote (verifiable); balanced with the merchant's positive theme; loss-aversion + effort externalization.",
        lever="loss_aversion+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "review replies",
            "artifact": f"Draft reply: \"Thank you for the honest feedback — you're right, that wait isn't the experience we want. We've tightened our {F.theme(theme).replace('delivery late', 'delivery timings')} and would love to make it up to you on your next order. — Team {F.biz_name(c.m)}\"",
            "confirm_en": "post the replies",
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
    pos = F.review_theme(c.m, "pos")
    neg = F.review_theme(c.m, "neg")
    if name:
        c.cite("trigger.payload competitor")
        lead = f"{c.sal}, a new {c.cat.get('display_name', '').rstrip('s').lower() or 'competitor'} — {name} — opened {dist} km from you"
        lead += f" on {F.day_month(opened)}" if opened else ""
        lead += "."
        if their:
            my_same = F.offer_matching(mine, *[w for w in their.split(" @")[0].split() if len(w) > 3])
            lead += f" They're advertising {their}" + (
                f" (yours: {F.price_in(my_same)})." if my_same and F.price_in(my_same) else "."
            )
    else:
        lead = f"{c.sal}, a new competitor listing just showed up near {F.locality(c.m) or 'you'} on Google."
    judg = "I wouldn't price-match."
    moat = ""
    if pos and (pos.get("occurrences_30d") or 0) >= 3:
        q = pos.get("common_quote")
        moat_word = "trust" if c.slug in ("dentists", "pharmacies") else "what regulars already love"
        moat = (
            f"Your moat is {moat_word}: {pos['occurrences_30d']} reviews this month praise your {F.theme(pos['theme'])}"
            + (f' ("{q}")' if q else "")
            + "."
        )
        c.cite("merchant.review_themes (positive)")
    gap = ""
    if neg and (neg.get("occurrences_30d") or 0) >= 2:
        gap = f"The one gap they could exploit: {F.theme(neg['theme'])} ({neg['occurrences_30d']} reviews)."
        c.cite("merchant.review_themes (negative)")
    ask = yes_close(
        c,
        (
            "pin a patient quote + a 'book a slot, no waiting' line on your Google profile"
            if c.slug == "dentists"
            else "pin your best review quote + a sharper first line on your Google profile"
        ),
        "Aapke Google profile par best review quote + ek sharp first line pin kar doon",
    )
    body = " ".join(x for x in [lead, judg, moat, gap, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Competitor opening = loss-aversion hook; contrarian judgment (don't price-match) grounded in the merchant's own review moat and one exploitable gap.",
        lever="loss_aversion+judgment",
        next_action={
            "type": "deliver",
            "topic": "profile defense",
            "artifact": f"Profile update drafted: pinned review quote + first line \"{F.biz_name(c.m)} — {F.locality(c.m) or ''}: book a slot on WhatsApp, minimal waiting.\"",
            "confirm_en": "publish",
        },
    )
