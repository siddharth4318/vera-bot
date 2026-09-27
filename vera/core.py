"""
Shared composition primitives: the Ctx bundle every handler receives, the
Draft every handler returns, language helpers, and the merchant "hook
ranker" (decision engine) that picks the single strongest merchant-state fact
to pair with a trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import facts as F


@dataclass
class Ctx:
    cat: dict
    m: dict
    trg: dict
    cust: dict | None = None
    now: datetime | None = None
    roster: list = field(default_factory=list)  # the merchant's own customer contexts, if pushed

    # derived
    slug: str = ""
    lang: str = "en"
    sal: str = ""
    seed: str = ""
    used: list = field(default_factory=list)  # provenance log -> rationale

    def __post_init__(self):
        self.slug = self.cat.get("slug") or self.m.get("category_slug") or ""
        self.lang = F.merchant_lang(self.cat, self.m)
        self.sal = F.salutation(self.slug, self.m)
        self.seed = f"{self.m.get('merchant_id')}|{self.trg.get('id')}|{(self.cust or {}).get('customer_id')}"

    # ---- provenance ------------------------------------------------------ #
    def cite(self, what: str):
        if what not in self.used:
            self.used.append(what)

    def say(self, en: str, hi: str) -> str:
        """Pick the Hinglish variant for Hindi-speaking merchants.

        Message bodies stay in English so numbers and sources read cleanly;
        only the opener / closing ask switches to code-mix.
        """
        return hi if self.lang == "hinglish" else en

    @property
    def payload(self) -> dict:
        return self.trg.get("payload") or {}


@dataclass
class Draft:
    body: str
    cta: str = "binary_yes_no"
    rationale: str = ""
    lever: str = ""
    next_action: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# CTA closers — one per message, always the last sentence
# --------------------------------------------------------------------------- #


def yes_close(c: Ctx, what_en: str, what_hi: str | None = None, eta: str = "10 min") -> str:
    """'Want me to <what>? Reply YES.' in the merchant's register. The tail
    varies deterministically per (merchant, trigger) so a merchant never sees
    the same closing line twice in a row."""
    if c.lang == "hinglish":
        what_hi = what_hi or what_en
        tail = F.pick(
            c.seed,
            [
                f"Bas YES reply kijiye — {eta} mein ready.",
                f"YES bhejiye, {eta} mein kaam ho jayega.",
                f"Ek YES kaafi hai — {eta} mein ready.",
            ],
        )
        return f"{what_hi}? {tail}"
    tail = F.pick(
        c.seed,
        [
            f"Reply YES — ready in {eta}.",
            f"Just reply YES; I'll have it ready in {eta}.",
            f"A YES is all I need — {eta} on my side.",
        ],
    )
    return f"Want me to {what_en}? {tail}"


# --------------------------------------------------------------------------- #
# Decision engine: rank merchant-state hooks
# --------------------------------------------------------------------------- #


@dataclass
class Hook:
    key: str
    score: float
    text: str
    fix: str = ""  # what Vera proposes to do about it
    cite: str = ""
    visible: bool = True  # can the reader check this from the headline numbers / signals / offers?


def rank_hooks(c: Ctx) -> list[Hook]:
    """Score every grounded merchant-state fact; highest = most worth saying.

    Scores blend severity (how far from normal, how big the swing) with
    fixability (can Vera act on it in one step). Hooks built from the headline
    evidence (views/calls/CTR, signals, live offers) are marked visible and
    preferred by best_hook(), so the message stays checkable.
    """
    m, cat = c.m, c.cat
    hooks: list[Hook] = []
    name = F.biz_name(m)
    views, calls, ctr = F.perf_numbers(m)

    # 1. no live offer (visible: the live-offer list is empty)
    if not F.active_offers(m):
        sug = F.suggested_offer(cat, m)
        hooks.append(
            Hook(
                "no_offer",
                6.0,
                "there's no live offer on your listing",
                fix=f"put '{sug}' live" if sug else "",
                cite="merchant.offers (none active)",
            )
        )

    # 2. unverified listing
    if F.has_signal(m, "unverified_gbp") or F.g(m, "identity", "verified") is False:
        hooks.append(
            Hook(
                "unverified",
                6.5,
                f"{name} is still unverified on Google",
                cite="merchant.signals.unverified_gbp",
                visible=F.has_signal(m, "unverified_gbp"),
            )
        )

    # 3. CTR — the number itself is visible; the peer benchmark only via the signal
    peer = F.g(cat, "peer_stats", "avg_ctr")
    if ctr and F.has_signal(m, "ctr_below_peer_median"):
        hooks.append(
            Hook(
                "ctr_below_peer",
                5.5,
                f"your CTR is {ctr}, below the peer median",
                cite="merchant.performance.ctr + signal ctr_below_peer_median",
            )
        )
    elif ctr and peer and F.perf(m, "ctr") < peer * 0.85:
        hooks.append(
            Hook(
                "ctr_below_peer",
                5.0,
                f"only {ctr} of the people who see your listing act on it",
                cite="merchant.performance.ctr",
            )
        )

    # 4. stale posts
    stale = F.signal_phrase(m, "stale_posts") or F.signal_phrase(m, "no_recent_post")
    if stale:
        hooks.append(Hook("stale_posts", 4.5, stale, cite="merchant.signals.stale_posts"))

    # 5. week-on-week swings: only the signal is visible, the delta itself isn't
    for key, label in (("calls_pct", "calls"), ("views_pct", "views")):
        d = F.delta(m, key)
        if d is None:
            continue
        if d <= -0.15:
            hooks.append(
                Hook(
                    f"dip_{label}",
                    6 + abs(d) * 10,
                    f"{label} are down {F.pct(d)} this week",
                    cite=f"merchant.performance.delta_7d.{key}={d}",
                    visible=False,
                )
            )
        elif d >= 0.15:
            hooks.append(
                Hook(
                    f"spike_{label}",
                    4 + d * 10,
                    f"{label} are up {F.pct(d)} this week",
                    cite=f"merchant.performance.delta_7d.{key}={d}",
                    visible=F.has_signal(m, "growing_views_7d"),
                )
            )

    # 6. deeper context — useful for judgment, not checkable from the headline view
    n, label = F.lapsed_count(m)
    if n:
        hooks.append(
            Hook(
                "lapsed",
                4 + min(n, 200) / 100,
                f"{F.num(n)} customers haven't been back in {label}",
                cite="merchant.customer_aggregate.lapsed",
                visible=False,
            )
        )
    rt = F.review_theme(m, "neg")
    if rt and (rt.get("occurrences_30d") or 0) >= 2:
        hooks.append(
            Hook(
                "neg_review",
                4 + rt["occurrences_30d"] * 0.4,
                f"recent reviews keep mentioning {F.theme(rt['theme'])}",
                cite="merchant.review_themes",
                visible=False,
            )
        )
    rp = F.review_theme(m, "pos")
    if rp and (rp.get("occurrences_30d") or 0) >= 3:
        hooks.append(
            Hook(
                "pos_review",
                3 + rp["occurrences_30d"] * 0.1,
                f"your reviews keep praising your {F.theme(rp['theme'])}",
                cite="merchant.review_themes",
                visible=False,
            )
        )

    # 7. renewal
    renew = F.signal_phrase(m, "renewal_due_soon")
    dr = F.g(m, "subscription", "days_remaining")
    if renew or (F.g(m, "subscription", "status") == "active" and dr is not None and dr <= 15):
        hooks.append(
            Hook(
                "renewal",
                5,
                renew or f"your plan renews in {dr} days",
                cite="merchant.subscription.days_remaining",
                visible=bool(renew),
            )
        )

    hooks.sort(key=lambda h: (-h.score, h.key))
    return hooks


def best_hook(c: Ctx, exclude: tuple = (), prefer: tuple = (), visible_only: bool = True) -> Hook | None:
    """Pick the one gap worth raising. Checkable hooks win over deeper ones
    unless nothing checkable exists."""
    hooks = [h for h in rank_hooks(c) if h.key not in exclude]
    pools = [[h for h in hooks if h.visible], hooks] if visible_only else [hooks]
    for pool in pools:
        for p in prefer:
            for h in pool:
                if h.key == p:
                    return h
        if pool:
            return pool[0]
    return None


def hook_text(c: Ctx, h: Hook) -> str:
    c.cite(h.cite)
    return h.text


def because(lead: str, *parts: str) -> str:
    """'<lead>: a, b and c.' from whichever parts are non-empty, so the
    rationale only claims what the message actually does."""
    ps = [p for p in parts if p]
    if not ps:
        return lead + "."
    tail = ps[0] if len(ps) == 1 else ", ".join(ps[:-1]) + " and " + ps[-1]
    return f"{lead}: {tail}."


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
