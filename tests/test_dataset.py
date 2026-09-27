"""Sweep every trigger in the expanded challenge dataset (skipped if it isn't generated)."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vera.composer import compose

ROOT = Path(os.getenv("VERA_DATASET", "expanded"))
pytestmark = pytest.mark.skipif(not (ROOT / "triggers").is_dir(), reason="expanded dataset not generated")


def cases():
    from scripts.dataset import Dataset

    data = Dataset(ROOT)
    for trigger in data.all("triggers"):
        yield pytest.param(data, trigger, id=trigger["id"])


@pytest.mark.parametrize(
    "now", [None, datetime(2026, 4, 26, tzinfo=timezone.utc), datetime(2026, 9, 27, tzinfo=timezone.utc)]
)
@pytest.mark.parametrize(("data", "trigger"), list(cases()) if (ROOT / "triggers").is_dir() else [])
def test_every_trigger_composes_cleanly(data, trigger, now):
    category, merchant, customer = data.contexts_for(trigger)
    out = compose(category, merchant, trigger, customer, now=now)
    body = out["body"]
    assert body and "handler_error" not in out["rationale"]
    assert not re.search(r"\bNone\b|[{}]|\[\]|  ", body), body
    assert body.count("YES") <= 2
    for taboo in category["voice"]["vocab_taboo"]:
        assert re.sub(r"\s*\(.*?\)", "", taboo).lower() not in body.lower()
