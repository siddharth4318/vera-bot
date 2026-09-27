"""
Fact layer: every number / name / date that can appear in a Vera message is
read from the four contexts through these helpers. Nothing here invents data.
If a field is missing the helper returns None and the composer simply skips
that sentence — that is the core anti-hallucination rule of this bot.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

# --------------------------------------------------------------------------- #
# Safe access
# --------------------------------------------------------------------------- #


def g(d: Any, *path, default=None):
    """Nested get that never raises. g(m, 'identity', 'name')."""
    cur = d
    for p in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int):
            cur = cur[p] if -len(cur) <= p < len(cur) else None
        else:
            return default
    return default if cur is None else cur


def pick(seed: str, options: list):
    """Deterministic choice — same inputs always give the same variant."""
    if not options:
        return None
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


# --------------------------------------------------------------------------- #
# Number formatting (Indian conventions)
# --------------------------------------------------------------------------- #


def inr_group(n: int) -> str:
    """12400 -> '12,400', 145000 -> '1,45,000' (Indian digit grouping)."""
    neg = n < 0
    s = str(abs(int(n)))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts + [tail])
    return ("-" if neg else "") + out


def num(n: Any) -> str | None:
    if n is None:
        return None
    try:
        f = float(n)
    except (TypeError, ValueError):
        return None
    if abs(f - round(f)) < 1e-9:
        return inr_group(int(round(f)))
    return f"{f:.1f}"


def money(v: Any) -> str | None:
    s = num(v)
    return f"₹{s}" if s else None


def pct(x: Any, signed: bool = False) -> str | None:
    """0.38 -> '38%'; -0.5 -> '50%' (unsigned by default); 0.021 -> '2.1%'."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    v = f * 100
    val = abs(v) if not signed else v
    txt = f"{val:.0f}" if abs(val) >= 10 or abs(val - round(val)) < 0.05 else f"{val:.1f}"
    if signed and v > 0:
        txt = "+" + txt
    return txt + "%"


def ctr_str(x: Any) -> str | None:
    if x is None:
        return None
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_LONG = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_dt(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(t)
            else:
                dt = datetime.strptime(t[:10], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def day_month(s: Any) -> str | None:
    """'2026-11-12' -> '12 Nov'."""
    dt = parse_dt(s)
    return f"{dt.day} {MONTHS[dt.month - 1]}" if dt else None


def weekday_day_month(s: Any) -> str | None:
    dt = parse_dt(s)
    return f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]}" if dt else None


def days_between(a: Any, b: Any) -> int | None:
    da, db = (a if isinstance(a, datetime) else parse_dt(a)), (b if isinstance(b, datetime) else parse_dt(b))
    if not da or not db:
        return None
    return (db.date() - da.date()).days


def month_of(now: datetime | None) -> int | None:
    return now.month if now else None


def month_in_range(month: int, rng: str) -> bool:
    """'Nov-Feb' / 'Apr-Jun' / 'Jan' / 'Feb 14' style ranges."""
    if not rng:
        return False
    toks = re.findall(r"[A-Za-z]{3}", rng)
    idx = [MONTHS.index(t.capitalize()) + 1 for t in toks if t.capitalize() in MONTHS]
    if not idx:
        return False
    if len(idx) == 1:
        return month == idx[0]
    a, b = idx[0], idx[1]
    if a <= b:
        return a <= month <= b
    return month >= a or month <= b


# --------------------------------------------------------------------------- #
# Identity / naming
# --------------------------------------------------------------------------- #


def owner_first(m: dict) -> str | None:
    raw = g(m, "identity", "owner_first_name")
    if not raw:
        return None
    return re.sub(r"^(dr\.?\s*)", "", str(raw).strip(), flags=re.I).strip() or None


