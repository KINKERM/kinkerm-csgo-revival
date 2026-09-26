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

import base64
import json
import os
import re
import socket
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
    with open(path, "r", encoding="utf-8-sig") as fh:
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


def _operation_selection_from_inventory_bytes(body: bytes) -> tuple[int, int]:
    """Return (mission_card, selected_quest) from revival inventory text."""
    text = body.decode("utf-8", "replace")
    pos = text.find('"operation_riptide"')
    if pos < 0:
        return 0, 0
    section = text[pos:pos + 8192]

    def _number(name: str) -> int:
        match = re.search(
            rf'"{re.escape(name)}"\s+"?(\d+)"?',
            section,
        )
        return int(match.group(1)) if match else 0

    return _number("mission_id"), _number("selected_quest_id")


def _print_operation_selection(body: bytes, prefix: str) -> None:
    mission_card, selected_quest = _operation_selection_from_inventory_bytes(body)
    print(
        f"[launcher] operation {prefix}: "
        f"mission_card={mission_card} selected_quest={selected_quest}"
    )


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
    _print_operation_selection(body, "after sync")


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

    # This is deliberately printed on every matchmaking-triggered upload. It
    # proves whether the mission click reached persistent GC state BEFORE the
    # laptop allocation starts, so a broken mission never looks like a HUD bug.
    _print_operation_selection(body, "before upload")

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


def _mm_reward_path(config: dict) -> str:
    return os.path.join(config["csgo_dir"], "csgo_gc", "mm_reward.bin")


def _operation_bridge_paths(config: dict) -> list[str]:
    root = config["csgo_dir"]
    return [
        os.path.join(root, "revival_mission_select.log"),
        os.path.join(root, "csgo", "revival_mission_select.log"),
        os.path.join(root, "csgo_gc", "revival_mission_select.log"),
        os.path.join(root, "csgo", "csgo_gc", "revival_mission_select.log"),
    ]


def _write_operation_bridge(config: dict, marker: str) -> None:
    # csgo_gc uses std::ifstream with a process-relative path. Legacy Source
    # can change cwd during bootstrap, so write every harmless candidate rather
    # than guessing which directory is active at the exact click frame.
    for path in _operation_bridge_paths(config):
        try:
            _atomic_write_text(path, marker)
        except OSError:
            pass


def _console_log_paths(config: dict) -> list[str]:
    # Source writes console.log through the GAME filesystem. Legacy installs
    # normally land it under csgo\, but keep the root candidate too.
    return [
        os.path.join(config["csgo_dir"], "csgo", "console.log"),
        os.path.join(config["csgo_dir"], "console.log"),
    ]


def _read_latest_operation_selection(config: dict) -> tuple[int, int, int] | None:
    pattern = re.compile(r"REVIVAL_MISSION_SELECT_V1\s+(\d+)\s+(\d+)\s+(\d+)")
    newest: tuple[int, int, int] | None = None

    for path in _console_log_paths(config):
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                # Only the recent console tail matters and avoids repeatedly
                # scanning a multi-megabyte debug log.
                fh.seek(max(0, size - 262144))
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            continue

        for match in pattern.finditer(text):
            newest = tuple(int(match.group(i)) for i in (1, 2, 3))

    return newest


def _relay_operation_selection(config: dict,
                               last_selection: tuple[int, int, int] | None
                               ) -> tuple[int, int, int] | None:
    selection = _read_latest_operation_selection(config)
    if not selection or selection == last_selection:
        return last_selection

    season, card, quest = selection
    marker = f"REVIVAL_MISSION_SELECT_V1 {season} {card} {quest}\n"
    _write_operation_bridge(config, marker)
    print(
        f"[launcher] operation click captured: "
        f"season={season} card={card} quest={quest}"
    )
    return selection


def _wait_for_operation_selection_persist(
    config: dict,
    expected: tuple[int, int, int],
    timeout: float = 2.5,
) -> bool:
    _, expected_card, expected_quest = expected
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            with open(inventory_path(config), "rb") as fh:
                body = fh.read()
        except OSError:
            body = b""

        card, quest = _operation_selection_from_inventory_bytes(body)
        if card == expected_card and quest == expected_quest:
            print(
                f"[launcher] operation persisted before allocation: "
                f"mission_card={card} selected_quest={quest}"
            )
            return True

        time.sleep(0.05)

    try:
        with open(inventory_path(config), "rb") as fh:
            body = fh.read()
    except OSError:
        body = b""
    card, quest = _operation_selection_from_inventory_bytes(body)
    existing_bridge_paths = [
        p for p in _operation_bridge_paths(config) if os.path.exists(p)
    ]
    if existing_bridge_paths:
        print(
            "[launcher] bridge marker still exists at: "
            + ", ".join(existing_bridge_paths)
        )
    else:
        print(
            "[launcher] bridge marker was consumed by csgo_gc, "
            "so persistence was rejected after parsing."
        )

    # Surface the GC's own reason from the same condebug log Panorama uses.
    operation_lines: list[str] = []
    for log_path in _console_log_paths(config):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    low = line.lower()
                    if (
                        "[gc]" in low
                        and (
                            "operation" in low
                            or "mission" in low
                            or "quest" in low
                        )
                    ):
                        operation_lines.append(line.rstrip())
        except OSError:
            pass

    if operation_lines:
        print("[launcher] latest csgo_gc Operation lines:")
        for line in operation_lines[-20:]:
            print("    " + line)

    print(
        "[launcher] ERROR: mission click was captured but csgo_gc did not "
        f"persist it (expected card={expected_card} quest={expected_quest}, "
        f"got card={card} quest={quest}). Refusing to start an ordinary queue."
    )
    return False


