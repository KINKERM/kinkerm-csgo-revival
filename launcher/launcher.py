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

import os
import subprocess
import sys
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


def launch_and_wait(config: dict) -> None:
    exe = os.path.join(config["csgo_dir"], config["game_exe"])
    if not os.path.exists(exe):
        print(f"[launcher] game executable not found: {exe}")
        print("[launcher] check csgo_dir / game_exe in your config.")
        sys.exit(3)

    args = [exe] + config["game_args"].split()
    print(f"[launcher] launching {exe} ...")
    try:
        proc = subprocess.Popen(args, cwd=config["csgo_dir"])
    except OSError as exc:
        print(f"[launcher] failed to launch game: {exc}")
        sys.exit(4)

    if not config["sync_token"]:
        print("[launcher] launched. (No sync_token set, so changes won't persist.)")
        return

    print("[launcher] launched. Waiting for you to close the game to save changes...")
    proc.wait()
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
