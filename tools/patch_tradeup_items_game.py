#!/usr/bin/env python3
"""Patch CS:GO Legacy items_game.txt for CS2-style 5-Covert trade-ups.

This follows Valve's October 22, 2025 schema change instead of faking a
"next_rarity" on Covert items:
- recipes 5 / 15 are the five-Covert Unique / StatTrak contracts;
- rare-special prefabs expose craft_class "unusual";
- case item_sets get an "unusuals" mapping to their knife/glove pool;
- unusual loot-list definitions are copied into client_loot_lists so the legacy
  client can resolve those mappings.

The revival GC remains authoritative for consuming inputs and creating output.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import pathlib
import re
import sys
from typing import Iterator

MARKER = "REVIVAL_COVERT_TRADEUP_SCHEMA_V3"
OLD_MARKER = "REVIVAL_COVERT_TRADEUP_V1"


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
            value, new_pos = _parse_token(text, pos, end)
            yield KVEntry(key, "scalar", value, entry_start, new_pos)
            pos = new_pos


def _iter_block_entries(text: str, open_pos: int, close_pos: int) -> Iterator[KVEntry]:
    yield from _iter_entries_range(text, open_pos + 1, close_pos)


def _direct_blocks(text: str, open_pos: int, close_pos: int) -> dict[str, KVEntry]:
    return {
        e.key: e
        for e in _iter_block_entries(text, open_pos, close_pos)
        if e.kind == "block"
    }


def _external_unusual_blocks(text: str) -> dict[str, str]:
    start, end = 0, len(text)
    try:
        root_open, root_close = _find_named_block(text, "unusual_loot_lists")
    except ValueError:
        pass
    else:
        start, end = root_open + 1, root_close

    out: dict[str, str] = {}
    for entry in _iter_entries_range(text, start, end):
        if entry.kind == "block":
            out[entry.key] = text[entry.start:entry.end].strip()
    if not out:
        raise ValueError("unusual_loot_lists.txt contains no loot-list blocks")
    return out


def _leaf_keys(raw_block: str) -> set[str]:
    open_pos = raw_block.find("{")
    if open_pos < 0:
        return set()
    close_pos = _find_matching_brace(raw_block, open_pos)
    leaves: set[str] = set()

    def walk(a: int, b: int) -> None:
        for entry in _iter_entries_range(raw_block, a, b):
            if entry.kind == "block":
                walk(entry.open_pos + 1, entry.close_pos)
            else:
                leaves.add(entry.key)

    walk(open_pos + 1, close_pos)
    return leaves


def _is_painted_weapon_key(key: str) -> bool:
    return key.startswith("[") and "]weapon_" in key


def _is_knife_key(key: str) -> bool:
    return "weapon_knife" in key


def _recipe_block(recipe_id: int, quality: str, stattrak: bool) -> str:
    input_lines = [
        ('*rarity', 'ancient'),
        ('*quality', quality),
    ]
    if stattrak:
        input_lines.append(('*kill_eater_score_type', '0'))
    input_lines.append(('craft_class', 'weapon'))

    output_lines = [('*match_set_rarity', 'unusual')]
    if stattrak:
        output_lines.append(('*stattrak_recipe', 'yes'))
    output_lines.append(('craft_class', 'unusual'))

    def conditions(lines: list[tuple[str, str]], base_indent: str) -> str:
        chunks: list[str] = []
        for i, (field, value) in enumerate(lines):
            chunks.append(
                f'{base_indent}"{i}"\n'
                f'{base_indent}{{\n'
                f'{base_indent}\t"field"\t\t"{field}"\n'
                f'{base_indent}\t"operator"\t\t"string=="\n'
                f'{base_indent}\t"value"\t\t"{value}"\n'
                f'{base_indent}\t"required"\t\t"1"\n'
                f'{base_indent}}}\n'
            )
        return "".join(chunks)

    input_conditions = conditions(input_lines, "\t\t\t\t\t\t")
    output_conditions = conditions(output_lines, "\t\t\t\t\t\t")

    return f"""
