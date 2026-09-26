#!/usr/bin/env python3
"""Patch CS:GO Legacy items_game.txt for 5-Covert trade-ups.

The 2018 client only needs a valid trade-up recipe to allow Covert items into
the normal Trade Up Contract UI. The revival GC is authoritative for the gold
result, so the client schema must NOT be backported with modern CS2-only
item_set/loot-list metadata.

This patch:
- removes old revival recipes 900/901, even if they were accidentally nested;
- removes the previous V3 item_set / unusual-loot-list / prefab additions;
- installs recipe 5 (Unique) and recipe 15 (StatTrak) using the same legacy
  shape as stock recipes 4 and 14, but with di_A=5 and output rarity unusual.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import pathlib
import re
import sys
from typing import Iterator

MARKER = "REVIVAL_COVERT_TRADEUP_SCHEMA_V4_LEGACY"
OLD_MARKER_V3 = "REVIVAL_COVERT_TRADEUP_SCHEMA_V3"
OLD_MARKER_V1 = "REVIVAL_COVERT_TRADEUP_V1"


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


def _find_named_blocks(
    text: str,
    name: str,
    start: int = 0,
    end: int | None = None,
) -> list[tuple[int, int]]:
    if end is None:
        end = len(text)
    pat = re.compile(
        r'(?m)^[ \t]*"' + re.escape(name) + r'"[ \t]*(?:\r?\n[ \t]*)?\{'
    )
    out: list[tuple[int, int]] = []
    pos = start
    while True:
        match = pat.search(text, pos, end)
        if not match:
            break
        open_pos = text.find("{", match.start(), match.end())
        close_pos = _find_matching_brace(text, open_pos)
        if close_pos >= end:
            raise ValueError(f'KeyValues block "{name}" escapes parent block')
        out.append((open_pos, close_pos))
        pos = close_pos + 1
    return out


def _find_named_block(
    text: str,
    name: str,
    start: int = 0,
    end: int | None = None,
) -> tuple[int, int]:
    blocks = _find_named_blocks(text, name, start, end)
    if not blocks:
        raise ValueError(f'KeyValues block "{name}" not found')
    return blocks[0]


def _skip_space_comments(text: str, pos: int, end: int) -> int:
    while pos < end:
        if text[pos].isspace():
            pos += 1
            continue
        if text.startswith("//", pos):
            nl = text.find("\n", pos + 2, end)
            if nl < 0:
                return end
            pos = nl + 1
            continue
        break
    return pos


def _parse_token(text: str, pos: int, end: int) -> tuple[str, int]:
    pos = _skip_space_comments(text, pos, end)
    if pos >= end:
        raise ValueError("unexpected end of KeyValues text")

    if text[pos] == '"':
        i = pos + 1
        out: list[str] = []
        escaped = False
        while i < end:
            ch = text[i]
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                return "".join(out), i + 1
            else:
                out.append(ch)
            i += 1
        raise ValueError("unterminated quoted KeyValues token")

    i = pos
    while i < end and not text[i].isspace() and text[i] not in "{}":
        i += 1
    if i == pos:
        raise ValueError(f"expected KeyValues token at byte {pos}")
    return text[pos:i], i


@dataclass(frozen=True)
class KVEntry:
    key: str
    kind: str
    value: str
    start: int
    end: int
    open_pos: int = -1
    close_pos: int = -1


def _iter_entries_range(text: str, start: int, end: int) -> Iterator[KVEntry]:
    pos = start
    while True:
        pos = _skip_space_comments(text, pos, end)
        if pos >= end:
            return

        entry_start = pos
        key, pos = _parse_token(text, pos, end)
        pos = _skip_space_comments(text, pos, end)
        if pos >= end:
            raise ValueError(f'missing value for KeyValues key "{key}"')

        if text[pos] == "{":
            close = _find_matching_brace(text, pos)
            if close >= end:
                raise ValueError(f'KeyValues block "{key}" escapes parent range')
            yield KVEntry(key, "block", "", entry_start, close + 1, pos, close)
            pos = close + 1
        else:
            value, pos2 = _parse_token(text, pos, end)
            yield KVEntry(key, "scalar", value, entry_start, pos2)
            pos = pos2


def _iter_block_entries(text: str, open_pos: int, close_pos: int) -> Iterator[KVEntry]:
    yield from _iter_entries_range(text, open_pos + 1, close_pos)


def _direct_blocks(text: str, open_pos: int, close_pos: int) -> dict[str, KVEntry]:
    return {
        e.key: e
        for e in _iter_block_entries(text, open_pos, close_pos)
        if e.kind == "block"
    }


def _condition_block(lines: list[tuple[str, str]], indent: str) -> str:
    chunks: list[str] = []
    for i, (field, value) in enumerate(lines):
        chunks.append(
            f'{indent}"{i}"\n'
            f'{indent}{{\n'
            f'{indent}\t"field"\t\t"{field}"\n'
            f'{indent}\t"operator"\t\t"string=="\n'
            f'{indent}\t"value"\t\t"{value}"\n'
            f'{indent}\t"required"\t\t"1"\n'
            f'{indent}}}\n'
        )
    return "".join(chunks)


def _recipe_block(recipe_id: int, stattrak: bool) -> str:
    input_lines = [
        ("*rarity", "ancient"),
        ("*quality", "strange" if stattrak else "unique"),
    ]
    if stattrak:
        input_lines.append(("*kill_eater_score_type", "0"))
    # This exists in the stock Legacy recipes and is safe on the old client.
    input_lines.append(("craft_class", "weapon"))

    output_lines = [("*match_set_rarity", "unusual")]
    if stattrak:
        output_lines.append(("*stattrak_recipe", "yes"))

    input_conditions = _condition_block(input_lines, "\t\t\t\t\t\t")
    output_conditions = _condition_block(output_lines, "\t\t\t\t\t\t")
    requires_tool = '\n\t\t\t"requires_tool"\t\t"0"' if stattrak else ""

    return f"""