def salutation(cat_slug: str, m: dict) -> str:
    """Dr. Meera / Lakshmi / Ramesh ji — falls back to business name."""
    first = owner_first(m)
    biz = g(m, "identity", "name")
    if not first:
        return f"{biz} team" if biz else "Hi"
    if cat_slug == "dentists":
        return f"Dr. {first}"
    if cat_slug == "pharmacies":
        return f"{first} ji"
    return first


def biz_name(m: dict) -> str:
    return g(m, "identity", "name", default="your business")


def locality(m: dict) -> str | None:
    return g(m, "identity", "locality")


def city(m: dict) -> str | None:
    return g(m, "identity", "city")


def cust_names(c: dict | None) -> tuple[str | None, str | None]:
    """'Aanya (parent: Sneha)' -> ('Aanya', 'Sneha'). Returns (name, addressee)."""
    if not c:
        return None, None
    raw = str(g(c, "identity", "name", default="")).strip()
    if not raw or raw.startswith("("):
        return None, None
    mt = re.match(r"^(.*?)\s*\(parent:\s*([^)]+)\)", raw)
    if mt:
        return mt.group(1).strip(), mt.group(2).strip()
    return raw, raw


# --------------------------------------------------------------------------- #
# Language
# --------------------------------------------------------------------------- #


def merchant_lang(cat: dict, m: dict) -> str:
    """'hinglish' when the merchant lists Hindi and the category voice allows
    natural code-mix; 'en_light' for english_primary categories; else 'en'."""
    langs = [str(x).lower() for x in (g(m, "identity", "languages") or [])]
    mix = str(g(cat, "voice", "code_mix", default=""))
    if "hi" in langs:
        if "english_primary" in mix:
            return "en_light"
        return "hinglish"
    return "en"


REGIONAL_GREETING = {"te": "Namaskaram", "ta": "Vanakkam", "kn": "Namaskara", "mr": "Namaskar"}


def customer_lang(c: dict | None) -> str:
    """Returns one of: 'hi', 'hinglish', 'en', 'te', 'ta', 'kn', 'mr'."""
    pref = str(g(c, "identity", "language_pref", default="en")).lower()
    if pref in ("hi", "hindi"):
        return "hi"
    if "hi" in pref and "mix" in pref:
        return "hinglish"
    for code in ("te", "ta", "kn", "mr"):
        if pref.startswith(code):
            return code
    return "en"


# --------------------------------------------------------------------------- #
# Offers / catalog
# --------------------------------------------------------------------------- #


def active_offers(m: dict) -> list[str]:
    return [o.get("title") for o in (m.get("offers") or []) if o.get("status") == "active" and o.get("title")]


def expired_offers(m: dict) -> list[str]:
    return [
        o.get("title") for o in (m.get("offers") or []) if o.get("status") in ("expired", "paused") and o.get("title")
    ]


def offer_matching(titles: Iterable[str], *keywords: str) -> str | None:
    for t in titles:
        tl = t.lower()
        if any(k.lower() in tl for k in keywords):
            return t
    return None


def catalog_offer(
    cat: dict, prefer_types=("service_at_price",), keywords: Iterable[str] = (), audience: str | None = None
) -> str | None:
    """Best catalog template to *suggest* (never claimed as already live)."""
    cands = cat.get("offer_catalog") or []
    kws = [k.lower() for k in keywords]

    def ok_aud(o):
        return audience is None or o.get("audience") in (audience, "all")

    for o in cands:
        if kws and any(k in o.get("title", "").lower() for k in kws) and ok_aud(o):
            return o.get("title")
    for t in prefer_types:
        for o in cands:
            if o.get("type") == t and ok_aud(o):
                return o.get("title")
    return cands[0].get("title") if cands else None


def price_in(title: str | None) -> str | None:
    if not title:
        return None
    mt = re.search(r"₹\s?[\d,]+", title)
    return mt.group(0).replace(" ", "") if mt else None


# --------------------------------------------------------------------------- #
# Category knowledge
# --------------------------------------------------------------------------- #


