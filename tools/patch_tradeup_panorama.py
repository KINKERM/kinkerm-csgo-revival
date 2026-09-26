#!/usr/bin/env python3
"""Patch an unpacked CS:GO Legacy Panorama bundle for 5-Covert trade-ups.

Run after tools/pbin.py unpack from <CSGO>/csgo/panorama:
    py -3 patch_tradeup_panorama.py <CSGO>/csgo/panorama

The legacy client filters the Trade Up inventory through the native "recipe"
capability before a tile can be clicked. That filter predates CS2's 5-Covert
contract. We broaden the visible equipment list, then gate clicks in JS:
normal legacy-eligible items still use CanTradeUp(), while rarity 6 weapon
skins (Covert) are sent directly to AddCraftIngredient(). The actual recipe
and the GC remain authoritative.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

MARKER = "REVIVAL_COVERT_TRADEUP_UI_V2"


def _read(path: pathlib.Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return raw.decode("utf-8"), len(raw)


def _fit_and_write(path: pathlib.Path, text: str, max_bytes: int) -> None:
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        # PBIN entries have fixed slots. Valve's dumped Panorama sources contain
        # large whitespace-only remnants from stripped comments, so reclaim only
        # semantically inert horizontal whitespace before considering failure.
        text = re.sub(r"(?m)^[ \t]+(?=\r?$)", "", text)
        text = re.sub(r"[ \t]+(?=\r?\n)", "", text)
        raw = text.encode("utf-8")

    if len(raw) > max_bytes:
        raise ValueError(
            f"{path}: patched file is {len(raw)} B but PBIN slot is {max_bytes} B"
        )

    path.write_bytes(raw)


def _patch_crafting(path: pathlib.Path) -> bool:
    text, size = _read(path)
    changed = False

    # Only alter the left-hand inventory filter for the Trade Up panel.
    anchor = text.find("#Crafting-Items")
    if anchor < 0:
        raise ValueError(f"{path}: Crafting-Items anchor not found")

    recipe = text.find("'recipe'", anchor, anchor + 700)
    if recipe >= 0:
        text = text[:recipe] + "''" + text[recipe + len("'recipe'"):]
        changed = True
    else:
        # Idempotency: accept a previously broadened filter.
        dispatch_end = text.find(");", anchor, anchor + 900)
        window = text[anchor: dispatch_end if dispatch_end >= 0 else anchor + 900]
        if "sortType" not in window:
            raise ValueError(f"{path}: trade-up filter shape was not recognized")

    _fit_and_write(path, text, size)
    return changed


def _patch_itemtile(path: pathlib.Path) -> bool:
    text, size = _read(path)
    if MARKER in text:
        return False

    fn = text.find("var _OnActivate = function()")
    if fn < 0:
        raise ValueError(f"{path}: _OnActivate not found")

    m = re.search(
        r"var\s+id\s*=\s*\$\.GetContextPanel\(\)\.GetAttributeString\(\s*'itemid'\s*,\s*'0'\s*\s*\)\s*;",
        text[fn: fn + 900],
    )
    if not m:
        raise ValueError(f"{path}: item id line in _OnActivate not found")

    insert_at = fn + m.end()
    nl = "\r\n" if "\r\n" in text[fn: fn + 900] else "\n"
    code = (
        nl + "\t\t/* " + MARKER + " */" + nl
        + "\t\tvar rp=$.GetContextPanel(),rc=rp;" + nl
        + "\t\twhile(rc&&rc.id!=='Crafting-Items')rc=rc.GetParent();" + nl
        + "\t\tif(rc){" + nl
        + "\t\t\tvar rs=ItemInfo.GetSlotSubPosition(id);" + nl
        + "\t\t\tif(rs&&rs!=='melee'&&rs!=='c4'&&rs!=='clothing_hands'&&!ItemInfo.IsEquippalbleButNotAWeapon(id)&&(InventoryAPI.CanTradeUp(id)||(InventoryAPI.GetItemRarity(id)===6&&ItemInfo.IsWeapon(id))))InventoryAPI.AddCraftIngredient(id);" + nl
        + "\t\t\treturn;" + nl
        + "\t\t}" + nl
    )

    text = text[:insert_at] + code + text[insert_at:]
    _fit_and_write(path, text, size)
    return True


def _patch_context(path: pathlib.Path) -> bool:
    text, size = _read(path)
    if MARKER in text:
        return False

    start = text.find("name: 'tradeup_add'")
    if start < 0:
        raise ValueError(f"{path}: tradeup_add entry not found")

    old = "( InventoryAPI.CanTradeUp( id ) || InventoryAPI.GetNumItemsNeededToTradeUp( id ) > 0 )"
    pos = text.find(old, start, start + 1400)
    if pos < 0:
        # This is only a redundant context-menu path; itemtile.js is the primary
        # path. Still fail if the expected legacy shape changed so we don't claim
        # a complete patch against an unknown build.
        raise ValueError(f"{path}: tradeup_add eligibility expression not found")

    new = (
        "( InventoryAPI.CanTradeUp( id ) || InventoryAPI.GetNumItemsNeededToTradeUp( id ) > 0 "
        "|| ( InventoryAPI.GetItemRarity( id ) === 6 && ItemInfo.IsWeapon( id ) ) ) "
        "/* " + MARKER + " */"
    )
    text = text[:pos] + new + text[pos + len(old):]
    _fit_and_write(path, text, size)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("panorama_root", help="CSGO/csgo/panorama directory")
    args = ap.parse_args()

    root = pathlib.Path(args.panorama_root)
    targets = {
        "crafting": root / "panorama" / "scripts" / "crafting.js",
        "itemtile": root / "panorama" / "scripts" / "itemtile.js",
        "context": root / "panorama" / "scripts" / "common" / "item_context_entries.js",
    }
    for name, path in targets.items():
        if not path.is_file():
            print(f"[tradeup-ui] missing {name}: {path}", file=sys.stderr)
            return 2

    try:
        a = _patch_crafting(targets["crafting"])
        b = _patch_itemtile(targets["itemtile"])
        c = _patch_context(targets["context"])
    except (UnicodeDecodeError, ValueError) as exc:
        print(f"[tradeup-ui] ERROR: {exc}", file=sys.stderr)
        return 3

    itemtile = targets["itemtile"].read_text(encoding="utf-8")
    context = targets["context"].read_text(encoding="utf-8")
    if MARKER not in itemtile or MARKER not in context:
        print("[tradeup-ui] ERROR: marker validation failed", file=sys.stderr)
        return 4

    print(
        "[tradeup-ui] "
        + ("patched" if (a or b or c) else "already current")
        + f": {MARKER}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
