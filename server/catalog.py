"""Case/item catalog.

catalog.json is normally generated from CS:GO's own items_game.txt by
build_catalog.py, but a hand-written catalog works just as well. Shape:

{
  "cases": {
    "<case_id>": {
      "display_name": "Chroma 3 Case",
      "def_index": 4001,
      "key_def_index": 5001,      # matching key; may be null if unknown
      "series": 1,                # supply crate series (informational)
      "rarity": 1
    }, ...
  },
  "keys": { "<key_id>": {"display_name": "...", "def_index": 5001}, ... },
  "items": { "<item_id>": {"display_name": "...", "def_index": 7, "rarity": 3}, ... }
}
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


class Catalog:
    def __init__(self, data: dict[str, Any]):
        self.cases: dict[str, Any] = data.get("cases", {})
        self.keys: dict[str, Any] = data.get("keys", {})
        self.items: dict[str, Any] = data.get("items", {})

    @classmethod
    def load(cls, path: str) -> "Catalog":
        if not os.path.exists(path):
            return cls({})
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def get_case(self, case_id: str) -> Optional[dict[str, Any]]:
        return self.cases.get(case_id)

    def get_item(self, item_id: str) -> Optional[dict[str, Any]]:
        return self.items.get(item_id)

    def case_ids(self) -> list[str]:
        return sorted(self.cases.keys())

    def item_ids(self) -> list[str]:
        return sorted(self.items.keys())
