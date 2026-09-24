"""Single-queue CS:GO Revival matchmaking coordinator.

The revival intentionally exposes one 5v5 Competitive queue. Clients enqueue via
revival_server.py; one lightweight Windows server agent registers the single
available srcds slot. When ten players are waiting, the coordinator allocates a
map, asks the agent to start srcds, then publishes the reservation to all ten
clients once the agent reports the server ready.
"""

from __future__ import annotations

import random
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any


# Deliberately broad: the Windows agent reports which BSPs actually exist, and
# the coordinator chooses only from the intersection. This lets an old install
# keep removed maps without creating dead reservations for maps it does not have.
DEFAULT_MAP_POOL = (
    "de_dust2",
    "de_mirage",
    "de_inferno",
    "de_nuke",
    "de_overpass",
    "de_vertigo",
    "de_train",
    "de_cache",
    "de_cbble",
    "de_ancient",
    "de_anubis",
    "de_tuscan",
    "de_canals",
    "de_breach",
    "de_basalt",
    "cs_office",
    "cs_agency",
    "cs_italy",
)

PLAYERS_PER_MATCH = 10
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
            "last_seen": 0.0,
            "ready_match_id": 0,
            "maps": [],
        }
        self._assignment: dict[str, Any] | None = None

    def _server_online_locked(self) -> bool:
        return (
            bool(self._server.get("agent_id"))
            and time.time() - float(self._server.get("last_seen", 0.0)) <= SERVER_STALE_SECONDS
        )

    def _server_idle_locked(self) -> bool:
        if not self._server_online_locked():
            return False
        if self._assignment is not None:
            return False
        return not any(m.state in ("allocating", "reserved", "in_match") for m in self._matches.values())

    def _waiting_ids_locked(self) -> list[int]:
        return [q.account_id for q in self._queue]

    def _publish_search_states_locked(self) -> None:
        waiting = self._waiting_ids_locked()
        for q in self._queue:
            self._states[q.steamid] = {
                "state": "searching",
                "waiting_account_ids": waiting,
                "players_searching": len(waiting),
                "players_required": PLAYERS_PER_MATCH,
                "server_online": self._server_online_locked(),
            }

    def _choose_map_locked(self) -> str:
        available = [str(x) for x in self._server.get("maps", []) if x]
        if available:
            candidates = [m for m in self._map_pool if m in set(available)]
            if candidates:
                return random.choice(candidates)
        return random.choice(self._map_pool)

    def _try_form_locked(self) -> None:
        if len(self._queue) < PLAYERS_PER_MATCH or not self._server_idle_locked():
            self._publish_search_states_locked()
            return

        players = self._queue[:PLAYERS_PER_MATCH]
        del self._queue[:PLAYERS_PER_MATCH]

        self._next_match_id += 1
        match_id = self._next_match_id
        # The real reservation id is generated/acknowledged by srcds in its
        # native 9106 response. Do not invent one in the coordinator.
        reservation_id = 0
        map_name = self._choose_map_locked()
        match = Match(match_id, reservation_id, players, map_name)
        self._matches[match_id] = match

        self._assignment = {
            "match_id": match_id,
            "reservation_id": reservation_id,
            "map": map_name,
            "account_ids": [p.account_id for p in players],
            "steamids": [p.steamid for p in players],
            "tickrate": 64,
            "game_type": players[0].game_type if players else 8,
            "client_version": players[0].client_version if players else 0,
            "srcds_game_type": 0,
            "srcds_game_mode": 1,
        }

        for p in players:
            self._states[p.steamid] = {
                "state": "allocating",
                "match_id": match_id,
                "reservation_id": reservation_id,
                "map": map_name,
                "account_ids": [x.account_id for x in players],
            }

        self._publish_search_states_locked()

    def start(self, steamid: str, game_type: int = 8, client_version: int = 0) -> dict[str, Any]:
        with self._lock:
            account_id = account_id_from_steamid64(steamid)
            if not account_id:
                return {"state": "error", "error": "invalid steamid"}

            # Do not duplicate an active queue/match entry.
            existing = self._states.get(steamid, {})
            if existing.get("state") in ("searching", "allocating", "reserved", "in_match"):
                return dict(existing)

            self._queue = [q for q in self._queue if q.steamid != steamid]
            self._queue.append(QueueEntry(
                steamid=steamid,
                account_id=account_id,
                game_type=int(game_type or 8),
                client_version=int(client_version or 0),
            ))
            self._states[steamid] = {"state": "searching"}
            self._try_form_locked()
            return dict(self._states[steamid])

    def stop(self, steamid: str) -> dict[str, Any]:
        with self._lock:
            self._queue = [q for q in self._queue if q.steamid != steamid]
            old = self._states.get(steamid, {})
            # Searching can always be cancelled. Reserved/in-match is treated as
            # an abandon request by later result/rank handling.
            self._states[steamid] = {"state": "idle", "previous_state": old.get("state", "")}
            self._publish_search_states_locked()
            return dict(self._states[steamid])

    def state(self, steamid: str) -> dict[str, Any]:
        with self._lock:
            state = dict(self._states.get(steamid, {"state": "idle"}))
            state.setdefault("server_online", self._server_online_locked())
            return state

    def server_heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._server["agent_id"] = str(body.get("agent_id") or "windows-laptop")
            self._server["public_host"] = str(body.get("public_host") or "")
            self._server["public_port"] = int(body.get("public_port") or 27015)
            self._server["last_seen"] = time.time()
            maps = body.get("maps")
            if isinstance(maps, list):
                self._server["maps"] = [str(m) for m in maps if str(m).strip()]

            started_match_id = int(body.get("started_match_id") or 0)
            if started_match_id:
                self.server_match_started(started_match_id)

            ready_match_id = int(body.get("ready_match_id") or 0)
            native_reservation_id = int(body.get("reservation_id") or 0)
            if ready_match_id and native_reservation_id:
                self._server["ready_match_id"] = ready_match_id
                match = self._matches.get(ready_match_id)
                if match and match.state == "allocating" and self._assignment:
                    match.reservation_id = native_reservation_id
                    self._assignment["reservation_id"] = native_reservation_id
                    host = self._server.get("public_host", "")
                    port = int(self._server.get("public_port") or 27015)
                    if host:
                        match.server_address = f"{host}:{port}"
                        match.state = "reserved"
                        ids = [p.account_id for p in match.players]
                        for p in match.players:
                            self._states[p.steamid] = {
                                "state": "reserved",
                                "match_id": match.match_id,
                                "reservation_id": match.reservation_id,
                                "map": match.map_name,
                                "server_address": match.server_address,
                                "public_host": host,
                                "public_port": port,
                                "account_ids": ids,
                                "game_type": 8,  # legacy client Competitive search type
                            }

            # If allocation died before the server became ready, put players back.
            if self._assignment:
                mid = int(self._assignment.get("match_id") or 0)
                match = self._matches.get(mid)
                if match and match.state == "allocating" and time.time() - match.created_at > ALLOCATE_TIMEOUT_SECONDS:
                    for p in reversed(match.players):
                        self._queue.insert(0, p)
                    match.state = "complete"
                    self._assignment = None
                    self._publish_search_states_locked()

            self._try_form_locked()
            return {
                "ok": True,
                "assignment": dict(self._assignment) if self._assignment else None,
                "server_online": True,
            }

    def server_match_started(self, match_id: int) -> None:
        with self._lock:
            match = self._matches.get(int(match_id))
            if not match:
                return
            match.state = "in_match"
            for p in match.players:
                state = dict(self._states.get(p.steamid, {}))
                state["state"] = "in_match"
                self._states[p.steamid] = state

    def server_match_ended(self, match_id: int, result: dict[str, Any] | None = None) -> list[str]:
        with self._lock:
            match = self._matches.get(int(match_id))
            if not match:
                return []

            result = dict(result or {})
            match.state = "complete"
            steamids = [p.steamid for p in match.players]

            if result.get("reason") == "accept_timeout":
                connected = {
                    int(x) for x in result.get("connected_account_ids", [])
                    if str(x).isdigit()
                }
                # Players who actually accepted/entered go straight back into the
                # single Competitive queue. Missing players return idle; this is
                # the revival equivalent of Valve cancelling a failed accept.
                for p in match.players:
                    if p.account_id in connected:
                        self._queue.append(p)
                        self._states[p.steamid] = {"state": "searching"}
                    else:
                        self._states[p.steamid] = {
                            "state": "idle",
                            "error": "match_accept_timeout",
                            "last_match_id": match.match_id,
                        }
            else:
                for p in match.players:
                    self._states[p.steamid] = {
                        "state": "idle",
                        "last_match_id": match.match_id,
                        "last_map": match.map_name,
                        "result": result,
                    }

            if self._assignment and int(self._assignment.get("match_id") or 0) == match.match_id:
                self._assignment = None
            self._server["ready_match_id"] = 0
            self._publish_search_states_locked()
            self._try_form_locked()
            return steamids

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "waiting": len(self._queue),
                "server_online": self._server_online_locked(),
                "server": dict(self._server),
                "assignment": dict(self._assignment) if self._assignment else None,
                "matches": {
                    str(mid): {
                        "state": m.state,
                        "map": m.map_name,
                        "server_address": m.server_address,
                        "players": [p.steamid for p in m.players],
                    }
                    for mid, m in self._matches.items()
                },
            }
