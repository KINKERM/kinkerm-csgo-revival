#!/usr/bin/env python3
"""Admin CLI for the CS:GO Revival server.

Examples:
  python3 admin.py catalog
  python3 admin.py grant-case 76561198000000000 chroma_3_case --count 5
  python3 admin.py grant-item 76561198000000000 --def-index 7 --rarity 3
  python3 admin.py show 76561198000000000
  python3 admin.py revoke 76561198000000000 --def-index 4001
  python3 admin.py clear 76561198000000000

Server URL:   --server  or  $CSGO_REVIVAL_SERVER   (default http://127.0.0.1:8787)
Admin token:  --token   or  $CSGO_REVIVAL_TOKEN    (falls back to data/server_config.json)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "data", "server_config.json")


def resolve_token(cli_token: str | None) -> str:
    if cli_token:
        return cli_token
    env = os.environ.get("CSGO_REVIVAL_TOKEN")
    if env:
        return env
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("admin_token", "")
    return ""


def request(server: str, method: str, path: str, token: str, body: dict | None = None):
    url = server.rstrip("/") + path
    data = None
    headers = {"X-Admin-Token": token}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {raw}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"connection failed: {exc.reason}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def main() -> None:
    parser = argparse.ArgumentParser(description="CS:GO Revival admin CLI")
    parser.add_argument("--server", default=os.environ.get("CSGO_REVIVAL_SERVER", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("catalog", help="list available cases")
    sub.add_parser("players", help="list players with inventories")

    p_show = sub.add_parser("show", help="show a player's inventory")
    p_show.add_argument("steamid")

    p_gc = sub.add_parser("grant-case", help="give a player a case (+ matching key)")
    p_gc.add_argument("steamid")
    p_gc.add_argument("case", help="case id from `catalog`")
    p_gc.add_argument("--count", type=int, default=1)
    p_gc.add_argument("--no-key", action="store_true", help="do not bundle the matching key")

    p_gi = sub.add_parser("grant-item", help="give a player an arbitrary item by def_index")
    p_gi.add_argument("steamid")
    p_gi.add_argument("--def-index", type=int, required=True)
    p_gi.add_argument("--quality", type=int, default=None)
    p_gi.add_argument("--rarity", type=int, default=None)
    p_gi.add_argument("--count", type=int, default=1)

    p_rv = sub.add_parser("revoke", help="remove items with a def_index")
    p_rv.add_argument("steamid")
    p_rv.add_argument("--def-index", type=int, required=True)
    p_rv.add_argument("--count", type=int, default=None)

    p_cl = sub.add_parser("clear", help="wipe a player's inventory")
    p_cl.add_argument("steamid")

    args = parser.parse_args()
    token = resolve_token(args.token)

    if args.cmd == "catalog":
        data = request(args.server, "GET", "/catalog", token)
        cases = data.get("cases", {})
        if not cases:
            print("(catalog is empty - run build_catalog.py to populate it)")
            return
        print(f"{'CASE ID':40} {'DEF':>6} {'KEY':>6}  DISPLAY NAME")
        for slug, case in sorted(cases.items()):
            print(f"{slug:40} {case['def_index']:>6} {str(case.get('key_def_index') or '-'):>6}  {case.get('display_name', '')}")
        return

    if args.cmd == "players":
        data = request(args.server, "GET", "/admin/players", token)
        for sid in data.get("players", []):
            print(sid)
        return

    if args.cmd == "show":
        data = request(args.server, "GET", f"/admin/player/{args.steamid}", token)
        print(json.dumps(data, indent=2))
        return

    if args.cmd == "grant-case":
        body = {"steamid": args.steamid, "case": args.case,
                "count": args.count, "include_key": not args.no_key}
        result = request(args.server, "POST", "/admin/grant-case", token, body)
        print(json.dumps(result, indent=2))
        return

    if args.cmd == "grant-item":
        body = {"steamid": args.steamid, "def_index": args.def_index, "count": args.count}
        if args.quality is not None:
            body["quality"] = args.quality
        if args.rarity is not None:
            body["rarity"] = args.rarity
        result = request(args.server, "POST", "/admin/grant-item", token, body)
        print(json.dumps(result, indent=2))
        return

    if args.cmd == "revoke":
        body = {"steamid": args.steamid, "def_index": args.def_index}
        if args.count is not None:
            body["count"] = args.count
        result = request(args.server, "POST", "/admin/revoke", token, body)
        print(json.dumps(result, indent=2))
        return

    if args.cmd == "clear":
        result = request(args.server, "POST", "/admin/clear", token, {"steamid": args.steamid})
        print(json.dumps(result, indent=2))
        return


if __name__ == "__main__":
    main()
