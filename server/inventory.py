"""Build a csgo_gc-compatible ``inventory.txt`` for a player.

The format mirrors what csgo_gc's Inventory::ReadFromFile / WriteToFile expect
(see csgo_gc/inventory.cpp). Each item is keyed by a per-account "high item id"
(a simple 1-based counter here); csgo_gc composes the real 64-bit item id from
the account id + this number at load time.

Player records live in players.json as plain dicts. Only ``def_index`` is
required per item; everything else has sane defaults so granting a case is a
one-field operation.
"""

from __future__ import annotations

from typing import Any

import kvparser
import kvwriter

# Defaults chosen to match csgo_gc's ItemSchema constants.
DEFAULT_QUALITY = 4  # QualityUnique
DEFAULT_RARITY = 1   # RarityCommon
DEFAULT_LEVEL = 1
# ItemOriginCrate. IMPORTANT: origin 0 is NOT a valid ItemOrigin (valid values
# are 2=Purchased, 8=Crate, 22=BaseItem), and the CS:GO client silently refuses
# to render items that carry an unknown origin - so they never appear in the
# inventory. 8 is what every working inventory.txt in the wild uses.
DEFAULT_ORIGIN = 8

# csgo_gc ItemSchema::AttributeTexturePrefab - the paint-kit attribute that marks
# an item as a painted weapon skin. Used to detect skins for the trade-up quality
# normalization below.
PAINT_KIT_ATTRIBUTE = 6


def _item_to_kv(item: dict[str, Any], position: int) -> dict[str, Any]:
    # attributes: { "<attr_def_index>": "<value string>" }
    attributes = item.get("attributes") or {}
    attr_keys = {str(k) for k in attributes}

    # Trade-up eligibility fix: a painted weapon skin (one carrying the paint-kit
    # attribute, def index 6) at the "Normal" (0) quality is NOT recognised as a
    # trade-up input by the client - only Unique (normal) or Strange (StatTrak)
    # qualities are. Case-opened normal skins used to come through at quality
    # Normal, so they never appeared in the Trade Up Contract while StatTrak ones
    # (Strange) did. Promote such skins Normal->Unique here so existing stashes
    # become eligible without re-grinding. StatTrak (9) and knives/gloves
    # (Unusual, 3) already have a non-zero quality, so they are untouched.
    quality = _as_int(item.get("quality", DEFAULT_QUALITY), DEFAULT_QUALITY)
    if str(PAINT_KIT_ATTRIBUTE) in attr_keys and quality == 0:
        quality = DEFAULT_QUALITY  # QualityUnique

    node: dict[str, Any] = {
        "inventory": item.get("inventory", position),
        "def_index": int(item["def_index"]),
        "level": item.get("level", DEFAULT_LEVEL),
        "quality": quality,
        "flags": item.get("flags", 0),
        "origin": item.get("origin", DEFAULT_ORIGIN),
        "custom_name": item.get("custom_name", ""),
        "in_use": item.get("in_use", 0),
        "rarity": item.get("rarity", DEFAULT_RARITY),
    }

    node["attributes"] = {str(k): str(v) for k, v in attributes.items()}

    # equipped_state: { "<class_id>": "<slot_id>" }
    equipped = item.get("equipped_state") or {}
    node["equipped_state"] = {str(k): str(v) for k, v in equipped.items()}

    return node


def build_inventory_tree(player: dict[str, Any]) -> dict[str, Any]:
    items = player.get("items", [])

    # The KV key of each item IS its in-game 64-bit item id (csgo_gc composes it
    # from account id + this number). CS:GO caches each item's generated inventory
    # icon on disk keyed by that item id, so the id MUST stay STABLE for a given
    # item across syncs -- otherwise the client serves a stale/wrong cached icon
    # (correct name, wrong picture). Items carry a persistent `high_id` (assigned
    # by the store); use it as the key. Defensively fill in any missing/duplicate
    # id with a fresh one above the current max so ids never collide.
    max_id = 0
    for item in items:
        max_id = max(max_id, _as_int(item.get("high_id"), 0))

    items_node: dict[str, Any] = {}
    used: set[int] = set()
    for position, item in enumerate(items, start=1):
        high_id = _as_int(item.get("high_id"), 0)
        if high_id <= 0 or high_id in used:
            max_id += 1
            high_id = max_id
        used.add(high_id)
        items_node[str(high_id)] = _item_to_kv(item, position)

    default_equips_node: dict[str, Any] = {}
    for eq in player.get("default_equips", []):
        default_equips_node[str(eq["item_definition"])] = {
            "class_id": eq.get("class_id", 0),
            "slot_id": eq.get("slot_id", 0),
        }

    tree: dict[str, Any] = {"items": items_node, "default_equips": default_equips_node}

    # Preserve Operation Riptide account/quest state written by the patched GC.
    # Without this block the central sync server would regenerate inventory.txt
    # with only items/equips and wipe earned mission stars on the next launch.
    operation = player.get("operation_riptide")
    if isinstance(operation, dict):
        tree["operation_riptide"] = _normalize_operation_state(operation)

    return tree


