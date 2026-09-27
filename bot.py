"""
Submission contract (challenge-brief §7.1):

    compose(category, merchant, trigger, customer=None) -> {body, cta, send_as, suppression_key, rationale}

Deterministic: same inputs -> same output. Typical latency < 5 ms.
"""

from __future__ import annotations

from vera import facts as F
from vera.composer import compose as _compose


def compose(
    category: dict, merchant: dict, trigger: dict, customer: dict | None = None, now: str | None = None
) -> dict:
    r = _compose(category, merchant, trigger, customer, now=F.parse_dt(now) if isinstance(now, str) else now)
    return {k: r[k] for k in ("body", "cta", "send_as", "suppression_key", "rationale")}
