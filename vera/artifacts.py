"""
Concrete deliverables Vera hands over the moment a merchant says YES.
Every artifact is built only from context fields (names, locality, live
offers, review quotes) — the merchant can paste it as-is.
"""

from __future__ import annotations

from . import facts as F
from .core import Ctx


def _offer_or_catalog(c: Ctx) -> str:
    live = F.active_offers(c.m)
    return live[0] if live else (F.catalog_offer(c.cat) or "")


def listing_first_line(c: Ctx) -> str:
    name, loc = F.biz_name(c.m), F.locality(c.m) or F.city(c.m) or ""
    pos = F.review_theme(c.m, "pos")
    offer = _offer_or_catalog(c)
    proof = f" Loved for {F.theme(pos['theme'])}." if pos else ""
    return (
        f"New first line for your Google description:\n\"{name}, {loc} —{proof} {offer + ' this week.' if offer else ''} "
        f'Book on WhatsApp in 1 minute."\nPhoto order: storefront → team at work → results/menu → interior.'
    ).replace("  ", " ")


def offer_setup(c: Ctx) -> str:
    o = F.catalog_offer(c.cat) or "your offer"
    return (
        f'Offer ready to publish: "{o}"\n• Shows on your Google listing + magicpin page\n• Runs 30 days, you can pause anytime\n'
        f"• Google post: \"{o} at {F.biz_name(c.m)}, {F.locality(c.m) or ''}. Message us to book.\""
    )


def three_posts(c: Ctx) -> str:
    name = F.biz_name(c.m)
    offer = _offer_or_catalog(c)
    pos = F.review_theme(c.m, "pos")
    trend = F.top_trend(c.cat)
    p1 = (
        f'1) Offer: "{offer} at {name} — this week only. Message us to book."'
        if offer
        else f'1) "Open today at {name} — message us to book."'
    )
    p2 = (
        f"2) Proof: \"What our {F.people(c.slug)} say: '{pos.get('common_quote')}'\""
        if pos and pos.get("common_quote")
        else f"2) Behind the scenes: a photo of your team at work with one line on what makes {name} different."
    )
    p3 = (
        f"3) Timely: \"{trend['query'].title()}? We've got you covered at {name}.\""
        if trend
        else "3) FAQ: answer the question customers ask most."
    )
    return "\n".join([p1, p2, p3])


def verification_steps(c: Ctx) -> str:
    return (
        "Verification request started. Google will call the listed number or send a postcard with a 5-digit code — "
        "just forward the code to me here and I'll finish it."
    )


def review_reply(c: Ctx, theme: str | None = None) -> str:
    t = F.theme(theme or (F.review_theme(c.m, "neg") or {}).get("theme") or "your experience")
    return (
        f"Draft reply: \"Thank you for the honest feedback about {t}. You're right — that's not the experience we want. "
        f"We've made changes this week and would love to welcome you back. — Team {F.biz_name(c.m)}\""
    )


def winback_message(c: Ctx) -> str:
    o = F.active_offers(c.m)
    return (
        f"Win-back message: \"Hi! It's been a while since your last visit to {F.biz_name(c.m)}. "
        + (f"This week: {o[0]}. " if o else "")
        + "Reply here and we'll hold a slot for you.\""
    )


def recovery_plan(c: Ctx) -> str:
    from .core import rank_hooks

    h = {hk.key for hk in rank_hooks(c)}
    steps = []
    if "no_offer" in h:
        steps.append(f"Put '{F.catalog_offer(c.cat)}' live today")
    if "unverified" in h:
        steps.append("Verify your Google listing (one call)")
    steps.append("Post on Google every week (I'll draft them)")
    steps.append("Reply to every new review within 24h")
    return "3-step recovery plan:\n" + "\n".join(f"{i+1}) {s}" for i, s in enumerate(steps[:3]))


BY_HOOK = {
    "ctr_below_peer": listing_first_line,
    "no_offer": offer_setup,
    "stale_posts": three_posts,
    "unverified": verification_steps,
    "neg_review": review_reply,
    "lapsed": winback_message,
    "dip_calls": recovery_plan,
    "dip_views": recovery_plan,
}


def for_hook(c: Ctx, key: str | None) -> str:
    fn = BY_HOOK.get(key or "", recovery_plan)
    try:
        return fn(c)
    except Exception:
        return recovery_plan(c)


HOOK_CONFIRM = {
    "unverified": "submit the verification request",
    "no_offer": "publish the offer",
    "stale_posts": "schedule the 3 posts",
    "neg_review": "post the replies",
    "lapsed": "send the win-back message",
    "ctr_below_peer": "update your listing",
    "dip_calls": "start step 1",
    "dip_views": "start step 1",
}

HOOK_TOPIC = {
    "ctr_below_peer": "listing conversion fix",
    "no_offer": "offer setup",
    "stale_posts": "3 Google posts",
    "unverified": "Google verification",
    "neg_review": "review replies",
    "lapsed": "win-back message",
    "dip_calls": "recovery plan",
    "dip_views": "recovery plan",
    "renewal": "renewal plan",
}