def _write_binary_atomic(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


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
    # 9107 carries both a printable server address and a numeric direct UDP IP.
    # playit normally gives us a hostname, so resolve it here rather than inside
    # the injected GC DLL.
    state = dict(state)
    host = str(state.get("public_host") or "").strip()
    if host and not state.get("direct_udp_ip"):
        try:
            ip = socket.gethostbyname(host)
            state["direct_udp_ip"] = int.from_bytes(socket.inet_aton(ip), "big")
        except OSError as exc:
            print(f"[launcher] matchmaking: could not resolve {host}: {exc}")

    fields = [
        "state", "players_searching", "players_required", "server_online",
        "server_available", "match_id", "reservation_id", "map", "server_address",
        "public_host", "public_port", "direct_udp_ip", "game_type", "server_version",
        "server_id", "error", "last_match_id", "last_map",
        "live_ct_score", "live_t_score", "live_player_team",
        "live_rounds_won",
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

    result = state.get("result")
    if isinstance(result, dict):
        for key in ("reason", "ct_score", "t_score", "time_played",
                    "player_team", "won", "tied", "rounds_won"):
            if key not in result:
                continue
            value = result[key]
            if isinstance(value, bool):
                value = 1 if value else 0
            lines.append(f"result_{key}={value}")

    _atomic_write_text(_mm_state_path(config), "\n".join(lines) + "\n")


def matchmaking_bridge(config: dict, stop_event: threading.Event) -> None:
    base = config["server_url"].rstrip("/")
    request_path = _mm_request_path(config)
    last_request = ""
    searching = False
    last_poll = 0.0
    last_operation_selection: tuple[int, int, int] | None = None

    while not stop_event.wait(0.05):
        try:
            # Panorama prints the exact selected Riptide mission to console.log.
            # Relay that marker into a plain OS file the injected GC already
            # polls. This avoids relying on Source's virtual con_logfile path.
            last_operation_selection = _relay_operation_selection(
                config, last_operation_selection
            )
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
                    # If this queue came from an Operation click, do not allow
                    # allocation until the injected GC has actually persisted
                    # that exact card+quest. This prevents the old failure mode
                    # where a mission click silently became ordinary Competitive.
                    if last_operation_selection:
                        # Re-write once at queue time in case the GC consumed an
                        # earlier path before Panorama finished the click.
                        season, card, quest = last_operation_selection
                        _write_operation_bridge(
                            config,
                            f"REVIVAL_MISSION_SELECT_V1 {season} {card} {quest}\n",
                        )
                        if not _wait_for_operation_selection_persist(
                            config, last_operation_selection
                        ):
                            # Keep the backend completely untouched. The game's
                            # local queue UI can be cancelled/retried after the
                            # bridge problem is fixed; never allocate a wrong match.
                            continue

                    # Equip + Operation changes are persisted by the injected GC.
                    # Push that exact current inventory before allocation so the
                    # laptop receives the same selected mission.
                    upload_inventory(config)
                    state = _http_json(
                        "POST", base + "/matchmaking/start",
                        {
                            "steamid": config["steam_id"],
                            "game_type": int(request.get("game_type") or 8),
                            "client_version": int(request.get("client_version") or 0),
                            "map": str(request.get("map") or ""),
                        },
                    )
                    _write_mm_state(config, state)
                    searching = True
                    last_poll = 0.0
                    mission_map = str(request.get("map") or "")
                    if last_operation_selection:
                        _, selected_card, selected_quest = last_operation_selection
                        if mission_map:
                            print(
                                f"[launcher] matchmaking: joined mission queue "
                                f"card={selected_card} quest={selected_quest} "
                                f"map={mission_map}"
                            )
                        else:
                            print(
                                f"[launcher] matchmaking: joined mission queue "
                                f"card={selected_card} quest={selected_quest} "
                                "using shared Competitive pool"
                            )
                    elif mission_map:
                        print(f"[launcher] matchmaking: joined repeatable mission queue for {mission_map}")
                    else:
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

            # Match-end rewards must keep flowing after the queue state becomes
            # idle. Only fetch the next server packet once the DLL consumed the
            # previous local spool file.
            reward_path = _mm_reward_path(config)
            if not os.path.exists(reward_path):
                reward = _http_json(
                    "GET", base + "/matchmaking/reward/" + config["steam_id"]
                )
                payload_b64 = str(reward.get("payload_b64") or "")
                if payload_b64:
                    payload = base64.b64decode(payload_b64, validate=True)
                    _write_binary_atomic(reward_path, payload)
                    print(
                        f"[launcher] matchmaking: delivered {len(payload)}-byte "
                        "match-end reward packet"
                    )
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

    # Clear stale matchmaking/mission bridge state before this session.
    for path in (
        _mm_request_path(config),
        _mm_state_path(config),
        *_operation_bridge_paths(config),
    ):
        try:
            os.remove(path)
        except OSError:
            pass

    # Force a fresh Source console.log. popup_activate_mission.js already emits
    # REVIVAL_MISSION_SELECT_V1 through $.Msg; condebug makes that observable by
    # the launcher without another DLL/Panorama rebuild.
    for path in _console_log_paths(config):
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
    if "-condebug" not in args:
        args.append("-condebug")
    if "-conclearlog" not in args:
        args.append("-conclearlog")
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