def digest_item(cat: dict, item_id: str | None = None, kinds: Iterable[str] = ()) -> dict | None:
    items = cat.get("digest") or []
    if item_id:
        for it in items:
            if it.get("id") == item_id:
                return it
    kinds = list(kinds)
    if kinds:
        for k in kinds:
            for it in items:
                if it.get("kind") == k:
                    return it
    return None


def seasonal_beat(cat: dict, month: int | None) -> dict | None:
    if not month:
        return None
    for b in cat.get("seasonal_beats") or []:
        if month_in_range(month, b.get("month_range", "")):
            return b
    return None


def top_trend(cat: dict, keywords: Iterable[str] = ()) -> dict | None:
    ts = sorted(cat.get("trend_signals") or [], key=lambda t: -(t.get("delta_yoy") or 0))
    kws = [k.lower() for k in keywords]
    if kws:
        for t in ts:
            if any(k in t.get("query", "").lower() for k in kws):
                return t
    return ts[0] if ts else None


def content_item(cat: dict, keywords: Iterable[str] = ()) -> dict | None:
    items = cat.get("patient_content_library") or []
    kws = [k.lower() for k in keywords]
    for it in items:
        blob = (it.get("title", "") + " " + it.get("body", "")).lower()
        if kws and any(k in blob for k in kws):
            return it
    return items[0] if items else None


# --------------------------------------------------------------------------- #
# Merchant state
# --------------------------------------------------------------------------- #


def perf(m: dict, key: str):
    return g(m, "performance", key)


def delta(m: dict, key: str):
    return g(m, "performance", "delta_7d", key)


def signal_value(m: dict, prefix: str) -> str | None:
    """'stale_posts:22d' -> '22d'; returns '' when present with no value."""
    for s in m.get("signals") or []:
        s = str(s)
        if s == prefix:
            return ""
        if s.startswith(prefix + ":"):
            return s.split(":", 1)[1]
    return None


def has_signal(m: dict, *prefixes: str) -> bool:
    return any(signal_value(m, p) is not None for p in prefixes)


def review_theme(m: dict, sentiment: str | None = None, theme: str | None = None) -> dict | None:
    themes = m.get("review_themes") or []
    if theme:
        for t in themes:
            if t.get("theme") == theme:
                return t
    cands = [t for t in themes if sentiment is None or t.get("sentiment") == sentiment]
    cands.sort(key=lambda t: -(t.get("occurrences_30d") or 0))
    return cands[0] if cands else None


_DAYS_CAP = {d.lower(): d for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}


THEME_LABEL = {
    "wait_time": "wait times",
    "saturday_wait": "Saturday wait times",
    "weekend_busy": "weekend crowding",
    "delivery_late": "late deliveries",
    "morning_crowd": "morning crowding",
    "doctor_manner": "chairside manner",
    "stylist_skill": "stylist skill",
    "thali_quality": "thali",
    "pizza_quality": "pizza quality",
    "equipment_quality": "equipment",
    "instructor_quality": "instructors",
    "small_classes": "small class sizes",
    "delivery_speed": "delivery speed",
    "medicine_availability": "stock availability",
}


def theme(token: str | None) -> str:
    return THEME_LABEL.get(str(token or ""), humanize(token))


def humanize(token: str | None) -> str:
    """'high_risk_adults' -> 'high-risk adults'; 'skin_prep_program_30day' ->
    '30-day skin-prep program'; 'saturday_morning' -> 'Saturday morning'."""
    t = str(token or "").strip()
    mt = re.match(r"^(.*?)_(\d+)\s*day$", t)
    if mt:
        t = f"{mt.group(2)}-day_{mt.group(1)}"
    t = t.replace("_", " ")
    t = re.sub(r"\bhigh risk\b", "high-risk", t)
    t = re.sub(r"\bskin prep\b", "skin-prep", t)
    t = re.sub(r"\bweight loss\b", "weight-loss", t)
    t = re.sub(r"\b(" + "|".join(_DAYS_CAP) + r")\b", lambda m: _DAYS_CAP[m.group(1).lower()], t, flags=re.I)
    return t.strip()


