#!/usr/bin/env python3
"""Patch CS:GO Legacy items_game.txt for CS2-style 5-Covert trade-ups.

This intentionally changes only the client recipe metadata:
- Covert/ancient gains next_rarity=unusual so the native CanTradeUp gate opens.
- Two filter -3 recipes are added: 5 Unique Coverts and 5 Strange Coverts.

The revival GC remains authoritative for the actual knife/glove result.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

MARKER = "REVIVAL_COVERT_TRADEUP_V1"


def _find_matching_brace(text: str, open_pos: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    line_comment = False
    i = open_pos
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if line_comment:
            if ch in "\r\n":
                line_comment = False
            i += 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    raise ValueError("unmatched KeyValues brace")


def _find_named_block(text: str, name: str, start: int = 0, end: int | None = None) -> tuple[int, int]:
    if end is None:
        end = len(text)
    pat = re.compile(r'(?m)^[ \t]*"' + re.escape(name) + r'"[ \t]*(?:\r?\n[ \t]*)?\{')
    match = pat.search(text, start, end)
    if not match:
        raise ValueError(f'KeyValues block "{name}" not found')
    open_pos = text.find("{", match.start(), match.end())
    close_pos = _find_matching_brace(text, open_pos)
    if close_pos >= end:
        raise ValueError(f'KeyValues block "{name}" escapes parent block')
    return open_pos, close_pos


def _recipe_block(recipe_id: int, quality: str) -> str:
    # IDs 900/901 are revival-private and still fit the uint16 recipe field.
    return f'''
\t\t// {MARKER}: CS2-style 5 Covert -> rare special contract
\t\t"{recipe_id}"
\t\t{{
\t\t\t"name"\t\t"#RT_MP_A"
\t\t\t"n_A"\t\t"#RI_R6p"
\t\t\t"desc_inputs"\t\t"#RDI_AB"
\t\t\t"desc_outputs"\t\t"#RDO_AB"
\t\t\t"di_A"\t\t"5"
\t\t\t"di_B"\t\t"#RI_R6p"
\t\t\t"do_A"\t\t"1"
\t\t\t"do_B"\t\t"#RI_R7"
\t\t\t"all_same_class"\t\t"0"
\t\t\t"always_known"\t\t"1"
\t\t\t"premium_only"\t\t"0"
\t\t\t"disabled"\t\t"0"
\t\t\t"input_items"
\t\t\t{{
\t\t\t\t"5"
\t\t\t\t{{
\t\t\t\t\t"conditions"
\t\t\t\t\t{{
\t\t\t\t\t\t"0"
\t\t\t\t\t\t{{
\t\t\t\t\t\t\t"field"\t\t"*rarity"
\t\t\t\t\t\t\t"operator"\t\t"string=="
\t\t\t\t\t\t\t"value"\t\t"ancient"
\t\t\t\t\t\t\t"required"\t\t"1"
\t\t\t\t\t\t}}
\t\t\t\t\t\t"1"
\t\t\t\t\t\t{{
\t\t\t\t\t\t\t"field"\t\t"*quality"
\t\t\t\t\t\t\t"operator"\t\t"string=="
\t\t\t\t\t\t\t"value"\t\t"{quality}"
\t\t\t\t\t\t\t"required"\t\t"1"
\t\t\t\t\t\t}}
\t\t\t\t\t}}
\t\t\t\t}}
\t\t\t}}
\t\t\t"output_items"
\t\t\t{{
\t\t\t\t"item1"
\t\t\t\t{{
\t\t\t\t\t"conditions"
\t\t\t\t\t{{
\t\t\t\t\t\t"0"
\t\t\t\t\t\t{{
\t\t\t\t\t\t\t"field"\t\t"*match_set_rarity"
\t\t\t\t\t\t\t"operator"\t\t"string=="
\t\t\t\t\t\t\t"value"\t\t"unusual"
\t\t\t\t\t\t\t"required"\t\t"1"
\t\t\t\t\t\t}}
\t\t\t\t\t}}
\t\t\t\t}}
\t\t\t}}
\t\t\t"category"\t\t"crafting"
\t\t\t"filter"\t\t"-3"
\t\t\t"requires_tool"\t\t"0"
\t\t}}
'''


def patch_text(text: str) -> tuple[str, bool]:
    changed = False

    # Open the native Covert CanTradeUp path by giving Ancient a real successor.
    rar_open, rar_close = _find_named_block(text, "rarities")
    ancient_open, ancient_close = _find_named_block(text, "ancient", rar_open + 1, rar_close)
    ancient = text[ancient_open + 1:ancient_close]
    if not re.search(r'(?m)^[ \t]*"next_rarity"[ \t]+"unusual"[ \t]*$', ancient):
        insert = '\n\t\t\t"next_rarity"\t\t"unusual"'
        text = text[:ancient_close] + insert + text[ancient_close:]
        changed = True

    # Re-find after the insertion changed offsets.
    recipes_open, recipes_close = _find_named_block(text, "recipes")
    recipes = text[recipes_open + 1:recipes_close]
    if MARKER not in recipes:
        addition = _recipe_block(900, "unique") + _recipe_block(901, "strange")
        text = text[:recipes_close] + addition + text[recipes_close:]
        changed = True

    return text, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--output")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    src = pathlib.Path(args.path)
    if not src.is_file():
        print(f"[tradeup] missing items_game: {src}", file=sys.stderr)
        return 2

    original = src.read_text(encoding="utf-8", errors="strict")
    try:
        patched, changed = patch_text(original)
    except ValueError as exc:
        print(f"[tradeup] ERROR: {exc}", file=sys.stderr)
        return 3

    if MARKER not in patched or '"next_rarity"\t\t"unusual"' not in patched:
        print("[tradeup] ERROR: covert recipe patch did not validate", file=sys.stderr)
        return 4

    if args.check:
        print("[tradeup] REVIVAL_COVERT_TRADEUP_V1 present")
        return 0

    dst = pathlib.Path(args.output) if args.output else src
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(patched, encoding="utf-8", newline="\n")
    print(
        f"[tradeup] {'patched' if changed else 'already current'}: "
        f"{dst} ({MARKER})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
