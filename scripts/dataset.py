"""Helpers for loading the expanded challenge dataset.

Generate it once from the challenge pack:

    python dataset/generate_dataset.py --seed-dir dataset --out expanded

and point VERA_DATASET at the `expanded/` folder (defaults to ./expanded).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def dataset_dir(override: str | None = None) -> Path:
    path = Path(override or os.getenv("VERA_DATASET", "expanded"))
    if not (path / "categories").is_dir():
        raise SystemExit(f"Dataset not found at '{path}'. Run generate_dataset.py or set VERA_DATASET.")
    return path


def read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Dataset:
    def __init__(self, root: Path):
        self.root = root
        self.categories = {p.stem: read_json(p) for p in (root / "categories").glob("*.json")}

    def _load(self, folder: str, item_id: str | None) -> dict | None:
        if not item_id:
            return None
        return read_json(self.root / folder / f"{item_id}.json")

    def merchant(self, merchant_id: str) -> dict:
        return self._load("merchants", merchant_id)

    def customer(self, customer_id: str | None) -> dict | None:
        return self._load("customers", customer_id)

    def trigger(self, trigger_id: str) -> dict:
        return self._load("triggers", trigger_id)

    def all(self, folder: str) -> list[dict]:
        return [read_json(p) for p in sorted((self.root / folder).glob("*.json"))]

    def test_pairs(self) -> list[dict]:
        return read_json(self.root / "test_pairs.json")["pairs"]

    def roster(self, merchant_id: str) -> list[dict]:
        """Every customer context that belongs to this merchant."""
        if not hasattr(self, "_customers"):
            self._customers = self.all("customers")
        return [c for c in self._customers if c.get("merchant_id") == merchant_id]

    def contexts_for(self, trigger: dict) -> tuple[dict, dict, dict | None]:
        merchant = self.merchant(trigger["merchant_id"])
        return self.categories[merchant["category_slug"]], merchant, self.customer(trigger.get("customer_id"))
