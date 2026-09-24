"""Player inventory storage + grant/revoke logic.

Players are keyed by their SteamID64. The store is a single JSON file guarded by
a lock so the HTTP server and admin CLI can both use it safely.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from catalog import Catalog


def _ensure_stable_high_ids(player: dict[str, Any]) -> bool:
    """Give every item a persistent, unique, never-reused ``high_id``.

    The ``high_id`` becomes the item's in-game 64-bit item id, which CS:GO uses as
    the cache key for each item's generated inventory icon. Reusing or reshuffling
    ids makes the client show stale/wrong icons, so ids must be stable per item and
    monotonically allocated (never handed out twice). ``high_id_seq`` tracks the
    high-water mark so a deleted item's id is never reused. Returns True if the
    player dict was modified.
    """
    items = player.get("items") or []
    seq = 0
    try:
        seq = int(player.get("high_id_seq", 0) or 0)
    except (TypeError, ValueError):
        seq = 0

    changed = False
    used: set[int] = set()

    # first pass: honor existing, unique ids and advance the high-water mark
    for item in items:
        try:
            hid = int(item.get("high_id", 0) or 0)
        except (TypeError, ValueError):
            hid = 0
        if hid > 0 and hid not in used:
            used.add(hid)
            if hid > seq:
                seq = hid
        elif "high_id" in item:
            # missing/zero/duplicate -> drop so it gets a fresh id below
            del item["high_id"]
            changed = True

    # second pass: allocate fresh ids for items that still lack one
    for item in items:
        try:
            hid = int(item.get("high_id", 0) or 0)
        except (TypeError, ValueError):
            hid = 0
        if hid <= 0:
            seq += 1
            item["high_id"] = seq
            used.add(seq)
            changed = True

    if int(player.get("high_id_seq", 0) or 0) != seq:
        player["high_id_seq"] = seq
        changed = True

    return changed


class PlayerStore:
    def __init__(self, players_path: str, catalog: Catalog):
        self._path = players_path
        self._catalog = catalog
        self._lock = threading.RLock()
        self._players: dict[str, Any] = {}
        self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8-sig") as fh:
                self._players = json.load(fh)
        else:
            self._players = {}

        # one-time migration: back-fill stable item ids for inventories saved
        # before stable ids existed (fixes wrong/stale inventory icons)
        migrated = [_ensure_stable_high_ids(p) for p in self._players.values()]
        if any(migrated):
            self._save()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._players, fh, indent=2)
        os.replace(tmp, self._path)

    # ---- helpers -----------------------------------------------------------
    def _player(self, steamid: str) -> dict[str, Any]:
        player = self._players.get(steamid)
        if player is None:
            player = {"items": [], "default_equips": []}
            self._players[steamid] = player
        player.setdefault("items", [])
        player.setdefault("default_equips", [])
        return player

    # ---- queries -----------------------------------------------------------
    def get_player(self, steamid: str) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._player(steamid)))  # deep copy

    def list_players(self) -> list[str]:
        with self._lock:
            return sorted(self._players.keys())

    # ---- mutations ---------------------------------------------------------
    def grant_case(self, steamid: str, case_id: str, count: int = 1,
                   include_key: bool = True) -> dict[str, Any]:
        """Add `count` copies of a case (and, by default, matching keys).

        Bundling the matching key means the case opens with the normal CS:GO
        flow. Because csgo_gc never validates keys, the key is purely there to
        satisfy the client UI.
        """
        case = self._catalog.get_case(case_id)
        if case is None:
            raise KeyError(f"unknown case '{case_id}'")

        with self._lock:
            player = self._player(steamid)
            added = {"cases": 0, "keys": 0}
            for _ in range(count):
                player["items"].append({
                    "def_index": int(case["def_index"]),
                    "quality": case.get("quality", 4),
                    "rarity": case.get("rarity", 1),
                })
                added["cases"] += 1

                key_def = case.get("key_def_index")
                if include_key and key_def:
                    player["items"].append({
                        "def_index": int(key_def),
                        "quality": 4,
                        "rarity": 1,
                    })
                    added["keys"] += 1
            _ensure_stable_high_ids(player)
            self._save()
            return added

    def grant_item(self, steamid: str, def_index: int,
                   quality: Optional[int] = None, rarity: Optional[int] = None,
                   attributes: Optional[dict] = None, count: int = 1) -> int:
        with self._lock:
            player = self._player(steamid)
            for _ in range(count):
                item: dict[str, Any] = {"def_index": int(def_index)}
                if quality is not None:
                    item["quality"] = int(quality)
                if rarity is not None:
                    item["rarity"] = int(rarity)
                if attributes:
                    item["attributes"] = {str(k): str(v) for k, v in attributes.items()}
                player["items"].append(item)
            _ensure_stable_high_ids(player)
            self._save()
            return count

    def revoke_def_index(self, steamid: str, def_index: int, count: Optional[int] = None) -> int:
        """Remove items with a given def_index. count=None removes all of them."""
        with self._lock:
            player = self._player(steamid)
            removed = 0
            kept: list = []
            for item in player["items"]:
                if int(item.get("def_index", -1)) == int(def_index) and (count is None or removed < count):
                    removed += 1
                    continue
                kept.append(item)
            player["items"] = kept
            self._save()
            return removed

    def clear(self, steamid: str) -> None:
        with self._lock:
            player = self._player(steamid)
            player["items"] = []
            player["default_equips"] = []
            self._save()

    def replace_inventory(self, steamid: str, items: list, default_equips: list,
                          operation_riptide: Optional[dict] = None,
                          revival_profile: Optional[dict] = None) -> int:
        """Replace a player's entire inventory with an uploaded snapshot.

        Used by two-way sync: after a session, the launcher uploads the player's
        local inventory.txt (which csgo_gc rewrote with opened cases, new skins,
        equips, etc.) so those changes persist on the server.
        """
        with self._lock:
            player = self._player(steamid)
            player["items"] = list(items or [])
            player["default_equips"] = list(default_equips or [])
            # Newer patched clients persist Operation Riptide quest/account state
            # in the same inventory.txt. Only replace it when the upload actually
            # contains the block, so older clients cannot erase newer progress.
            if operation_riptide is not None:
                player["operation_riptide"] = dict(operation_riptide)
            if revival_profile is not None:
                player["revival_profile"] = dict(revival_profile)
            # keep ids stable: honor ids the game/we already assigned, hand out
            # fresh unique ids for the rest, never reuse a retired id
            _ensure_stable_high_ids(player)
            self._save()
            return len(player["items"])
