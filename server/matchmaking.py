"""Single-server drop-in CS:GO Revival matchmaking coordinator.

The revival exposes one ranked Competitive queue backed by one Windows srcds.
The first queued human starts a match immediately. Empty player slots are filled
by bots on the game server; later humans who queue are attached to the same live
match (up to 10 humans) and replace bots as they connect.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any


DEFAULT_MAP_POOL = (
    "de_dust2", "de_mirage", "de_inferno", "de_nuke", "de_overpass",
    "de_vertigo", "de_train", "de_cache", "de_cbble", "de_ancient",
    "de_anubis", "de_tuscan", "de_canals", "de_breach", "de_basalt",
    "de_abbey", "de_austria", "de_biome", "de_blackgold", "de_chlorine",
    "de_engage", "de_grind", "de_lite", "de_mocha", "de_mutiny",
    "de_ruby", "de_seaside", "de_shipped", "de_studio", "de_subzero",
    "de_swamp", "de_thrill", "de_zoo",
    "cs_office", "cs_agency", "cs_italy", "cs_insertion", "cs_insertion2",
)

MAX_HUMANS = 10
SERVER_STALE_SECONDS = 12.0
ALLOCATE_TIMEOUT_SECONDS = 90.0


def account_id_from_steamid64(steamid: str) -> int:
    try:
        return int(steamid) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return 0


@dataclass
class QueueEntry:
    steamid: str
    account_id: int
    game_type: int = 8
    client_version: int = 0
    joined_at: float = field(default_factory=time.time)


@dataclass
class Match:
    match_id: int
    reservation_id: int
    players: list[QueueEntry]
    map_name: str
    created_at: float = field(default_factory=time.time)
    state: str = "allocating"  # allocating -> reserved -> in_match -> complete
    server_address: str = ""


class MatchmakingCoordinator:
    def __init__(self, map_pool: list[str] | tuple[str, ...] | None = None):
        self._lock = threading.RLock()
        self._queue: list[QueueEntry] = []
        self._states: dict[str, dict[str, Any]] = {}
        self._matches: dict[int, Match] = {}
        self._next_match_id = max(int(time.time() * 1000), 1)
        self._map_pool = tuple(map_pool or DEFAULT_MAP_POOL)

        self._server: dict[str, Any] = {
            "agent_id": "",
            "public_host": "",
            "public_port": 27015,
            "server_version": 0,
            "server_id": 0,
            "last_seen": 0.0,
            "ready_match_id": 0,
            "reserved_account_ids": [],
            "maps": [],
        }
        self._assignment: dict[str, Any] | None = None

    def _server_online_locked(self) -> bool:
        return (
            bool(self._server.get("agent_id"))
            and time.time() - float(self._server.get("last_seen", 0.0))
            <= SERVER_STALE_SECONDS
        )

    def _active_match_locked(self) -> Match | None:
        for match in self._matches.values():
            if match.state in ("allocating", "reserved", "in_match"):
                return match
        return None

    def _server_idle_locked(self) -> bool:
        return (
            self._server_online_locked()
            and self._assignment is None
            and self._active_match_locked() is None
        )

    def _server_joinable_locked(self) -> bool:
        if not self._server_online_locked():
            return False
        match = self._active_match_locked()
        if match is None:
            return True
        return len(match.players) < MAX_HUMANS

    def _waiting_ids_locked(self) -> list[int]:
        return [q.account_id for q in self._queue]

    def _match_account_ids(self, match: Match) -> list[int]:
        return [p.account_id for p in match.players]

    def _publish_search_states_locked(self) -> None:
        waiting = self._waiting_ids_locked()
        for q in self._queue:
            self._states[q.steamid] = {
                "state": "searching",
                "waiting_account_ids": waiting,
                "players_searching": len(waiting),
                # One human is enough to boot a server. Ten is only the human cap.
                "players_required": 1,
                "server_online": self._server_online_locked(),
                "server_available": self._server_joinable_locked(),
            }

    def _state_for_match_player_locked(self, match: Match, player: QueueEntry) -> dict[str, Any]:
        # Keep Panorama in its normal "searching" presentation while srcds is
        # booting. Exposing the internal "allocating" phase makes legacy CS:GO
        # display "Matchmaking unavailable, retrying..." even though the laptop
        # is online and actively starting the server.
        acknowledged = {
            int(x) for x in self._server.get("reserved_account_ids", [])
            if str(x).isdigit()
        }
        native_ready_for_player = (
            match.reservation_id > 0
            and player.account_id in acknowledged
        )
        client_state = (
            "searching"
            if match.state == "allocating" or not native_ready_for_player
            else match.state
        )
        state: dict[str, Any] = {
            "state": client_state,
            "match_id": match.match_id,
            "reservation_id": match.reservation_id,
            "map": match.map_name,
            "account_ids": self._match_account_ids(match),
            "server_version": int(self._server.get("server_version") or 0),
            "server_id": int(self._server.get("server_id") or 0),
            "server_online": self._server_online_locked(),
            "server_available": len(match.players) < MAX_HUMANS,
            # Preserve the exact queue bitfield sent by this client. Legacy
            # Competitive is commonly 0x02000008, not plain 8.
            "game_type": int(match.players[0].game_type if match.players else 8),
        }
        if client_state == "searching":
            ids = self._match_account_ids(match)
            state.update({
                "waiting_account_ids": ids,
                "players_searching": len(ids),
                "players_required": 1,
            })
        if match.server_address:
            host = str(self._server.get("public_host") or "")
            port = int(self._server.get("public_port") or 27015)
            state.update({
                "server_address": match.server_address,
                "public_host": host,
                "public_port": port,
            })
        return state

    def _refresh_match_player_states_locked(self, match: Match) -> None:
        for player in match.players:
            current = self._states.get(player.steamid, {})
            # Do not resurrect someone who explicitly cancelled/abandoned.
            if current.get("state") == "idle" and current.get("previous_state"):
                continue
            self._states[player.steamid] = self._state_for_match_player_locked(
                match, player
            )

    def _choose_map_locked(self) -> str:
        available = [
            str(x) for x in self._server.get("maps", [])
            if str(x).startswith(("de_", "cs_"))
        ]
        if available:
            return random.choice(available)
        return random.choice(self._map_pool)

    def _sync_assignment_locked(self, match: Match) -> None:
        if self._assignment is None:
            return
        if int(self._assignment.get("match_id") or 0) != match.match_id:
            return
        self._assignment["reservation_id"] = match.reservation_id
        self._assignment["account_ids"] = self._match_account_ids(match)
        self._assignment["steamids"] = [p.steamid for p in match.players]
        self._assignment["human_slots"] = len(match.players)
        self._assignment["max_humans"] = MAX_HUMANS

    def _attach_waiting_to_active_match_locked(self) -> None:
        match = self._active_match_locked()
        if match is None:
            return

        existing = {p.steamid: p for p in match.players}
        changed = False
        while self._queue and len(match.players) < MAX_HUMANS:
            player = self._queue.pop(0)
            already = existing.get(player.steamid)
            if already is not None:
                # Repair a stale/restarted search for a player who is already
                # attached to this allocation. Older builds could leave the
                # player's public state as "searching" while silently dropping
                # the new queue entry here forever.
                already.game_type = player.game_type
                already.client_version = player.client_version
                self._states[player.steamid] = self._state_for_match_player_locked(
                    match, already
                )
                continue
            match.players.append(player)
            existing[player.steamid] = player
            changed = True
            self._states[player.steamid] = self._state_for_match_player_locked(
                match, player
            )

        if changed:
            self._sync_assignment_locked(match)
            self._refresh_match_player_states_locked(match)

    def _allocate_from_first_human_locked(self) -> None:
        if not self._queue or not self._server_idle_locked():
            return

        # Take everyone currently waiting, up to the 10-human server cap. The
        # important difference from Valve-style 5v5 formation is that ONE player
        # is enough to allocate the server.
        players = self._queue[:MAX_HUMANS]
        del self._queue[:len(players)]

        self._next_match_id += 1
        match_id = self._next_match_id
        map_name = self._choose_map_locked()
        match = Match(match_id, 0, players, map_name)
        self._matches[match_id] = match
        self._server["reserved_account_ids"] = []

        self._assignment = {
            "match_id": match_id,
            "reservation_id": 0,
            "map": map_name,
            "account_ids": self._match_account_ids(match),
            "steamids": [p.steamid for p in players],
            "human_slots": len(players),
            "max_humans": MAX_HUMANS,
            "fill_with_bots": True,
            "tickrate": 64,
            "game_type": players[0].game_type if players else 8,
            "client_version": players[0].client_version if players else 0,
            "srcds_game_type": 0,
            "srcds_game_mode": 1,
        }

        self._refresh_match_player_states_locked(match)

    def _try_form_locked(self) -> None:
        # If a bot-filled match already exists, new humans go straight into it.
        self._attach_waiting_to_active_match_locked()

        # If no match exists, the first waiting human boots the server.
        if self._active_match_locked() is None:
            self._allocate_from_first_human_locked()

        self._publish_search_states_locked()

    def start(self, steamid: str, game_type: int = 8, client_version: int = 0) -> dict[str, Any]:
        with self._lock:
            account_id = account_id_from_steamid64(steamid)
            if not account_id:
                return {"state": "error", "error": "invalid steamid"}

            existing = self._states.get(steamid, {})
            if existing.get("state") in ("reserved", "in_match"):
                return dict(existing)

            # Idempotent repair path: if a previous build left this SteamID in
            # "searching" without a queue entry, or it is already attached to a
            # still-live allocation, reconstruct the correct state instead of
            # returning the dead searching state forever.
            if existing.get("state") in ("searching", "allocating"):
                match = self._active_match_locked()
                if match is not None:
                    for attached in match.players:
                        if attached.steamid == steamid:
                            attached.game_type = int(game_type or attached.game_type or 8)
                            attached.client_version = int(client_version or attached.client_version or 0)
                            self._states[steamid] = self._state_for_match_player_locked(
                                match, attached
                            )
                            return dict(self._states[steamid])

                if not any(q.steamid == steamid for q in self._queue):
                    self._queue.append(QueueEntry(
                        steamid=steamid,
                        account_id=account_id,
                        game_type=int(game_type or 8),
                        client_version=int(client_version or 0),
                    ))
                self._try_form_locked()
                return dict(self._states[steamid])

            self._queue = [q for q in self._queue if q.steamid != steamid]
            self._queue.append(QueueEntry(
                steamid=steamid,
                account_id=account_id,
                game_type=int(game_type or 8),
                client_version=int(client_version or 0),
            ))
            self._states[steamid] = {
                "state": "searching",
                "server_online": self._server_online_locked(),
                "server_available": self._server_joinable_locked(),
            }
            self._try_form_locked()
            return dict(self._states[steamid])

    def stop(self, steamid: str) -> dict[str, Any]:
        with self._lock:
            self._queue = [q for q in self._queue if q.steamid != steamid]
            old = self._states.get(steamid, {})

            # If the user cancels before the match actually starts, detach them
            # from the allocation too. Otherwise an empty reserved match can
            # remain "active" and poison the next search for the same SteamID.
            match = self._active_match_locked()
            if match is not None and match.state in ("allocating", "reserved"):
                before = len(match.players)
                match.players = [p for p in match.players if p.steamid != steamid]
                if len(match.players) != before:
                    if match.players:
                        self._sync_assignment_locked(match)
                        self._refresh_match_player_states_locked(match)
                    else:
                        match.state = "complete"
                        if (
                            self._assignment
                            and int(self._assignment.get("match_id") or 0)
                            == match.match_id
                        ):
                            self._assignment = None
                        self._server["ready_match_id"] = 0
                        self._server["reserved_account_ids"] = []

            self._states[steamid] = {
                "state": "idle",
                "previous_state": old.get("state", ""),
            }
            self._try_form_locked()
            return dict(self._states[steamid])

    def state(self, steamid: str) -> dict[str, Any]:
        with self._lock:
            state = dict(self._states.get(steamid, {"state": "idle"}))
            state.setdefault("server_online", self._server_online_locked())
            state.setdefault("server_available", self._server_joinable_locked())
            return state

    def server_heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._server["agent_id"] = str(
                body.get("agent_id") or "windows-laptop"
            )
            self._server["public_host"] = str(body.get("public_host") or "")
            self._server["public_port"] = int(body.get("public_port") or 27015)
            self._server["server_version"] = int(body.get("server_version") or 0)
            self._server["server_id"] = int(body.get("server_id") or 0)
            self._server["last_seen"] = time.time()
            maps = body.get("maps")
            if isinstance(maps, list):
                self._server["maps"] = [
                    str(m) for m in maps if str(m).strip()
                ]

            reserved_accounts = body.get("reserved_account_ids")
            if isinstance(reserved_accounts, list):
                self._server["reserved_account_ids"] = [
                    int(x) for x in reserved_accounts
                    if str(x).isdigit() and int(x) > 0
                ]

            started_match_id = int(body.get("started_match_id") or 0)
            if started_match_id:
                self.server_match_started(started_match_id)

            ready_match_id = int(body.get("ready_match_id") or 0)
            native_reservation_id = int(body.get("reservation_id") or 0)
            if ready_match_id and native_reservation_id:
                self._server["ready_match_id"] = ready_match_id
                match = self._matches.get(ready_match_id)
                if (
                    match
                    and match.state in ("allocating", "reserved", "in_match")
                    and self._assignment
                ):
                    match.reservation_id = native_reservation_id
                    host = str(self._server.get("public_host") or "")
                    port = int(self._server.get("public_port") or 27015)
                    if host:
                        match.server_address = f"{host}:{port}"
                        if match.state == "allocating":
                            match.state = "reserved"
                        self._sync_assignment_locked(match)
                        self._refresh_match_player_states_locked(match)

            if self._assignment:
                mid = int(self._assignment.get("match_id") or 0)
                match = self._matches.get(mid)
                if (
                    match
                    and match.state == "allocating"
                    and time.time() - match.created_at > ALLOCATE_TIMEOUT_SECONDS
                ):
                    # Server failed to boot. Put still-interested players back in
                    # line and let the next heartbeat retry from a clean slot.
                    for player in reversed(match.players):
                        if self._states.get(player.steamid, {}).get("state") != "idle":
                            self._queue.insert(0, player)
                    match.state = "complete"
                    self._assignment = None
                    self._server["ready_match_id"] = 0

            self._try_form_locked()
            return {
                "ok": True,
                "assignment": dict(self._assignment)
                if self._assignment else None,
                "server_online": True,
                "server_available": self._server_joinable_locked(),
            }

    def server_match_started(self, match_id: int) -> None:
        with self._lock:
            match = self._matches.get(int(match_id))
            if not match or match.state == "complete":
                return
            match.state = "in_match"
            self._sync_assignment_locked(match)
            self._refresh_match_player_states_locked(match)

    def server_match_ended(
        self, match_id: int, result: dict[str, Any] | None = None
    ) -> list[str]:
        with self._lock:
            match = self._matches.get(int(match_id))
            if not match:
                return []

            result = dict(result or {})
            match.state = "complete"
            steamids = [p.steamid for p in match.players]

            if result.get("reason") == "accept_timeout":
                # This now means nobody entered the freshly-created server.
                # Keep players who were still interested in the queue so the
                # server can be retried; explicit cancellations remain idle.
                for player in match.players:
                    if self._states.get(player.steamid, {}).get("state") != "idle":
                        self._queue.append(player)
                        self._states[player.steamid] = {"state": "searching"}
            else:
                for player in match.players:
                    self._states[player.steamid] = {
                        "state": "idle",
                        "last_match_id": match.match_id,
                        "last_map": match.map_name,
                        "result": result,
                    }

            if (
                self._assignment
                and int(self._assignment.get("match_id") or 0) == match.match_id
            ):
                self._assignment = None
            self._server["ready_match_id"] = 0
            self._server["reserved_account_ids"] = []
            self._try_form_locked()
            return steamids

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "waiting": len(self._queue),
                "server_online": self._server_online_locked(),
                "server_available": self._server_joinable_locked(),
                "server": dict(self._server),
                "assignment": dict(self._assignment)
                if self._assignment else None,
                "matches": {
                    str(mid): {
                        "state": match.state,
                        "map": match.map_name,
                        "server_address": match.server_address,
                        "players": [p.steamid for p in match.players],
                        "human_count": len(match.players),
                        "max_humans": MAX_HUMANS,
                    }
                    for mid, match in self._matches.items()
                },
            }
