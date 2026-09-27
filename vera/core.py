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


def rank_hooks(c: Ctx) -> list[Hook]:
    """Score every grounded merchant-state fact; highest = most worth saying.
    Scores blend severity (how far from peer / how big the swing) with
    fixability (can Vera act on it in one step)."""
    m, cat = c.m, c.cat
    hooks: list[Hook] = []
    name = F.biz_name(m)

    # 1. calls / views swing (7d)
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
                )
            )
        elif d >= 0.15:
            hooks.append(
                Hook(
                    f"spike_{label}",
                    4 + d * 10,
                    f"{label} are up {F.pct(d)} this week",
                    cite=f"merchant.performance.delta_7d.{key}={d}",
                )
            )

    # 2. CTR vs peers
    gap = F.peer_gap_ctr(cat, m)
    if gap and gap[2] <= -0.15:
        hooks.append(
            Hook(
                "ctr_below_peer",
                5 + abs(gap[2]) * 6,
                f"your listing converts {gap[0]} of views into actions vs {gap[1]} for {F.peer_scope(cat)}",
                cite="merchant.performance.ctr vs category.peer_stats.avg_ctr",
            )
        )
    elif gap and gap[2] >= 0.25:
        hooks.append(
            Hook(
                "ctr_above_peer",
                2.5,
                f"your CTR is {gap[0]} vs {gap[1]} for {F.peer_scope(cat)}",
                cite="merchant.performance.ctr vs peer",
            )
        )

    # 3. unverified GBP
    if F.g(m, "identity", "verified") is False:
        hooks.append(
            Hook("unverified", 6.5, f"{name} is still unverified on Google", cite="merchant.identity.verified=false")
        )

    # 4. no active offer
    if not F.active_offers(m):
        sug = F.catalog_offer(cat)
        hooks.append(
            Hook(
                "no_offer",
                5.5,
                "there is no active offer on your listing",
                fix=f"put '{sug}' live" if sug else "",
                cite="merchant.offers (none active)",
            )
        )

    # 5. stale posts
    sp = F.signal_value(m, "stale_posts")
    if sp is not None:
        days = sp.rstrip("d") if sp else None
        hooks.append(
            Hook(
                "stale_posts",
                4.5,
                f"your last Google post was {days} days ago" if days else "your Google posts have gone stale",
                cite="merchant.signals.stale_posts",
            )
        )

    # 6. lapsed customers
    n, label = F.lapsed_count(m)
    if n:
        hooks.append(
            Hook(
                "lapsed",
                4 + min(n, 200) / 100,
                f"{F.num(n)} customers haven't been back in {label}",
                cite="merchant.customer_aggregate.lapsed",
            )
        )

    # 7. negative review theme
    rt = F.review_theme(m, "neg")
    if rt and (rt.get("occurrences_30d") or 0) >= 2:
        hooks.append(
            Hook(
                "neg_review",
                4 + rt["occurrences_30d"] * 0.4,
                f"{rt['occurrences_30d']} reviews this month mention {F.theme(rt['theme'])}",
                cite="merchant.review_themes",
            )
        )

    # 8. positive review theme (proof / moat)
    rp = F.review_theme(m, "pos")
    if rp and (rp.get("occurrences_30d") or 0) >= 3:
        hooks.append(
            Hook(
                "pos_review",
                3 + rp["occurrences_30d"] * 0.1,
                f"{rp['occurrences_30d']} reviews this month praise your {F.theme(rp['theme'])}",
                cite="merchant.review_themes",
            )
        )

    # 9. renewal
    dr = F.g(m, "subscription", "days_remaining")
    if F.g(m, "subscription", "status") == "active" and dr is not None and dr <= 15:
        plan = F.g(m, "subscription", "plan", default="")
        hooks.append(
            Hook(
                "renewal",
                5,
                f"your {plan} plan renews in {dr} days".replace("  ", " "),
                cite="merchant.subscription.days_remaining",
            )
        )

    hooks.sort(key=lambda h: (-h.score, h.key))
    return hooks


def best_hook(c: Ctx, exclude: tuple = (), prefer: tuple = ()) -> Hook | None:
    hs = [h for h in rank_hooks(c) if h.key not in exclude]
    for p in prefer:
        for h in hs:
            if h.key == p:
                return h
    return hs[0] if hs else None


def hook_text(c: Ctx, h: Hook) -> str:
    c.cite(h.cite)
    return h.text


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