def lapsed_count(m: dict) -> tuple[int | None, str | None]:
    ca = m.get("customer_aggregate") or {}
    for k, label in (("lapsed_180d_plus", "180+ days"), ("lapsed_90d_plus", "90+ days")):
        if ca.get(k) is not None:
            return ca[k], label
    return None, None


def last_merchant_turn(m: dict) -> dict | None:
    for t in reversed(m.get("conversation_history") or []):
        if t.get("from") == "merchant":
            return t
    return None


def last_vera_turn(m: dict) -> dict | None:
    for t in reversed(m.get("conversation_history") or []):
        if t.get("from") == "vera":
            return t
    return None


def open_loop(m: dict) -> str | None:
    """If the merchant's last message was an ask/commitment Vera still owes,
    return that message — the best continuity hook there is."""
    hist = m.get("conversation_history") or []
    if not hist:
        return None
    last = hist[-1]
    if last.get("from") == "merchant" and str(last.get("engagement", "")).startswith("intent"):
        return last.get("body")
    return None


def peer_gap_ctr(cat: dict, m: dict) -> tuple[str, str, float] | None:
    mine, peer = perf(m, "ctr"), g(cat, "peer_stats", "avg_ctr")
    if mine is None or not peer:
        return None
    return ctr_str(mine), ctr_str(peer), (mine - peer) / peer


def peer_scope(cat: dict) -> str:
    sc = str(g(cat, "peer_stats", "scope", default=""))
    sc = re.sub(r"_?20\d\d$", "", sc).replace("_", " ").strip()
    return sc or f"similar {cat.get('slug', 'businesses')}"


_ABBR = re.compile(r"\b(Dr|Mr|Mrs|Ms|St|No|vs|approx|Rs|p)\.$", re.I)


def sentences(text: str) -> list[str]:
    """Sentence split that does not break on 'Dr. R. Mehta' / 'p.14'."""
    if not text:
        return []
    out, buf = [], ""
    for tok in re.split(r"(?<=[.!?])\s+", text.strip()):
        buf = (buf + " " + tok).strip() if buf else tok
        last = buf.split()[-1] if buf.split() else ""
        if _ABBR.search(buf) or re.fullmatch(r"[A-Z]\.", last):
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def first_sentences(text: str, n: int = 1) -> str:
    ss = sentences(text)
    s = " ".join(ss[:n]).strip()
    return s if not s or s[-1] in ".!?" else s + "."


PEOPLE = {
    "dentists": "patients",
    "salons": "clients",
    "gyms": "members",
    "restaurants": "guests",
    "pharmacies": "customers",
}


def people(slug: str) -> str:
    return PEOPLE.get(slug, "customers")


def beat_phrase(beat: dict) -> str:
    """{'month_range':'Oct-Dec','note':'primary wedding/festival season — bridal package bookings 4x baseline'}
    -> 'Oct-Dec is the primary wedding/festival season (bridal package bookings 4x baseline)'"""
    note = str(beat.get("note", "")).strip()
    head, _, tail = note.partition(" — ")
    rng = beat.get("month_range", "")
    if tail:
        return f"{rng} is the {head} ({tail})"
    return f"{rng}: {head}"


def upcoming_beat(
    cat: dict, month: int | None, keywords=("festival", "wedding", "diwali", "holiday", "christmas")
) -> dict | None:
    beats = cat.get("seasonal_beats") or []
    kw = [b for b in beats if any(k in b.get("note", "").lower() for k in keywords)]
    if not kw:
        return None
    if not month:
        return kw[0]

    def start(b):
        toks = re.findall(r"[A-Za-z]{3}", b.get("month_range", ""))
        return MONTHS.index(toks[0].capitalize()) + 1 if toks and toks[0].capitalize() in MONTHS else 13

    kw.sort(key=lambda b: ((start(b) - month) % 12, b.get("month_range", "")))
    return kw[0]
