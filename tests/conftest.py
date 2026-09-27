import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vera.store import ContextStore  # noqa: E402


@pytest.fixture
def dentists():
    return {
        "slug": "dentists",
        "display_name": "Dentists",
        "voice": {"tone": "peer_clinical", "code_mix": "hindi_english_natural", "vocab_taboo": ["guaranteed"]},
        "offer_catalog": [{"title": "Dental Cleaning @ ₹299", "type": "service_at_price", "audience": "new_user"}],
        "peer_stats": {"avg_ctr": 0.03, "avg_review_count": 62, "scope": "metro_solo_practices_2026"},
        "digest": [
            {
                "id": "d_fluoride",
                "kind": "research",
                "title": "3-month fluoride recall beats 6-month",
                "source": "JIDA Oct 2026, p.14",
                "trial_n": 2100,
                "patient_segment": "high_risk_adults",
                "summary": "Multi-center trial shows 38% lower caries recurrence with 3-month recall.",
            },
            {
                "id": "d_recall",
                "kind": "alert",
                "title": "Voluntary recall: atorvastatin batches",
                "summary": "Sub-potency.",
            },
        ],
        "seasonal_beats": [],
        "trend_signals": [],
    }


@pytest.fixture
def clinic():
    return {
        "merchant_id": "m_test",
        "category_slug": "dentists",
        "identity": {
            "name": "Smile Dental",
            "locality": "Saket",
            "city": "Delhi",
            "languages": ["en", "hi"],
            "owner_first_name": "Asha",
            "verified": False,
        },
        "performance": {"views": 1000, "calls": 5, "ctr": 0.018, "delta_7d": {"calls_pct": -0.4, "views_pct": 0.02}},
        "offers": [],
        "customer_aggregate": {"high_risk_adult_count": 60, "total_unique_ytd": 400},
        "signals": [],
        "review_themes": [],
        "conversation_history": [],
    }


@pytest.fixture
def store(dentists, clinic):
    s = ContextStore()
    s.put("category", "dentists", 1, dentists)
    s.put("merchant", "m_test", 1, clinic)
    return s