def render_inventory_txt(player: dict[str, Any]) -> str:
    """Return the full text of a csgo_gc inventory.txt for one player.

    IMPORTANT: csgo_gc expects the "items" and "default_equips" blocks at the TOP
    level of the file, with NO outer "inventory" wrapper. csgo_gc reads the file
    with `KeyValue inventory{"inventory"}; inventory.ParseFromFile(...)` which
    loads the file's top-level keys directly as children - so wrapping them in an
    extra "inventory" block hides them and no items load.
    """
    tree = build_inventory_tree(player)
    return kvwriter.dumps_top(tree)


# ---------------------------------------------------------------------------
# Reverse direction: parse an inventory.txt (as written by csgo_gc after play)
# back into our stored item format. Used for two-way sync / persistence, so
# that opened cases, new skins, equips etc. survive across launches.
# ---------------------------------------------------------------------------

_INT_FIELDS = ("inventory", "level", "quality", "flags", "origin", "in_use", "rarity")
_OPERATION_INT_FIELDS = (
    "season", "earned_stars", "missions_completed", "mission_id", "season_pass_time"
)


def _normalize_operation_state(raw: Any) -> dict[str, Any]:
    """Return a safe JSON/KV representation of the Operation Riptide state."""
    if not isinstance(raw, dict):
        return {}

    out: dict[str, Any] = {}
    for field in _OPERATION_INT_FIELDS:
        if field in raw:
            out[field] = _as_int(raw.get(field), 0)

    quests_out: dict[str, Any] = {}
    quests = raw.get("quests")
    if isinstance(quests, dict):
        for quest_id, quest_raw in quests.items():
            qid = _as_int(quest_id, 0)
            if qid <= 0 or not isinstance(quest_raw, dict):
                continue
            quests_out[str(qid)] = {
                "progress": _as_int(quest_raw.get("progress"), 0),
                "bonus_points": _as_int(quest_raw.get("bonus_points"), 0),
            }
    out["quests"] = quests_out
    return out


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_inventory_txt(text: str) -> dict[str, Any]:
    """Parse a csgo_gc inventory.txt back into {items: [...], default_equips: [...]}.

    Item ids (the highItemId keys) are not preserved - they get reassigned when
    we regenerate the file. We keep every meaningful field (def_index, quality,
    rarity, origin, attributes, equipped_state, ...) so the round-trip is
    faithful and equips/opened items persist.
    """
    root = kvparser.parse(text)

    items: list[dict[str, Any]] = []
    items_block = root.get("items")
    if isinstance(items_block, dict):
        for _high_id, raw in items_block.items():
            if not isinstance(raw, dict):
                continue
            def_index = _as_int(raw.get("def_index"), 0)
            if not def_index:
                continue
            item: dict[str, Any] = {"def_index": def_index}
            # preserve the item id (the KV key) so it stays stable across syncs;
            # keeps the client's per-item icon cache from going stale
            high_id = _as_int(_high_id, 0)
            if high_id > 0:
                item["high_id"] = high_id
            for field in _INT_FIELDS:
                if field in raw:
                    item[field] = _as_int(raw[field])
            name = raw.get("custom_name")
            if isinstance(name, str) and name:
                item["custom_name"] = name
            attrs = raw.get("attributes")
            if isinstance(attrs, dict) and attrs:
                item["attributes"] = {str(k): str(v) for k, v in attrs.items()}
            equipped = raw.get("equipped_state")
            if isinstance(equipped, dict) and equipped:
                item["equipped_state"] = {str(k): str(v) for k, v in equipped.items()}
            items.append(item)

    default_equips: list[dict[str, Any]] = []
    de_block = root.get("default_equips")
    if isinstance(de_block, dict):
        for item_def, raw in de_block.items():
            if not isinstance(raw, dict):
                continue
            default_equips.append({
                "item_definition": _as_int(item_def),
                "class_id": _as_int(raw.get("class_id")),
                "slot_id": _as_int(raw.get("slot_id")),
            })

    operation_raw = root.get("operation_riptide")
    operation = (_normalize_operation_state(operation_raw)
                 if isinstance(operation_raw, dict) else None)

    return {
        "items": items,
        "default_equips": default_equips,
        "operation_riptide": operation,
    }