\t\t// {MARKER}: Legacy-native 5 Covert -> GC-resolved rare special
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
{input_conditions}\t\t\t\t\t}}
\t\t\t\t}}
\t\t\t}}
\t\t\t"output_items"
\t\t\t{{
\t\t\t\t"item1"
\t\t\t\t{{
\t\t\t\t\t"conditions"
\t\t\t\t\t{{
{output_conditions}\t\t\t\t\t}}
\t\t\t\t}}
\t\t\t}}
\t\t\t"category"\t\t"crafting"
\t\t\t"filter"\t\t"-3"{requires_tool}
\t\t}}
"""


def _remove_block_ranges(text: str, ranges: list[tuple[int, int]]) -> tuple[str, int]:
    if not ranges:
        return text, 0

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    for start, end in reversed(merged):
        text = text[:start] + text[end:]
    return text, len(merged)


def _remove_old_recipe_blocks(text: str) -> tuple[str, int]:
    recipes_open, recipes_close = _find_named_block(text, "recipes")
    removals: list[tuple[int, int]] = []

    # Remove all revival 5-Covert recipe generations. 900/901 were sometimes
    # accidentally nested inside recipe 4/14, so search the whole recipes range.
    for recipe_id in ("5", "15", "900", "901"):
        for open_pos, close_pos in _find_named_blocks(
            text, recipe_id, recipes_open + 1, recipes_close
        ):
            line_start = text.rfind("\n", recipes_open + 1, open_pos) + 1
            raw = text[line_start:close_pos + 1]

            is_revival = (
                MARKER in raw
                or OLD_MARKER_V3 in raw
                or OLD_MARKER_V1 in raw
                or (
                    '"di_A"' in raw
                    and re.search(r'"di_A"[ \t]+"5"', raw)
                    and '"*rarity"' in raw
                    and '"ancient"' in raw
                )
            )
            if is_revival:
                removals.append((line_start, close_pos + 1))

    return _remove_block_ranges(text, removals)


def _remove_v3_prefab_lines(text: str) -> tuple[str, int]:
    pat = re.compile(
        r'(?m)^[ \t]*"craft_class"[ \t]+"unusual"[^\r\n]*'
        + re.escape(OLD_MARKER_V3)
        + r'[^\r\n]*(?:\r?\n)?'
    )
    text2, count = pat.subn("", text)
    return text2, count


def _remove_v3_itemset_blocks(text: str) -> tuple[str, int]:
    marker = f"// {OLD_MARKER_V3}: case collection -> rare-special pool"
    removals: list[tuple[int, int]] = []
    pos = 0
    while True:
        marker_pos = text.find(marker, pos)
        if marker_pos < 0:
            break

        line_start = text.rfind("\n", 0, marker_pos) + 1
        unusual_match = re.search(
            r'(?m)^[ \t]*"unusuals"[ \t]*(?:\r?\n[ \t]*)?\{',
            text[marker_pos:],
        )
        if not unusual_match:
            pos = marker_pos + len(marker)
            continue

        abs_match_start = marker_pos + unusual_match.start()
        abs_match_end = marker_pos + unusual_match.end()
        open_pos = text.find("{", abs_match_start, abs_match_end)
        close_pos = _find_matching_brace(text, open_pos)
        removals.append((line_start, close_pos + 1))
        pos = close_pos + 1

    return _remove_block_ranges(text, removals)


def _remove_v3_copied_loot_lists(text: str) -> tuple[str, int]:
    marker = f"// {OLD_MARKER_V3}: copied from csgo_gc/unusual_loot_lists.txt"
    removals: list[tuple[int, int]] = []

    for open_pos, close_pos in _find_named_blocks(text, "client_loot_lists"):
        marker_pos = text.find(marker, open_pos + 1, close_pos)
        if marker_pos < 0:
            continue
        # V3 always inserted this payload at the end of the chosen section.
        line_start = text.rfind("\n", open_pos + 1, marker_pos) + 1
        removals.append((line_start, close_pos))

    return _remove_block_ranges(text, removals)


def _remove_obsolete_next_rarity(text: str) -> tuple[str, int]:
    rar_open, rar_close = _find_named_block(text, "rarities")
    ancient_open, ancient_close = _find_named_block(
        text, "ancient", rar_open + 1, rar_close
    )
    body = text[ancient_open + 1:ancient_close]
    cleaned, count = re.subn(
        r'(?m)^[ \t]*"next_rarity"[ \t]+"unusual"[^\r\n]*(?:\r?\n)?',
        "",
        body,
    )
    if count:
        text = text[:ancient_open + 1] + cleaned + text[ancient_close:]
    return text, count


def _ensure_recipes(text: str) -> tuple[str, int]:
    recipes_open, recipes_close = _find_named_block(text, "recipes")
    existing = _direct_blocks(text, recipes_open, recipes_close)

    additions: list[str] = []
    if "5" not in existing:
        additions.append(_recipe_block(5, False))
    if "15" not in existing:
        additions.append(_recipe_block(15, True))

    if not additions:
        return text, 0
    return text[:recipes_close] + "".join(additions) + text[recipes_close:], len(additions)


def _validate_recipe(raw: str, recipe_id: str, stattrak: bool) -> None:
    required = [
        '"di_A"\t\t"5"',
        '"*rarity"',
        '"ancient"',
        '"*quality"',
        '"strange"' if stattrak else '"unique"',
        '"craft_class"',
        '"weapon"',
        '"*match_set_rarity"',
        '"unusual"',
        '"filter"\t\t"-3"',
    ]
    for needle in required:
        if needle not in raw:
            raise ValueError(f"recipe {recipe_id} is missing expected field: {needle}")

    if '"craft_class"\t\t"unusual"' in raw:
        raise ValueError(f"recipe {recipe_id} still contains modern output craft_class unusual")


def _validate(text: str) -> None:
    recipes_open, recipes_close = _find_named_block(text, "recipes")
    recipes = _direct_blocks(text, recipes_open, recipes_close)

    for recipe_id, stattrak in (("5", False), ("15", True)):
        entry = recipes.get(recipe_id)
        if entry is None:
            raise ValueError(f"missing Legacy 5-Covert recipe {recipe_id}")
        raw = text[entry.start:entry.end]
        if MARKER not in raw:
            raise ValueError(f"recipe {recipe_id} is not the revival Legacy recipe")
        _validate_recipe(raw, recipe_id, stattrak)

    if OLD_MARKER_V3 in text:
        raise ValueError("obsolete V3 client-schema injection is still present")

    rar_open, rar_close = _find_named_block(text, "rarities")
    ancient_open, ancient_close = _find_named_block(
        text, "ancient", rar_open + 1, rar_close
    )
    if re.search(
        r'(?m)^[ \t]*"next_rarity"[ \t]+"unusual"',
        text[ancient_open + 1:ancient_close],
    ):
        raise ValueError('obsolete ancient next_rarity "unusual" is still present')

    # Old malformed 900/901 blocks must be gone even if they were nested.
    for recipe_id in ("900", "901"):
        if _find_named_blocks(text, recipe_id, recipes_open + 1, recipes_close):
            raise ValueError(f"obsolete revival recipe {recipe_id} is still present")


def patch_text(text: str, unusual_text: str = "") -> tuple[str, bool, dict[str, object]]:
    del unusual_text  # kept for backward-compatible callers; GC still uses that file.

    original = text
    stats: dict[str, object] = {}

    text, n = _remove_v3_copied_loot_lists(text)
    stats["removed_v3_loot_payloads"] = n

    text, n = _remove_v3_itemset_blocks(text)
    stats["removed_v3_itemsets"] = n

    text, n = _remove_v3_prefab_lines(text)
    stats["removed_v3_prefab_lines"] = n

    text, n = _remove_obsolete_next_rarity(text)
    stats["removed_next_rarity"] = n

    text, n = _remove_old_recipe_blocks(text)
    stats["removed_old_recipes"] = n

    text, n = _ensure_recipes(text)
    stats["added_recipes"] = n

    _validate(text)
    return text, text != original, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument(
        "--unusual-loot-lists",
        help="accepted for compatibility; rare-special resolution is GC-side",
    )
    ap.add_argument("--output")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    src = pathlib.Path(args.path)
    if not src.is_file():
        print(f"[tradeup] missing items_game: {src}", file=sys.stderr)
        return 2

    original = src.read_text(encoding="utf-8", errors="strict")

    if args.check:
        try:
            _validate(original)
        except ValueError as exc:
            print(f"[tradeup] ERROR: {exc}", file=sys.stderr)
            return 4
        print(f"[tradeup] {MARKER} validation passed")
        return 0

    unusual_text = ""
    if args.unusual_loot_lists:
        unusual_path = pathlib.Path(args.unusual_loot_lists)
        if unusual_path.is_file():
            unusual_text = unusual_path.read_text(encoding="utf-8", errors="strict")

    try:
        patched, changed, stats = patch_text(original, unusual_text)
    except ValueError as exc:
        print(f"[tradeup] ERROR: {exc}", file=sys.stderr)
        return 3

    dst = pathlib.Path(args.output) if args.output else src
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(patched, encoding="utf-8", newline="\n")

    print(f"[tradeup] {'patched' if changed else 'already current'}: {dst} ({MARKER})")
    print(
        "[tradeup] "
        f"removed_old_recipes={stats['removed_old_recipes']} "
        f"removed_v3_itemsets={stats['removed_v3_itemsets']} "
        f"removed_v3_loot_payloads={stats['removed_v3_loot_payloads']} "
        f"removed_v3_prefab_lines={stats['removed_v3_prefab_lines']} "
        f"added_recipes={stats['added_recipes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
