#!/usr/bin/env python3
"""Python launcher for CS:GO Revival (no C++ compiler needed).

Does the same job as the C++ launcher, but runs anywhere Python does:
  1. read launcher.cfg
  2. fetch the player's inventory from the revival server
  3. write it to <csgo_dir>/csgo_gc/inventory.txt
  4. launch CS:GO Legacy

Usage:
  python launcher.py [path/to/launcher.cfg]

If no path is given it looks for launcher.cfg next to this script.
Uses only the Python standard library.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request

REQUIRED = ("server_url", "steam_id", "csgo_dir")
TRUE_VALUES = {"1", "true", "yes", "on"}


def load_config(path: str) -> dict:
    config = {
        "server_url": "",
        "steam_id": "",
        "csgo_dir": "",
        "game_exe": "",
        "game_args": "",
        "launch_game": "1",
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
            config["game_exe"] = "csgo.exe"
        elif sys.platform == "darwin":
            config["game_exe"] = "csgo_osx64"
        else:
            config["game_exe"] = "csgo_linux64"

    if not config["game_args"]:
        config["game_args"] = "-steam -game csgo -novid"

    return config


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "launcher.cfg")
    config = load_config(cfg_path)

    server = config["server_url"].rstrip("/")
    url = f"{server}/inventory/{config['steam_id']}"
    print(f"[launcher] syncing inventory for {config['steam_id']}")
    print(f"[launcher] server: {url}")

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read()
    except urllib.error.URLError as exc:
        print(f"[launcher] failed to fetch inventory: {exc}")
        print("[launcher] aborting so we don't launch with a stale inventory.")
        sys.exit(2)

    inv_path = os.path.join(config["csgo_dir"], "csgo_gc", "inventory.txt")
    os.makedirs(os.path.dirname(inv_path), exist_ok=True)
    with open(inv_path, "wb") as fh:
        fh.write(body)
    print(f"[launcher] wrote {len(body)} bytes -> {inv_path}")

    if config["launch_game"].lower() not in TRUE_VALUES:
        print("[launcher] launch_game is off; inventory synced, not launching.")
        return

    exe = os.path.join(config["csgo_dir"], config["game_exe"])
    if not os.path.exists(exe):
        print(f"[launcher] game executable not found: {exe}")
        print("[launcher] check csgo_dir / game_exe in your config.")
        sys.exit(3)

    args = [exe] + config["game_args"].split()
    print(f"[launcher] launching {exe} ...")
    try:
        # cwd must be the game dir so csgo_gc finds its relative paths
        subprocess.Popen(args, cwd=config["csgo_dir"])
    except OSError as exc:
        print(f"[launcher] failed to launch game: {exc}")
        sys.exit(4)

    print("[launcher] launched. Have fun!")


if __name__ == "__main__":
    main()
