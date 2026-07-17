#!/usr/bin/env python3
"""Generate catalog.json from CS:GO's items_game.txt.

A "case" in items_game is any item that carries the attribute
``set supply crate series``. The matching key is listed in the item's
``associated_items`` block. Both can be inherited from prefabs, so we resolve
the prefab chain (mirroring csgo_gc's ItemSchema::ParseItemRecursive).

Usage:
  python3 build_catalog.py \
      --items-game "/path/to/csgo/scripts/items/items_game.txt" \
      --out data/catalog.json
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Optional

import kvparser

HERE = os.path.dirname(os.path.abspath(__file__))


def _split_prefabs(prefab_str: str) -> list[str]:
    return [p for p in prefab_str.split(" ") if p]


def _resolve(item: dict, prefabs: dict, _seen: Optional[set] = None) -> dict:
    """Merge an item with everything it inherits from its prefab chain.

    Later (more specific) definitions win, so we start from the base prefabs and
    overlay the item itself last.
    """
    if _seen is None:
        _seen = set()

    merged: dict[str, Any] = {}
    prefab_str = item.get("prefab", "")
    if isinstance(prefab_str, str) and prefab_str:
        for name in _split_prefabs(prefab_str):
            if name in _seen:
                continue
            _seen.add(name)
            parent = prefabs.get(name)
            if isinstance(parent, dict):
                _merge_into(merged, _resolve(parent, prefabs, _seen))

    _merge_into(merged, item)
    return merged


def _merge_into(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            merged = dict(dst[key])
            _merge_into(merged, value)
            dst[key] = merged
        else:
            dst[key] = value


def _supply_crate_series(resolved: dict) -> Optional[int]:
    attributes = resolved.get("attributes")
    if not isinstance(attributes, dict):
        return None
    scs = attributes.get("set supply crate series")
    if isinstance(scs, dict):
        value = scs.get("value")
        if value is not None:
            try:
                return int(float(value))
            except ValueError:
                return None
    return None


def _first_associated_key(resolved: dict) -> Optional[int]:
    assoc = resolved.get("associated_items")
    if not isinstance(assoc, dict):
        return None
    for def_index in assoc.keys():
        try:
            return int(def_index)
        except ValueError:
            continue
    return None


RARITY_NAMES = {
    "default": 0, "common": 1, "uncommon": 2, "rare": 3, "mythical": 4,
    "legendary": 5, "ancient": 6, "immortal": 7,
}


def _slug(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "case"


def build_catalog(items_game_path: str) -> dict:
    root = kvparser.parse_file(items_game_path)
    items_game = root.get("items_game", root)
    items = items_game.get("items", {})
    prefabs = items_game.get("prefabs", {})

    if not isinstance(items, dict):
        raise SystemExit("could not find 'items' block in items_game.txt")

    cases: dict[str, Any] = {}
    keys: dict[str, Any] = {}
    used_slugs: set = set()

    for def_index, item in items.items():
        if def_index == "default" or not isinstance(item, dict):
            continue
        if not def_index.isdigit():
            continue

        resolved = _resolve(item, prefabs if isinstance(prefabs, dict) else {})
        series = _supply_crate_series(resolved)
        if series is None:
            continue  # not a crate

        key_def = _first_associated_key(resolved)
        name = resolved.get("name", f"crate_{def_index}")
        item_name = resolved.get("item_name", name)
        display_name = str(item_name).lstrip("#")

        rarity_name = str(resolved.get("item_rarity", "common")).lower()
        rarity = RARITY_NAMES.get(rarity_name, 1)

        slug = _slug(str(name))
        base_slug = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}_{n}"
            n += 1
        used_slugs.add(slug)

        cases[slug] = {
            "display_name": display_name,
            "def_index": int(def_index),
            "key_def_index": key_def,
            "series": series,
            "rarity": rarity,
        }

        if key_def:
            keys[str(key_def)] = {
                "display_name": f"Key for {display_name}",
                "def_index": key_def,
            }

    return {"cases": cases, "keys": keys, "items": {}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build catalog.json from items_game.txt")
    parser.add_argument("--items-game", required=True,
                        help="path to csgo/scripts/items/items_game.txt")
    parser.add_argument("--out", default=os.path.join(HERE, "data", "catalog.json"))
    args = parser.parse_args()

    catalog = build_catalog(args.items_game)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2)

    print(f"[build_catalog] wrote {args.out}")
    print(f"[build_catalog] {len(catalog['cases'])} cases, {len(catalog['keys'])} keys")
    example = list(catalog["cases"].items())[:5]
    for slug, case in example:
        print(f"  - {slug}: def_index={case['def_index']} key={case['key_def_index']} ({case['display_name']})")


if __name__ == "__main__":
    main()
