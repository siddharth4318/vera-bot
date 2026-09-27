"""Generic reasoner for trigger kinds without a dedicated handler.

It renders whatever the payload actually carries, pairs it with the
top-ranked merchant hook and closes with one effort-free ask. When the
payload is empty it doesn't pretend otherwise: the message leads with the
merchant's own most pressing fact instead.
"""

from __future__ import annotations

from .. import artifacts as A
from .. import facts as F
from ..core import Ctx, Draft, best_hook, cap, yes_close

# hook key -> (what Vera offers to do, Hinglish version)
FIXES = {
    "unverified": ("start your Google verification (one call)", "Google verification shuru kar doon (bas ek call)"),
    "stale_posts": ("draft 3 fresh posts for you to approve", "3 fresh posts draft kar doon"),
    "ctr_below_peer": (
        "tighten your listing's first line + photo order",
        "listing ki pehli line aur photo order theek kar doon",
    ),
    "neg_review": ("draft replies to those reviews", "un reviews ke replies draft kar doon"),
    "lapsed": ("draft a win-back message for them", "unke liye win-back message draft kar doon"),
    "dip_calls": ("send a 3-step recovery plan", "3-step recovery plan bhej doon"),
    "dip_views": ("send a 3-step recovery plan", "3-step recovery plan bhej doon"),
    "spike_calls": (
        "post a Google update that turns the extra interest into bookings",
        "extra interest ko bookings mein badalne wala Google update post kar doon",
    ),
    "spike_views": (
        "post a Google update that turns the extra interest into bookings",
        "extra interest ko bookings mein badalne wala Google update post kar doon",
    ),
}


def _payload_facts(c: Ctx) -> str:
    """One readable line from whatever the trigger payload carries."""
    p = {k: v for k, v in c.payload.items() if k not in ("placeholder", "metric_or_topic")}
    ref = F.digest_item(c.cat, p.get("top_item_id") or p.get("digest_item_id") or p.get("item_id"))
    if ref:
        c.cite(f"category.digest[{ref.get('id')}]")
        return f"{ref.get('title').rstrip('.')} ({ref.get('source')})."

    facts = []
    for key, val in list(p.items())[:4]:
        if isinstance(val, bool) or not isinstance(val, (str, int, float)) or len(str(val)) >= 60:
            continue
        if isinstance(val, float) and abs(val) < 1.5 and "pct" in key:
            val = F.pct(val, signed=True)
        facts.append(f"{F.humanize(key)}: {val}")
    if not facts:
        return ""
    c.cite("trigger.payload")
    kind = F.humanize(c.trg.get("kind", "update"))
    return f"heads-up on {kind} — {'; '.join(facts)}."


def _fix_for(c: Ctx, key: str | None) -> tuple[str, str]:
    if key == "no_offer":
        sug = F.catalog_offer(c.cat)
        if sug:
            return (
                f"set up '{sug}' so visitors have something to act on",
                f"'{sug}' set kar doon taaki visitors ko action ka reason mile",
            )
    return FIXES.get(key or "", ("send a 1-step plan for this", "iske liye 1-step plan bhej doon"))


def generic(c: Ctx) -> Draft:
    why = _payload_facts(c)
    hook = best_hook(c)
    key = hook.key if hook else None

    if why:
        lead = f"{c.sal}, {why}"
        anchor = f"On your side, the thing that matters most right now: {hook.text}." if hook else ""
    elif hook:
        # nothing usable in the payload: lead with the merchant's own fact
        lead = f"{c.sal}, quick check on {F.biz_name(c.m)}: {hook.text}."
        anchor = ""
    else:
        lead = f"{c.sal}, a quick check-in from Vera."
        anchor = ""
    if hook:
        c.cite(hook.cite)

    en, hi = _fix_for(c, key)
    ask = yes_close(c, en, cap(hi))
    body = " ".join(x for x in (lead, anchor, ask) if x)
    kind = F.humanize(c.trg.get("kind", "update"))
    return Draft(
        body,
        "binary_yes_no",
        rationale=(
            f"Trigger '{kind}' carried little usable data, so only grounded payload facts were used, "
            f"paired with the top-ranked merchant hook ({key or 'none'})."
        ),
        lever="specificity+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": A.HOOK_TOPIC.get(key or "", kind),
            "artifact": A.for_hook(c, key),
            "confirm_en": A.HOOK_CONFIRM.get(key or "", "publish it"),
        },
    )
