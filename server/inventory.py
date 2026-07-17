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


def _item_to_kv(item: dict[str, Any], position: int) -> dict[str, Any]:
    node: dict[str, Any] = {
        "inventory": item.get("inventory", position),
        "def_index": int(item["def_index"]),
        "level": item.get("level", DEFAULT_LEVEL),
        "quality": item.get("quality", DEFAULT_QUALITY),
        "flags": item.get("flags", 0),
        "origin": item.get("origin", DEFAULT_ORIGIN),
        "custom_name": item.get("custom_name", ""),
        "in_use": item.get("in_use", 0),
        "rarity": item.get("rarity", DEFAULT_RARITY),
    }

    # attributes: { "<attr_def_index>": "<value string>" }
    attributes = item.get("attributes") or {}
    node["attributes"] = {str(k): str(v) for k, v in attributes.items()}

    # equipped_state: { "<class_id>": "<slot_id>" }
    equipped = item.get("equipped_state") or {}
    node["equipped_state"] = {str(k): str(v) for k, v in equipped.items()}

    return node


def build_inventory_tree(player: dict[str, Any]) -> dict[str, Any]:
    items_node: dict[str, Any] = {}
    for index, item in enumerate(player.get("items", []), start=1):
        items_node[str(index)] = _item_to_kv(item, index)

    default_equips_node: dict[str, Any] = {}
    for eq in player.get("default_equips", []):
        default_equips_node[str(eq["item_definition"])] = {
            "class_id": eq.get("class_id", 0),
            "slot_id": eq.get("slot_id", 0),
        }

    return {"items": items_node, "default_equips": default_equips_node}


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
