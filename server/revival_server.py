#!/usr/bin/env python3
"""CS:GO Revival inventory + admin server (stdlib only).

Public:
  GET  /health                      -> "ok"
  GET  /catalog                     -> catalog.json
  GET  /inventory/<steamid64>       -> csgo_gc inventory.txt (text/plain)

Sync (require header  X-Sync-Token: <token>, admin token also accepted):
  POST /inventory/<steamid64>       <- upload local inventory.txt (persistence)

Admin (require header  X-Admin-Token: <token>):
  GET  /admin/player/<steamid64>    -> player record (json)
  GET  /admin/players               -> list of steamids
  POST /admin/grant-case            {steamid, case, count?, include_key?}
  POST /admin/grant-item            {steamid, def_index, quality?, rarity?, attributes?, count?}
  POST /admin/revoke                {steamid, def_index, count?}
  POST /admin/clear                 {steamid}

Run:  python3 revival_server.py --host 0.0.0.0 --port 8787
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import inventory as inventory_mod
from catalog import Catalog
from matchmaking import MatchmakingCoordinator
from store import PlayerStore

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "server_config.json")


def load_config() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    config: dict = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            config = json.load(fh)

    # fill in any missing keys (also upgrades older config files in place)
    changed = False
    defaults = {
        "admin_token": lambda: secrets.token_hex(24),
        "sync_token": lambda: secrets.token_hex(24),
        "players_file": lambda: os.path.join(DATA_DIR, "players.json"),
        "catalog_file": lambda: os.path.join(DATA_DIR, "catalog.json"),
    }
    for key, factory in defaults.items():
        if not config.get(key):
            config[key] = factory()
            changed = True

    # Optional: a case slug (from the catalog) that acts as the "Gold Trade-Up"
    # crate. Every player is auto-given this crate + its key when their inventory
    # is served, so anyone can do 5 Covert -> gold with NO admin grant. Empty = off.
    if "gold_tradeup_case" not in config:
        config["gold_tradeup_case"] = ""
        changed = True

    if changed:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        print(f"[revival] wrote config -> {CONFIG_PATH}")
    return config


class Handler(BaseHTTPRequestHandler):
    server_version = "CSGORevival/1.0"

    # injected by make_handler
    store: PlayerStore
    catalog: Catalog
    admin_token: str
    sync_token: str
    matchmaking: MatchmakingCoordinator
    gold_tradeup_crate_def: int = 0
    gold_tradeup_key_def: int = 0

    # ---- helpers -----------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, code: int, text: str) -> None:
        self._send(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _authed(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Admin-Token", ""), self.admin_token
        )

    def _sync_authed(self) -> bool:
        # accept the sync token OR the admin token (admin is a superset)
        provided = self.headers.get("X-Sync-Token", "")
        return (secrets.compare_digest(provided, self.sync_token)
                or secrets.compare_digest(provided, self.admin_token))

    def _read_raw_body(self) -> str:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", errors="replace")

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):  # quieter logging
        print(f"[revival] {self.address_string()} {fmt % args}")

    def _inject_gold_tradeup_crate(self, player: dict) -> None:
        """Give every player the Gold Trade-Up crate (+ its key) so anyone can do
        5 Covert -> gold without an admin grant. Injected into the *rendered*
        inventory only (not stored), so it reappears each session if consumed."""
        crate_def = self.gold_tradeup_crate_def
        if not crate_def:
            return
        items = player.setdefault("items", [])
        present = {int(it.get("def_index", 0) or 0) for it in items}
        if crate_def not in present:
            items.append({"def_index": crate_def, "quality": 4, "rarity": 1})
        key_def = self.gold_tradeup_key_def
        if key_def and key_def not in present:
            items.append({"def_index": key_def, "quality": 4, "rarity": 1})

    # ---- routing -----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/health" or path == "":
            return self._send_text(200, "ok")

        if path == "/catalog":
            return self._send_json(200, {
                "cases": self.catalog.cases,
                "keys": self.catalog.keys,
                "items": self.catalog.items,
            })

        if path.startswith("/matchmaking/state/"):
            steamid = path[len("/matchmaking/state/"):]
            if not steamid.isdigit():
                return self._send_text(400, "invalid steamid")
            return self._send_json(200, self.matchmaking.state(steamid))

        if path.startswith("/matchmaking/reward/"):
            steamid = path[len("/matchmaking/reward/"):]
            if not steamid.isdigit():
                return self._send_text(400, "invalid steamid")
            return self._send_json(200, self.matchmaking.pop_reward(steamid))

        if path == "/matchmaking/admin/state":
            if not self._authed():
                return self._send_text(401, "unauthorized")
            return self._send_json(200, self.matchmaking.snapshot())

        if path.startswith("/inventory/"):
            steamid = path[len("/inventory/"):]
            if not steamid.isdigit():
                return self._send_text(400, "invalid steamid")
            player = self.store.get_player(steamid)
            self._inject_gold_tradeup_crate(player)
            return self._send_text(200, inventory_mod.render_inventory_txt(player))

        if path == "/admin/players":
            if not self._authed():
                return self._send_text(401, "unauthorized")
            return self._send_json(200, {"players": self.store.list_players()})

        if path.startswith("/admin/player/"):
            if not self._authed():
                return self._send_text(401, "unauthorized")
            steamid = path[len("/admin/player/"):]
            return self._send_json(200, self.store.get_player(steamid))

        return self._send_text(404, "not found")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/matchmaking/start":
            body = self._read_json_body()
            steamid = str(body.get("steamid", "")).strip()
            if not steamid.isdigit():
                return self._send_json(400, {"error": "valid steamid required"})
            return self._send_json(200, self.matchmaking.start(
                steamid,
                game_type=int(body.get("game_type") or 8),
                client_version=int(body.get("client_version") or 0),
            ))

        if path == "/matchmaking/stop":
            body = self._read_json_body()
            steamid = str(body.get("steamid", "")).strip()
            if not steamid.isdigit():
                return self._send_json(400, {"error": "valid steamid required"})
            return self._send_json(200, self.matchmaking.stop(steamid))

        if path == "/matchmaking/server/heartbeat":
            body = self._read_json_body()
            return self._send_json(200, self.matchmaking.server_heartbeat(body))

        if path == "/matchmaking/server/reward":
            body = self._read_json_body()
            steamid = str(body.get("steamid", "")).strip()
            payload_b64 = str(body.get("payload_b64", "")).strip()
            if not steamid.isdigit() or not payload_b64 or len(payload_b64) > 6_000_000:
                return self._send_json(400, {"error": "invalid reward payload"})
            return self._send_json(
                200, self.matchmaking.queue_reward(steamid, payload_b64)
            )

        if path == "/matchmaking/server/started":
            body = self._read_json_body()
            self.matchmaking.server_match_started(int(body.get("match_id") or 0))
            return self._send_json(200, {"ok": True})

        if path == "/matchmaking/server/ended":
            body = self._read_json_body()
            steamids = self.matchmaking.server_match_ended(
                int(body.get("match_id") or 0),
                body.get("result") if isinstance(body.get("result"), dict) else {},
            )
            return self._send_json(200, {"ok": True, "players": steamids})

        # Two-way sync: a client uploads its local inventory.txt so opened cases,
        # new skins and equips persist. Requires the sync (or admin) token.
        if path.startswith("/inventory/"):
            steamid = path[len("/inventory/"):]
            if not steamid.isdigit():
                return self._send_text(400, "invalid steamid")
            if not self._sync_authed():
                return self._send_text(401, "unauthorized")
            text = self._read_raw_body()
            parsed = inventory_mod.parse_inventory_txt(text)
            count = self.store.replace_inventory(
                steamid,
                parsed["items"],
                parsed["default_equips"],
                parsed.get("operation_riptide"),
                parsed.get("revival_profile"),
            )
            return self._send_json(200, {"ok": True, "items": count})

        if not path.startswith("/admin/"):
            return self._send_text(404, "not found")
        if not self._authed():
            return self._send_text(401, "unauthorized")

        body = self._read_json_body()
        steamid = str(body.get("steamid", "")).strip()
        if not steamid.isdigit():
            return self._send_json(400, {"error": "valid steamid (SteamID64) required"})

        try:
            if path == "/admin/grant-case":
                added = self.store.grant_case(
                    steamid,
                    str(body["case"]),
                    count=int(body.get("count", 1)),
                    include_key=bool(body.get("include_key", True)),
                )
                return self._send_json(200, {"ok": True, "added": added})

            if path == "/admin/grant-item":
                n = self.store.grant_item(
                    steamid,
                    int(body["def_index"]),
                    quality=body.get("quality"),
                    rarity=body.get("rarity"),
                    attributes=body.get("attributes"),
                    count=int(body.get("count", 1)),
                )
                return self._send_json(200, {"ok": True, "added": n})

            if path == "/admin/revoke":
                count = body.get("count")
                removed = self.store.revoke_def_index(
                    steamid,
                    int(body["def_index"]),
                    count=None if count is None else int(count),
                )
                return self._send_json(200, {"ok": True, "removed": removed})

            if path == "/admin/clear":
                self.store.clear(steamid)
                return self._send_json(200, {"ok": True})

        except KeyError as exc:
            return self._send_json(400, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self._send_json(400, {"error": f"bad request: {exc}"})

        return self._send_text(404, "not found")


def make_handler(store: PlayerStore, catalog: Catalog, admin_token: str, sync_token: str,
                 matchmaking: MatchmakingCoordinator,
                 gold_tradeup_crate_def: int = 0, gold_tradeup_key_def: int = 0):
    return type("BoundHandler", (Handler,), {
        "store": store,
        "catalog": catalog,
        "admin_token": admin_token,
        "sync_token": sync_token,
        "matchmaking": matchmaking,
        "gold_tradeup_crate_def": gold_tradeup_crate_def,
        "gold_tradeup_key_def": gold_tradeup_key_def,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="CS:GO Revival server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    config = load_config()
    catalog = Catalog.load(config["catalog_file"])
    store = PlayerStore(config["players_file"], catalog)

    # resolve the Gold Trade-Up crate (auto-given to every player)
    gold_crate_def = 0
    gold_key_def = 0
    gold_case_slug = config.get("gold_tradeup_case", "")
    if gold_case_slug:
        case = catalog.get_case(gold_case_slug)
        if case:
            gold_crate_def = int(case.get("def_index") or 0)
            gold_key_def = int(case.get("key_def_index") or 0)
            print(f"[revival] gold trade-up crate: '{gold_case_slug}' "
                  f"(def {gold_crate_def}, key {gold_key_def}) auto-given to all players")
        else:
            print(f"[revival] WARNING: gold_tradeup_case '{gold_case_slug}' not found in catalog")

    matchmaking = MatchmakingCoordinator()
    handler = make_handler(store, catalog, config["admin_token"], config["sync_token"],
                           matchmaking, gold_crate_def, gold_key_def)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)

    print(f"[revival] serving on http://{args.host}:{args.port}")
    print(f"[revival] catalog: {len(catalog.cases)} cases, {len(catalog.items)} items")
    print(f"[revival] admin token: {config['admin_token']}")
    print(f"[revival] sync token:  {config['sync_token']}  (put this in launcher.cfg for persistence)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[revival] shutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
