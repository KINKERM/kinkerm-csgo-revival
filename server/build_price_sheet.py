#!/usr/bin/env python3
"""Add case entries to a csgo_gc price_sheet.txt so cases can be redeemed live
in the in-game Store (free, since csgo_gc fakes the transaction).

This does NOT generate a price sheet from scratch - it takes your EXISTING,
known-working price_sheet.txt (the one already showing keys/capsules) and just
appends case entries, so nothing that already works gets broken.

IMPORTANT format facts (from a real dumped price_sheet.txt):
  - The file is wrapped in a top-level "store" block (unlike config/inventory,
    csgo_gc re-serializes this node, so the wrapper MUST stay).
  - Store entries are keyed by an item NAME (item_link), not a def_index.
  - Prices are per-currency in cents; csgo_gc bills nothing, so values are
    cosmetic. csgo_gc reports the client currency as EUR.

LIMITATION: the store can only sell whole items by def_index (cases, keys,
capsules, agents, tools) - NOT individual weapon skins (those need paint-kit
attributes the store can't apply). Skins still come from opening cases.

Usage (test with a few cases first!):
  python build_price_sheet.py \
      --price-sheet "<csgo>/csgo_gc/price_sheet.txt" \
      --items-game "<csgo>/csgo/scripts/items/items_game.txt" \
      --out price_sheet.txt --limit 5
"""

from __future__ import annotations

import argparse
import os

import build_catalog as bc  # reuse prefab resolution + crate detection
import kvparser
import kvwriter

# Symbolic prices (buying is free). EUR is what csgo_gc's client uses; include a
# broad set so a price shows in every region.
CASE_PRICES = {
    "USD": "1", "EUR": "1", "GBP": "1", "CAD": "1", "AUD": "1", "NZD": "1",
    "RUB": "100", "BRL": "5", "CNY": "10", "JPY": "100", "PLN": "5", "TRY": "5",
    "INR": "100", "UAH": "40", "MXN": "20", "ZAR": "20", "CHF": "1",
}


def _load_store(price_sheet_path: str) -> dict:
    root = kvparser.parse_file(price_sheet_path)
    store = root.get("store")
    if not isinstance(store, dict):
        raise SystemExit("could not find a 'store' block in the price sheet")
    store.setdefault("entries", {})
    store.setdefault("store_banner_layout", {})
    return store


def _find_cases(items_game_path: str):
    """Return list of (def_index, name) for openable crates (skips gift tools)."""
    root = kvparser.parse_file(items_game_path)
    items_game = root.get("items_game", root)
    items = items_game.get("items", {})
    prefabs = items_game.get("prefabs", {})
    if not isinstance(items, dict):
        raise SystemExit("could not find 'items' in items_game.txt")

    cases = []
    for def_index, item in items.items():
        if def_index == "default" or not isinstance(item, dict) or not def_index.isdigit():
            continue
        resolved = bc._resolve(item, prefabs if isinstance(prefabs, dict) else {})
        if bc._supply_crate_series(resolved) is None:
            continue
        name = resolved.get("name")
        if not isinstance(name, str) or not name:
            continue
        item_name = str(resolved.get("item_name", "")).lower()
        if "gift" in name.lower() or "tool_gift" in item_name:
            continue  # gift-wrap tools are not real cases
        cases.append((def_index, name))
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Add case entries to a csgo_gc price_sheet.txt")
    parser.add_argument("--price-sheet", required=True, help="path to your existing csgo_gc/price_sheet.txt")
    parser.add_argument("--items-game", required=True, help="path to csgo/scripts/items/items_game.txt")
    parser.add_argument("--out", default="price_sheet.txt", help="output path")
    parser.add_argument("--limit", type=int, default=0, help="only add the first N cases (0 = all). Use a small number to test first!")
    parser.add_argument("--category", default="Misc", help="store category tab to place cases under (must exist in the sheet's metadata)")
    args = parser.parse_args()

    store = _load_store(args.price_sheet)
    cases = _find_cases(args.items_game)
    if args.limit > 0:
        cases = cases[:args.limit]

    entries = store["entries"]
    banner = store["store_banner_layout"]

    added = 0
    for def_index, name in cases:
        if name in entries:
            continue  # already present, don't disturb it
        entries[name] = {
            "item_link": name,
            "category_tags": args.category,
            "prices": dict(CASE_PRICES),
        }
        banner[def_index] = {"custom_format": "single"}
        added += 1

    text = kvwriter.dumps("store", store)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)

    print(f"[build_price_sheet] added {added} case entries")
    print(f"[build_price_sheet] wrote {args.out}")
    if added:
        sample = [n for _, n in cases[:5]]
        print("[build_price_sheet] sample case item_links:", ", ".join(sample))
    print("[build_price_sheet] copy this over <csgo>/csgo_gc/price_sheet.txt and test in-game.")


if __name__ == "__main__":
    main()