\t\t// {MARKER}: Valve-style 5 Covert -> rare special contract
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
\t\t\t"filter"\t\t"-3"
\t\t}}
"""


def _remove_old_patch(text: str) -> tuple[str, bool]:
    changed = False

    rar_open, rar_close = _find_named_block(text, "rarities")
    ancient_open, ancient_close = _find_named_block(text, "ancient", rar_open + 1, rar_close)
    body = text[ancient_open + 1:ancient_close]
    cleaned = re.sub(
        r'(?m)^[ \t]*"next_rarity"[ \t]+"unusual"[ \t]*(?:\r?\n)?',
        "",
        body,
    )
    if cleaned != body:
        text = text[:ancient_open + 1] + cleaned + text[ancient_close:]
        changed = True

    recipes_open, recipes_close = _find_named_block(text, "recipes")
    removals: list[tuple[int, int]] = []
    for entry in _iter_block_entries(text, recipes_open, recipes_close):
        if entry.kind != "block" or entry.key not in {"900", "901"}:
            continue
        raw = text[entry.start:entry.end]
        if OLD_MARKER in raw or ('"di_A"' in raw and '"ancient"' in raw):
            removals.append((entry.start, entry.end))
    for start, end in reversed(removals):
        text = text[:start] + text[end:]
        changed = True

    return text, changed


def _ensure_prefab_craft_class(text: str, prefab_name: str) -> tuple[str, bool]:
    prefabs_open, prefabs_close = _find_named_block(text, "prefabs")
    block_open, block_close = _find_named_block(text, prefab_name, prefabs_open + 1, prefabs_close)
    body = text[block_open + 1:block_close]
    if re.search(r'(?m)^[ \t]*"craft_class"[ \t]+"unusual"', body):
        return text, False
    insertion = f'\n\t\t\t"craft_class"\t\t"unusual"\t// {MARKER}'
    return text[:block_open + 1] + insertion + text[block_open + 1:], True


def _collect_list_info(
    name: str,
    client_blocks: dict[str, str],
    unusual_blocks: dict[str, str],
    memo: dict[str, tuple[set[str], set[str]]],
    stack: set[str],
) -> tuple[set[str], set[str]]:
    if name in memo:
        a, b = memo[name]
        return set(a), set(b)
    if name in stack:
        return set(), set()

    stack.add(name)
    skins: set[str] = set()
    unusuals: set[str] = set()

    if name in unusual_blocks:
        unusuals.add(name)
        raw = unusual_blocks[name]
    else:
        raw = client_blocks.get(name, "")
        if not raw:
            stack.remove(name)
            return skins, unusuals

    open_pos = raw.find("{")
    if open_pos >= 0:
        close_pos = _find_matching_brace(raw, open_pos)
        for entry in _iter_entries_range(raw, open_pos + 1, close_pos):
            if entry.kind == "block":
                for key in _leaf_keys(raw[entry.start:entry.end]):
                    if _is_painted_weapon_key(key):
                        skins.add(key)
                continue

            key = entry.key
            if _is_painted_weapon_key(key):
                skins.add(key)
            elif key in client_blocks or key in unusual_blocks:
                sub_skins, sub_unusuals = _collect_list_info(
                    key, client_blocks, unusual_blocks, memo, stack
                )
                skins.update(sub_skins)
                unusuals.update(sub_unusuals)

    stack.remove(name)
    memo[name] = (set(skins), set(unusuals))
    return skins, unusuals


def _build_skin_pool_map(client_blocks: dict[str, str], unusual_blocks: dict[str, str]) -> dict[str, str]:
    memo: dict[str, tuple[set[str], set[str]]] = {}
    candidates: list[tuple[int, str, set[str]]] = []

    for name in client_blocks:
        skins, unusuals = _collect_list_info(name, client_blocks, unusual_blocks, memo, set())
        if skins and len(unusuals) == 1:
            candidates.append((len(skins), next(iter(unusuals)), skins))

    candidates.sort(key=lambda row: row[0])
    out: dict[str, str] = {}
    for _, pool, skins in candidates:
        for skin in skins:
            out.setdefault(skin, pool)
    return out


def _pool_has_knife(pool_name: str, unusual_blocks: dict[str, str]) -> bool:
    skins, _ = _collect_list_info(pool_name, {}, unusual_blocks, {}, set())
    return any(_is_knife_key(key) for key in skins)


def _client_loot_blocks(text: str) -> dict[str, str]:
    open_pos, close_pos = _find_named_block(text, "client_loot_lists")
    return {
        e.key: text[e.start:e.end]
        for e in _iter_block_entries(text, open_pos, close_pos)
        if e.kind == "block"
    }


def _inject_unusual_loot_lists(text: str, unusual_blocks: dict[str, str]) -> tuple[str, int]:
    loot_open, loot_close = _find_named_block(text, "client_loot_lists")
    existing = _direct_blocks(text, loot_open, loot_close)
    missing = [(name, raw) for name, raw in unusual_blocks.items() if name not in existing]
    if not missing:
        return text, 0

    chunks = [f"\n\t\t// {MARKER}: copied from csgo_gc/unusual_loot_lists.txt"]
    for _, raw in missing:
        chunks.append("\n".join("\t\t" + line.lstrip("\t") for line in raw.splitlines()))
    addition = "\n".join(chunks) + "\n"
    return text[:loot_close] + addition + text[loot_close:], len(missing)


def _patch_item_set_unusuals(
    text: str,
    skin_to_pool: dict[str, str],
    unusual_blocks: dict[str, str],
) -> tuple[str, int, list[str]]:
    item_sets_open, item_sets_close = _find_named_block(text, "item_sets")
    entries = list(_iter_block_entries(text, item_sets_open, item_sets_close))
    edits: list[tuple[int, str]] = []
    ambiguous: list[str] = []

    for set_entry in entries:
        if set_entry.kind != "block":
            continue

        raw = text[set_entry.start:set_entry.end]
        if re.search(r'(?m)^[ \t]*"unusuals"[ \t]*(?:\r?\n[ \t]*)?\{', raw):
            continue

        try:
            items_open, items_close = _find_named_block(raw, "items")
        except ValueError:
            continue

        pools = Counter()
        for item in _iter_block_entries(raw, items_open, items_close):
            if item.kind == "scalar":
                pool = skin_to_pool.get(item.key)
                if pool:
                    pools[pool] += 1

        if not pools:
            continue
        if len(pools) != 1:
            ambiguous.append(
                f'{set_entry.key}: ' + ", ".join(
                    f"{name} ({count})" for name, count in pools.most_common()
                )
            )
            continue

        pool = next(iter(pools))
        strange = ""
        if _pool_has_knife(pool, unusual_blocks):
            strange = f'\n\t\t\t\t"strange"\t\t"{pool}"'

        block = (
            f'\n\t\t\t// {MARKER}: case collection -> rare-special pool'
            f'\n\t\t\t"unusuals"'
            f'\n\t\t\t{{'
            f'\n\t\t\t\t"unique"\t\t"{pool}"'
            f'{strange}'
            f'\n\t\t\t}}'
        )
        edits.append((set_entry.close_pos, block))

    for pos, block in reversed(edits):
        text = text[:pos] + block + text[pos:]

    return text, len(edits), ambiguous


def _ensure_recipes(text: str) -> tuple[str, int]:
    recipes_open, recipes_close = _find_named_block(text, "recipes")
    existing = _direct_blocks(text, recipes_open, recipes_close)
    additions: list[str] = []
    if "5" not in existing:
        additions.append(_recipe_block(5, "unique", False))
    if "15" not in existing:
        additions.append(_recipe_block(15, "strange", True))
    if not additions:
        return text, 0
    return text[:recipes_close] + "".join(additions) + text[recipes_close:], len(additions)


def _validate(text: str) -> None:
    prefabs_open, prefabs_close = _find_named_block(text, "prefabs")
    for prefab in ("melee_unusual", "hands_paintable"):
        open_pos, close_pos = _find_named_block(text, prefab, prefabs_open + 1, prefabs_close)
        if not re.search(
            r'(?m)^[ \t]*"craft_class"[ \t]+"unusual"',
            text[open_pos + 1:close_pos],
        ):
            raise ValueError(f'{prefab} is missing craft_class "unusual"')

    recipes_open, recipes_close = _find_named_block(text, "recipes")
    recipes = _direct_blocks(text, recipes_open, recipes_close)
    for recipe_id in ("5", "15"):
        if recipe_id not in recipes:
            raise ValueError(f"missing Valve-style recipe {recipe_id}")
        raw = text[recipes[recipe_id].start:recipes[recipe_id].end]
        if '"di_A"' not in raw or '"ancient"' not in raw or '"craft_class"' not in raw:
            raise ValueError(f"recipe {recipe_id} is not the expected five-Covert recipe")

    rar_open, rar_close = _find_named_block(text, "rarities")
    ancient_open, ancient_close = _find_named_block(text, "ancient", rar_open + 1, rar_close)
    if re.search(
        r'(?m)^[ \t]*"next_rarity"[ \t]+"unusual"',
        text[ancient_open + 1:ancient_close],
    ):
        raise ValueError('obsolete ancient next_rarity "unusual" is still present')

    item_sets_open, item_sets_close = _find_named_block(text, "item_sets")
    if '"unusuals"' not in text[item_sets_open + 1:item_sets_close]:
        raise ValueError("no item_set unusuals mappings were installed")

    client_open, client_close = _find_named_block(text, "client_loot_lists")
    if MARKER not in text[client_open + 1:client_close]:
        raise ValueError("rare-special loot-list definitions were not installed")


def patch_text(text: str, unusual_text: str) -> tuple[str, bool, dict[str, object]]:
    changed = False
    stats: dict[str, object] = {}

    text, did = _remove_old_patch(text)
    changed |= did
    stats["removed_old_patch"] = did

    unusual_blocks = _external_unusual_blocks(unusual_text)
    client_blocks = _client_loot_blocks(text)
    skin_to_pool = _build_skin_pool_map(client_blocks, unusual_blocks)
    if not skin_to_pool:
        raise ValueError(
            "could not derive any case skin -> rare-special mappings from client_loot_lists"
        )
    stats["mapped_skins"] = len(skin_to_pool)

    for prefab in ("melee_unusual", "hands_paintable"):
        text, did = _ensure_prefab_craft_class(text, prefab)
        changed |= did

    text, set_count, ambiguous = _patch_item_set_unusuals(
        text, skin_to_pool, unusual_blocks
    )
    changed |= set_count > 0
    stats["mapped_item_sets"] = set_count
    stats["ambiguous_item_sets"] = ambiguous

    text, copied = _inject_unusual_loot_lists(text, unusual_blocks)
    changed |= copied > 0
    stats["copied_unusual_lists"] = copied

    text, recipes = _ensure_recipes(text)
    changed |= recipes > 0
    stats["added_recipes"] = recipes

    _validate(text)
    return text, changed, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument(
        "--unusual-loot-lists",
        help="csgo_gc/unusual_loot_lists.txt from the same legacy build",
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

    if not args.unusual_loot_lists:
        print(
            "[tradeup] ERROR: --unusual-loot-lists is required for a fresh patch",
            file=sys.stderr,
        )
        return 2

    unusual_path = pathlib.Path(args.unusual_loot_lists)
    if not unusual_path.is_file():
        print(f"[tradeup] missing unusual loot lists: {unusual_path}", file=sys.stderr)
        return 2

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
        f"mapped_skins={stats['mapped_skins']} "
        f"mapped_item_sets={stats['mapped_item_sets']} "
        f"copied_unusual_lists={stats['copied_unusual_lists']} "
        f"added_recipes={stats['added_recipes']}"
    )
    ambiguous = stats.get("ambiguous_item_sets", [])
    if ambiguous:
        print("[tradeup] skipped ambiguous item_sets:")
        for item in ambiguous:
            print(f"    {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
