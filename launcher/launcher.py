#!/usr/bin/env python3
"""Python launcher for CS:GO Revival (no C++ compiler needed).

Normal run (python launcher.py):
  1. fetch the player's inventory from the revival server
  2. write it to <csgo_dir>/csgo_gc/inventory.txt
  3. launch CS:GO Legacy
  4. if a sync_token is configured: wait for the game to close, then upload the
     (now updated) inventory.txt back to the server so opened cases, new skins
     and equips PERSIST for next time.

Manual sync-back (python launcher.py --upload):
  Just read the local inventory.txt and upload it. Use this if you launched the
  game some other way (e.g. Steam) and want to save what changed.

Uses only the Python standard library.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

REQUIRED = ("server_url", "steam_id", "csgo_dir")
TRUE_VALUES = {"1", "true", "yes", "on"}


def load_config(path: str) -> dict:
    config = {
        "server_url": "", "steam_id": "", "csgo_dir": "",
        "game_exe": "", "game_args": "", "launch_game": "1",
        "sync_token": "",
    }
    if not os.path.exists(path):
        print(f"[launcher] config file not found: {path}")
        print("[launcher] copy launcher.example.cfg to launcher.cfg and edit it.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] in "#;" or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key in config:
                config[key] = value
    for req in REQUIRED:
        if not config[req]:
            print(f"[launcher] '{req}' is required in {path}")
            sys.exit(1)
    if not config["game_exe"]:
        if sys.platform.startswith("win"):
            config["game_exe"] = "csgo_revival.exe"
        elif sys.platform == "darwin":
            config["game_exe"] = "csgo_osx64"
        else:
            config["game_exe"] = "csgo_linux64"
    if not config["game_args"]:
        config["game_args"] = "-steam -game csgo -novid"
    return config


def inventory_url(config: dict) -> str:
    return config["server_url"].rstrip("/") + "/inventory/" + config["steam_id"]


def inventory_path(config: dict) -> str:
    return os.path.join(config["csgo_dir"], "csgo_gc", "inventory.txt")


def fetch_inventory(config: dict) -> None:
    url = inventory_url(config)
    print(f"[launcher] syncing inventory for {config['steam_id']}")
    print(f"[launcher] server: {url}")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read()
    except urllib.error.URLError as exc:
        print(f"[launcher] failed to fetch inventory: {exc}")
        print("[launcher] aborting so we don't launch with a stale inventory.")
        sys.exit(2)

    path = inventory_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    print(f"[launcher] wrote {len(body)} bytes -> {path}")


def upload_inventory(config: dict) -> None:
    """Upload the local inventory.txt back to the server (persistence)."""
    if not config["sync_token"]:
        print("[launcher] no sync_token set - skipping upload (persistence off).")
        return

    path = inventory_path(config)
    if not os.path.exists(path):
        print(f"[launcher] no inventory.txt to upload at {path}")
        return

    with open(path, "rb") as fh:
        body = fh.read()

    req = urllib.request.Request(
        inventory_url(config), data=body, method="POST",
        headers={"X-Sync-Token": config["sync_token"],
                 "Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[launcher] uploaded inventory ({resp.status}) - changes saved.")
    except urllib.error.HTTPError as exc:
        print(f"[launcher] upload failed: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as exc:
        print(f"[launcher] upload failed: {exc}")


def _mm_request_path(config: dict) -> str:
    return os.path.join(config["csgo_dir"], "csgo_gc", "mm_request.txt")


def _mm_state_path(config: dict) -> str:
    return os.path.join(config["csgo_dir"], "csgo_gc", "mm_state.txt")


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _read_kv(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def _http_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"User-Agent": "csgo-revival-matchmaking/1.0"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _write_mm_state(config: dict, state: dict) -> None:
    fields = [
        "state", "players_searching", "players_required", "server_online",
        "match_id", "reservation_id", "map", "server_address",
        "public_host", "public_port", "game_type", "error",
    ]
    lines: list[str] = []
    for key in fields:
        if key in state:
            value = state[key]
            if isinstance(value, bool):
                value = 1 if value else 0
            lines.append(f"{key}={value}")
    for key in ("waiting_account_ids", "account_ids"):
        value = state.get(key)
        if isinstance(value, list):
            lines.append(f"{key}=" + ",".join(str(int(x)) for x in value))
    _atomic_write_text(_mm_state_path(config), "\n".join(lines) + "\n")


def matchmaking_bridge(config: dict, stop_event: threading.Event) -> None:
    base = config["server_url"].rstrip("/")
    request_path = _mm_request_path(config)
    last_request = ""
    searching = False
    last_poll = 0.0

    while not stop_event.wait(0.20):
        try:
            try:
                with open(request_path, "r", encoding="utf-8", errors="replace") as fh:
                    request_text = fh.read()
            except OSError:
                request_text = ""

            if request_text and request_text != last_request:
                last_request = request_text
                request = _read_kv(request_path)
                action = request.get("action", "")
                if action == "start":
                    state = _http_json(
                        "POST", base + "/matchmaking/start",
                        {"steamid": config["steam_id"]},
                    )
                    _write_mm_state(config, state)
                    searching = True
                    last_poll = 0.0
                    print("[launcher] matchmaking: joined shared Competitive queue")
                elif action == "stop":
                    state = _http_json(
                        "POST", base + "/matchmaking/stop",
                        {"steamid": config["steam_id"]},
                    )
                    _write_mm_state(config, state)
                    searching = False
                    print("[launcher] matchmaking: left queue")

            if searching and time.monotonic() - last_poll >= 0.75:
                last_poll = time.monotonic()
                state = _http_json(
                    "GET", base + "/matchmaking/state/" + config["steam_id"]
                )
                _write_mm_state(config, state)
                if state.get("state") in ("reserved", "in_match"):
                    # Keep polling slowly so reconnect/end state stays fresh.
                    pass
                elif state.get("state") == "idle":
                    searching = False
        except (OSError, ValueError, urllib.error.URLError) as exc:
            # Matchmaking should recover automatically when the backend/tunnel
            # comes back; do not kill the launcher or the running game.
            print(f"[launcher] matchmaking bridge retrying after: {exc}")
            stop_event.wait(1.0)


def launch_and_wait(config: dict) -> None:
    exe = os.path.join(config["csgo_dir"], config["game_exe"])
    if not os.path.exists(exe):
        print(f"[launcher] game executable not found: {exe}")
        print("[launcher] check csgo_dir / game_exe in your config.")
        sys.exit(3)

    # Clear stale matchmaking state before this session.
    for path in (_mm_request_path(config), _mm_state_path(config)):
        try:
            os.remove(path)
        except OSError:
            pass

    stop_bridge = threading.Event()
    bridge = threading.Thread(
        target=matchmaking_bridge, args=(config, stop_bridge),
        name="revival-matchmaking", daemon=True,
    )
    bridge.start()

    args = [exe] + config["game_args"].split()
    print(f"[launcher] launching {exe} ...")
    try:
        proc = subprocess.Popen(args, cwd=config["csgo_dir"])
    except OSError as exc:
        stop_bridge.set()
        print(f"[launcher] failed to launch game: {exc}")
        sys.exit(4)

    print("[launcher] launched. Matchmaking bridge is active.")
    proc.wait()
    stop_bridge.set()
    bridge.join(timeout=2)

    if not config["sync_token"]:
        print("[launcher] game closed. No sync_token set, so inventory persistence is off.")
        return

    # give csgo_gc a moment to finish writing inventory.txt on exit
    time.sleep(2)
    print("[launcher] game closed - saving your inventory back to the server.")
    upload_inventory(config)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))

    args = sys.argv[1:]
    upload_only = "--upload" in args
    cfg_args = [a for a in args if not a.startswith("--")]
    cfg_path = cfg_args[0] if cfg_args else os.path.join(here, "launcher.cfg")

    config = load_config(cfg_path)

    if upload_only:
        # just push the local inventory back (e.g. after launching via Steam)
        upload_inventory(config)
        return

    fetch_inventory(config)

    if config["launch_game"].lower() not in TRUE_VALUES:
        print("[launcher] launch_game is off; inventory synced, not launching.")
        return

    launch_and_wait(config)


if __name__ == "__main__":
    main()
