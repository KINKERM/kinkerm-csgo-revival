#!/usr/bin/env python3
"""Windows one-slot CS:GO Revival dedicated-server agent.

- No port forwarding is required. Run playit.gg separately (or set playit_exe)
  and put the public tunnel hostname/port in server_agent.json.
- The first queued human starts one 64-tick srcds.exe. Bots fill the empty
  10-player slots, and later queued humans join the same live server and replace
  bots. The slot is freed when the match ends.
- Standard library only.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "server_agent.json")

MAP_POOL = (
    "de_dust2", "de_mirage", "de_inferno", "de_nuke", "de_overpass",
    "de_vertigo", "de_train", "de_cache", "de_cbble", "de_ancient",
    "de_anubis", "de_tuscan", "de_canals", "de_breach", "de_basalt",
    "de_abbey", "de_austria", "de_biome", "de_blackgold", "de_chlorine",
    "de_engage", "de_grind", "de_lite", "de_mocha", "de_mutiny",
    "de_ruby", "de_seaside", "de_shipped", "de_studio", "de_subzero",
    "de_swamp", "de_thrill", "de_zoo",
    "cs_office", "cs_agency", "cs_italy", "cs_insertion", "cs_insertion2",
)

# Same hardcoded gscookieid used by the injected GC in CMsgCStrike15Welcome.
# Public/community CS:GO DS builds can report an empty 9106 after welcome and
# never turn our 9105 into a Valve-style queued reservation. Source's built-in
# R<pointer> fallback and the client GC both use this exact cookie.
REVIVAL_GAME_SERVER_COOKIE_ID = 0x293A206F6C6C6548
REVIVAL_AGENT_BUILD = "REVIVAL_AGENT_MATCH_FINAL_V22"

GAME_OVER_PATTERNS = (
    re.compile(r'World triggered "Game_Over"', re.I),
    re.compile(r'Game Over:', re.I),
    re.compile(r'Match_End', re.I),
)
TEAM_SCORE_RE = re.compile(r'Team "(CT|TERRORIST)" scored "(\d+)"', re.I)
STEAM2_RE = re.compile(r'STEAM_[0-5]:(\d):(\d+)', re.I)
STEAM3_RE = re.compile(r'\[U:1:(\d+)\]', re.I)
STEAM64_RE = re.compile(r'\b(7656119\d{10})\b')
PLAYER_TEAM_RE = re.compile(r'<(CT|TERRORIST)>', re.I)


def account_id_from_text(text: str) -> int:
    m = STEAM3_RE.search(text)
    if m:
        return int(m.group(1))
    m = STEAM2_RE.search(text)
    if m:
        return int(m.group(2)) * 2 + int(m.group(1))
    m = STEAM64_RE.search(text)
    if m:
        return int(m.group(1)) & 0xFFFFFFFF
    return 0


def account_id_from_log_line(line: str) -> int:
    if "entered the game" not in line.lower():
        return 0
    return account_id_from_text(line)


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"[agent] missing {CONFIG_PATH}")
        print("[agent] copy server_agent.example.json to server_agent.json and edit it.")
        raise SystemExit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
        cfg = json.load(fh)
    for key in ("backend_url", "csgo_dir", "public_host"):
        if not str(cfg.get(key, "")).strip():
            raise SystemExit(f"[agent] '{key}' is required in server_agent.json")
    cfg.setdefault("agent_id", "revival-laptop-1")
    cfg.setdefault("public_port", 27015)
    cfg.setdefault("local_port", 27015)
    cfg.setdefault("playit_exe", "")
    cfg.setdefault("steam_account_token", "")
    cfg.setdefault("extra_srcds_args", "")
    cfg.setdefault("accept_timeout_seconds", 300)
    cfg["accept_timeout_seconds"] = max(300.0, float(cfg.get("accept_timeout_seconds", 300)))
    cfg.setdefault("post_match_grace_seconds", 35)
    return cfg


def write_srcds_crash_report(cfg: dict, exit_code: int) -> None:
    """Persist enough diagnostics to debug a disappearing SRCDS console."""
    try:
        out_dir = os.path.join(HERE, "crash-reports")
        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(out_dir, f"srcds-crash-{stamp}.txt")
        lines = [
            f"timestamp={time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"exit_code={exit_code}",
            f"exit_code_hex=0x{(exit_code & 0xFFFFFFFF):08X}",
            f"csgo_dir={cfg.get('csgo_dir', '')}",
            "",
        ]

        # Capture the GC fatal text and Source console log as well. The
        # compatible Win32 GC's Platform::Error exits the process with code 1,
        # so Windows Error Reporting may legitimately have no crash event.
        for diag_path in (
            os.path.join(cfg["csgo_dir"], "gc_fatal.txt"),
            os.path.join(cfg["csgo_dir"], "gc_log.txt"),
            os.path.join(cfg["csgo_dir"], "csgo", "console.log"),
        ):
            try:
                if os.path.isfile(diag_path):
                    lines.append(f"===== {os.path.basename(diag_path)} =====")
                    with open(diag_path, "rb") as fh:
                        data = fh.read()
                    lines.append(data[-131072:].decode("utf-8", errors="replace"))
                    lines.append(f"===== END {os.path.basename(diag_path)} =====")
            except Exception as exc:
                lines.append(f"diagnostic_capture_error[{diag_path}]={exc}")

        logs_dir = os.path.join(cfg["csgo_dir"], "csgo", "logs")
        try:
            candidates = [
                os.path.join(logs_dir, name)
                for name in os.listdir(logs_dir)
                if name.lower().endswith(".log")
            ]
            candidates = [p for p in candidates if os.path.isfile(p)]
            if candidates:
                newest = max(candidates, key=os.path.getmtime)
                lines.append(f"source_log={newest}")
                with open(newest, "rb") as fh:
                    data = fh.read()
                tail = data[-65536:].decode("utf-8", errors="replace")
                lines.append("===== LAST SOURCE LOG BYTES =====")
                lines.append(tail)
                lines.append("===== END SOURCE LOG =====")
        except Exception as exc:
            lines.append(f"source_log_capture_error={exc}")

        if os.name == "nt":
            try:
                ps = (
                    "$ErrorActionPreference='SilentlyContinue';"
                    "$since=(Get-Date).AddMinutes(-5);"
                    "Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=$since} | "
                    "Where-Object { $_.ProviderName -in @('Application Error','Windows Error Reporting') "
                    "-and $_.Message -match 'srcds\\.exe' } | "
                    "Select-Object -First 8 TimeCreated,Id,ProviderName,Message | Format-List | Out-String"
                )
                event = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", ps],
                    capture_output=True, text=True, timeout=12,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                lines.append("===== WINDOWS APPLICATION CRASH EVENTS =====")
                lines.append(event.stdout or "(none found)")
                if event.stderr:
                    lines.append(event.stderr)
                lines.append("===== END WINDOWS EVENTS =====")
            except Exception as exc:
                lines.append(f"windows_event_capture_error={exc}")

        with open(out_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write("\n".join(lines))
        print(f"[agent] SRCDS CRASH REPORT SAVED: {out_path}")
    except Exception as exc:
        print(f"[agent] failed to save SRCDS crash report: {exc}")


def post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "csgo-revival-gameserver/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def flush_server_reward_bridge(cfg: dict) -> None:
    reward_dir = os.path.join(cfg["csgo_dir"], "csgo_gc", "server_rewards")
    os.makedirs(reward_dir, exist_ok=True)
    try:
        names = sorted(os.listdir(reward_dir))
    except OSError:
        return

    base = cfg["backend_url"].rstrip("/")
    for name in names:
        if not name.lower().endswith(".bin"):
            continue
        stem = name[:-4]
        match = re.match(r"^(\d+)(?:[_.-].*)?$", stem)
        if not match:
            continue
        steamid = match.group(1)
        path = os.path.join(reward_dir, name)
        try:
            with open(path, "rb") as fh:
                payload = fh.read()
            if not payload:
                os.remove(path)
                continue
            response = post_json(
                base + "/matchmaking/server/reward",
                {
                    "steamid": steamid,
                    "payload_b64": base64.b64encode(payload).decode("ascii"),
                },
            )
            if response.get("ok"):
                os.remove(path)
                print(
                    f"[agent] relayed match-end reward packet for {steamid} "
                    f"({len(payload)} bytes, {name})"
                )
        except Exception as exc:
            print(f"[agent] reward relay retry for {steamid}: {exc}")


def sync_server_player_inventories(
    cfg: dict, assignment: dict, clear_existing: bool = False
) -> None:
    cache_dir = os.path.join(cfg["csgo_dir"], "csgo_gc", "server_players")
    os.makedirs(cache_dir, exist_ok=True)

    if clear_existing:
        try:
            for name in os.listdir(cache_dir):
                if name.lower().endswith(".txt"):
                    try:
                        os.remove(os.path.join(cache_dir, name))
                    except OSError:
                        pass
        except OSError:
            pass

    steamids = [
        str(x).strip()
        for x in assignment.get("steamids", [])
        if str(x).strip().isdigit()
    ]
    base = cfg["backend_url"].rstrip("/")

    for steamid in steamids:
        url = base + "/inventory/" + steamid
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "csgo-revival-gameserver/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read()
            dest = os.path.join(cache_dir, steamid + ".txt")
            tmp = dest + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(body)
            os.replace(tmp, dest)
            print(
                f"[agent] cached equipped inventory source for {steamid} "
                f"({len(body)} bytes)"
            )
        except Exception as exc:
            print(f"[agent] failed to cache inventory for {steamid}: {exc}")


def find_srcds(csgo_dir: str) -> str:
    candidates = (
        os.path.join(csgo_dir, "srcds.exe"),
        os.path.join(csgo_dir, "bin", "srcds.exe"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "srcds.exe was not found. The normal legacy install sometimes includes it; "
        "if yours does not, install CS:GO Dedicated Server (SteamCMD app 740) and "
        "set csgo_dir to that folder."
    )


def installed_maps(csgo_dir: str) -> list[str]:
    maps_dir = os.path.join(csgo_dir, "csgo", "maps")
    if not os.path.isdir(maps_dir):
        return []

    # Single huge queue: every top-level defuse/hostage BSP installed on the
    # laptop is eligible. Keep the known pool first for stable logs, then append
    # any other preserved de_/cs_ maps automatically.
    found = {
        name[:-4]
        for name in os.listdir(maps_dir)
        if name.lower().endswith(".bsp")
        and name[:-4].lower().startswith(("de_", "cs_"))
        and not name[:-4].lower().endswith(("_se", "_ve"))
    }
    ordered = [m for m in MAP_POOL if m in found]
    ordered.extend(sorted(found.difference(ordered)))
    return ordered


def ensure_match_cfg(csgo_dir: str, steam_account_token: str = "") -> None:
    cfg_dir = os.path.join(csgo_dir, "csgo", "cfg")
    os.makedirs(cfg_dir, exist_ok=True)
    token = str(steam_account_token or "").replace('"', '').strip()

    # Early process/server settings. Gameplay cvars placed here are overwritten
    # by Host_NewGame/gamemode_competitive.cfg, so keep this file intentionally
    # small.
    early_path = os.path.join(cfg_dir, "revival_competitive.cfg")
    early = r"""hostname "Kinkerm CS:GO Revival Competitive"
sv_lan 0
sv_password ""
sv_cheats 0
sv_pure 0
sv_allow_votes 1
sv_hibernate_when_empty 0
sv_hibernate_postgame_delay 5
sv_mmqueue_reservation_timeout 600
sv_setsteamaccount "__REVIVAL_STEAM_TOKEN__"
log on
"""
    early = early.replace("__REVIVAL_STEAM_TOKEN__", token)
    with open(early_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(early)

    # CS:GO loads this AFTER gamemode_competitive.cfg. The server log explicitly
    # attempts this file on every competitive map, making it the correct final
    # override point for the revival's matchmaking runtime.
    late_path = os.path.join(cfg_dir, "gamemode_competitive_server.cfg")
    late = r"""// CS:GO Revival - final matchmaking overrides
sv_competitive_official_5v5 1
sv_mmqueue_reservation_timeout 600

bot_quota 10
bot_quota_mode fill
bot_join_after_player 1
bot_auto_vacate 1
bot_join_team any
bot_stop 0
bot_freeze 0
bot_dont_shoot 0

mp_autokick 0
mp_autoteambalance 0
mp_limitteams 0
mp_friendlyfire 1
mp_maxrounds 30
mp_winlimit 0
mp_halftime 1
mp_overtime_enable 1
mp_overtime_maxrounds 6
mp_match_can_clinch 1
mp_ignore_round_win_conditions 0
mp_timelimit 0
mp_startmoney 800
mp_maxmoney 16000
mp_buytime 20
mp_buy_anywhere 0
mp_freezetime 15
mp_roundtime 1.92
mp_roundtime_defuse 1.92
mp_roundtime_hostage 1.92
mp_match_restart_delay 15
mp_competitive_endofmatch_extra_time 20
mp_endmatch_votenextmap 0
mp_match_end_restart 0

// Hold normal matchmaking warmup until the reserved human is actually present.
// The agent ends it immediately once status/logs confirm that player.
mp_do_warmup_period 1
mp_warmuptime 15
mp_warmuptime_all_players_connected 5
mp_warmup_pausetimer 0

echo "[REVIVAL] gamemode_competitive_server.cfg applied"
"""
    with open(late_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(late)

    print(f"[agent] wrote late competitive override: {late_path}")

def reservation_paths(csgo_dir: str) -> tuple[str, str]:
    gc_dir = os.path.join(csgo_dir, "csgo_gc")
    os.makedirs(gc_dir, exist_ok=True)
    return (
        os.path.join(gc_dir, "server_reservation.txt"),
        os.path.join(gc_dir, "server_reservation_response.txt"),
    )


def server_auth_dir(csgo_dir: str) -> str:
    return os.path.join(csgo_dir, "csgo_gc", "server_auth")


def clear_server_auth_markers(csgo_dir: str) -> None:
    path = server_auth_dir(csgo_dir)
    os.makedirs(path, exist_ok=True)
    try:
        for name in os.listdir(path):
            if name.lower().endswith(".txt"):
                try:
                    os.remove(os.path.join(path, name))
                except OSError:
                    pass
    except OSError:
        pass


def engine_reservation_ready_path(csgo_dir: str) -> str:
    return os.path.join(csgo_dir, "csgo_gc", "engine_reservation_ready.txt")


def engine_reservation_is_ready(csgo_dir: str, match_id: int) -> bool:
    try:
        with open(
            engine_reservation_ready_path(csgo_dir),
            "r",
            encoding="utf-8",
            errors="replace",
        ) as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("match_id="):
                    try:
                        return int(line.split("=", 1)[1]) == int(match_id)
                    except ValueError:
                        return False
    except OSError:
        pass
    return False


def read_csgo_server_version(csgo_dir: str) -> int:
    """Read Source's dedicated ServerVersion from steam.inf.

    This is NOT the same number as the client/build version reported by the
    matchmaking request. Source compares reservations against GetServerVersion(),
    which is populated from ServerVersion= in steam.inf.
    """
    candidates = (
        os.path.join(csgo_dir, "csgo", "steam.inf"),
        os.path.join(csgo_dir, "steam.inf"),
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line.lower().startswith("serverversion="):
                        continue
                    value = line.split("=", 1)[1].strip()
                    version = int(value)
                    if version > 0:
                        return version
        except (OSError, ValueError):
            continue
    return 0


def write_native_reservation(
    csgo_dir: str, assignment: dict, *, clear_response: bool = True
) -> None:
    request_path, response_path = reservation_paths(csgo_dir)
    if clear_response:
        try:
            os.remove(response_path)
        except OSError:
            pass
        try:
            os.remove(engine_reservation_ready_path(csgo_dir))
        except OSError:
            pass

    account_ids = [
        int(x) for x in assignment.get("account_ids", []) if int(x) > 0
    ]
    # GC reservation game_type is the matchmaking bitfield, not Source's
    # +game_type convar. Competitive reservations use mode bits == 8.
    server_version = read_csgo_server_version(csgo_dir)
    if not server_version:
        # Zero is safer than copying client_version: Source treats these as
        # different version domains. A nonzero wrong value causes reservation
        # rejection.
        print("[agent] WARNING: could not read ServerVersion from steam.inf; "
              "writing server_version=0")

    reservation_game_type = int(assignment.get("game_type") or 8)
    lines = [
        f"match_id={int(assignment.get('match_id') or 0)}",
        f"game_type={reservation_game_type}",
        f"server_version={server_version}",
        "account_ids=" + ",".join(str(x) for x in account_ids),
    ]
    tmp = request_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, request_path)


def read_native_reservation_response(csgo_dir: str) -> dict[str, int | str]:
    _, response_path = reservation_paths(csgo_dir)
    out: dict[str, int | str] = {}
    try:
        with open(response_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key in ("match_id", "reservation_id", "server_id"):
                    try:
                        out[key] = int(value)
                    except ValueError:
                        pass
                else:
                    out[key] = value
    except OSError:
        pass
    return out



def _rcon_packet(request_id: int, packet_type: int, text: str) -> bytes:
    body = struct.pack("<ii", request_id, packet_type)
    body += text.encode("utf-8", errors="replace") + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("RCON socket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv_rcon(sock: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", _recv_exact(sock, 4))[0]
    if size < 10 or size > 1024 * 1024:
        raise ValueError(f"invalid RCON packet size {size}")
    data = _recv_exact(sock, size)
    request_id, packet_type = struct.unpack("<ii", data[:8])
    text = data[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, text


def send_local_rcon(port: int, password: str, command: str) -> str:
    """Send one command to Source and collect response packets until idle."""
    with socket.create_connection(("127.0.0.1", int(port)), timeout=3.0) as sock:
        sock.settimeout(3.0)
        sock.sendall(_rcon_packet(101, 3, password))  # SERVERDATA_AUTH

        authed = False
        for _ in range(4):
            request_id, packet_type, _ = _recv_rcon(sock)
            if packet_type == 2:  # SERVERDATA_AUTH_RESPONSE
                if request_id == -1:
                    raise PermissionError("RCON authentication failed")
                authed = True
                break
        if not authed:
            raise ConnectionError("RCON authentication response not received")

        command_id = 102
        sentinel_id = 103
        sock.sendall(_rcon_packet(command_id, 2, command))
        sock.sendall(_rcon_packet(sentinel_id, 2, "echo REVIVAL_RCON_DONE_103"))

        chunks: list[str] = []
        sock.settimeout(1.50)
        while True:
            try:
                request_id, packet_type, text = _recv_rcon(sock)
            except socket.timeout:
                break
            except ConnectionError:
                break
            if request_id == command_id:
                chunks.append(text)
            elif request_id == sentinel_id:
                break
        return "".join(chunks)

def set_above_normal(proc: subprocess.Popen) -> None:
    if os.name != "nt":
        return
    try:
        ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
        ctypes.windll.kernel32.SetPriorityClass(int(proc._handle), ABOVE_NORMAL_PRIORITY_CLASS)
    except Exception as exc:
        print(f"[agent] could not raise srcds priority: {exc}")


class ServerSlot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.match_id = 0
        self.ready_match_id = 0
        self.reservation_id = 0
        self.server_id = 0
        self.reserved_account_ids: set[int] = set()
        self.ct_score = 0
        self.t_score = 0
        self.expected_account_ids: set[int] = set()
        self.connected_account_ids: set[int] = set()
        self.player_teams: dict[int, str] = {}
        self.match_play_started_at = 0.0
        self.ready_at = 0.0
        self.source_match_started_at = 0.0
        self.using_cookie_fallback = False
        self.started = False
        self.runtime_applied = False
        self.runtime_guard_at = 0.0
        self.human_presence_seen = False
        self._lock = threading.RLock()
        self._ended = False
        self.rcon_password = secrets.token_hex(16)
        self.log_started_at = 0.0
        self.log_files_before: set[str] = set()
        self.assignment_missing_since = 0.0
        self.launched_at = 0.0
        self.intentional_stop = False

    def alive(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def start(self, assignment: dict) -> None:
        match_id = int(assignment.get("match_id") or 0)
        if not match_id:
            return
        with self._lock:
            if self.alive() and self.match_id == match_id:
                self.assignment_missing_since = 0.0
                new_accounts = {
                    int(x) for x in assignment.get("account_ids", []) if int(x) > 0
                }
                added = new_accounts.difference(self.expected_account_ids)
                self.expected_account_ids.update(new_accounts)
                if added:
                    sync_server_player_inventories(
                        self.cfg, assignment, clear_existing=False
                    )
                    # Keep the request file current for builds that do support
                    # native 9105 refreshes. In cookie-fallback mode the HTTP
                    # coordinator is the membership authority.
                    write_native_reservation(
                        self.cfg["csgo_dir"], assignment, clear_response=False
                    )
                    if self.using_cookie_fallback:
                        self.reserved_account_ids.update(added)
                    print(
                        "[agent] drop-in player(s) added to live match "
                        f"{match_id}: {', '.join(str(x) for x in sorted(added))}"
                    )
                return
            self.stop()

            map_name = str(assignment.get("map") or "de_dust2")
            srcds = find_srcds(self.cfg["csgo_dir"])
            ensure_match_cfg(
                self.cfg["csgo_dir"],
                self.cfg.get("steam_account_token", ""),
            )
            sync_server_player_inventories(
                self.cfg, assignment, clear_existing=True
            )
            clear_server_auth_markers(self.cfg["csgo_dir"])
            try:
                os.remove(os.path.join(
                    self.cfg["csgo_dir"], "csgo_gc", "server_match_end_trigger.txt"
                ))
            except OSError:
                pass
            os.makedirs(
                os.path.join(self.cfg["csgo_dir"], "csgo_gc", "server_rewards"),
                exist_ok=True,
            )
            write_native_reservation(self.cfg["csgo_dir"], assignment)
            server_version = read_csgo_server_version(self.cfg["csgo_dir"])
            print(f"[agent] reservation ServerVersion={server_version or 0}")
            cmd = [
                srcds,
                "-game", "csgo",
                "-console",
                "-condebug",
                "-conclearlog",
                "-usercon",
                "-secure",
                "-tickrate", "64",
                "-port", str(int(self.cfg["local_port"])),
                "-maxplayers_override", "10",
                "+game_type", "0",
                "+game_mode", "1",
                "+sv_mmqueue_reservation_timeout", "600",
                "+map", map_name,
                "+exec", "revival_competitive.cfg",
                "+rcon_password", self.rcon_password,
            ]
            extra = str(self.cfg.get("extra_srcds_args") or "").strip()
            if extra:
                cmd.extend(extra.split())

            logs_dir = os.path.join(self.cfg["csgo_dir"], "csgo", "logs")
            os.makedirs(logs_dir, exist_ok=True)
            try:
                self.log_files_before = set(os.listdir(logs_dir))
            except OSError:
                self.log_files_before = set()
            self.log_started_at = time.time()

            # Clear prior-run diagnostics so any captured fatal belongs to
            # this allocation only.
            for diag_path in (
                os.path.join(self.cfg["csgo_dir"], "gc_fatal.txt"),
                os.path.join(self.cfg["csgo_dir"], "gc_log.txt"),
                os.path.join(self.cfg["csgo_dir"], "csgo", "console.log"),
            ):
                try:
                    os.remove(diag_path)
                except OSError:
                    pass

            print(f"[agent] starting match {match_id} on {map_name} @ 64 tick")
            if os.name == "nt":
                creationflags = (
                    subprocess.CREATE_NEW_CONSOLE
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                creationflags = 0
            self.proc = subprocess.Popen(
                cmd,
                cwd=self.cfg["csgo_dir"],
                # Source's CTextConsoleWin32 requires genuine console handles.
                # CREATE_NEW_CONSOLE + no stdio redirection avoids the
                # GetNumberOfConsoleInputEvents crash.
                stdin=None,
                stdout=None,
                stderr=None,
                creationflags=creationflags,
            )
            set_above_normal(self.proc)
            self.match_id = match_id
            self.ready_match_id = 0
            self.reservation_id = 0
            self.server_id = 0
            self.reserved_account_ids.clear()
            self.ct_score = 0
            self.t_score = 0
            self.expected_account_ids = {
                int(x) for x in assignment.get("account_ids", []) if int(x) > 0
            }
            self.connected_account_ids.clear()
            self.player_teams.clear()
            self.match_play_started_at = 0.0
            self.ready_at = 0.0
            self.source_match_started_at = 0.0
            self.using_cookie_fallback = False
            self.started = False
            self.runtime_applied = False
            self.runtime_guard_at = 0.0
            self.human_presence_seen = False
            self._ended = False
            self.assignment_missing_since = 0.0
            self.launched_at = time.monotonic()
            self.intentional_stop = False
            threading.Thread(target=self._reader, daemon=True, name="srcds-output").start()
            threading.Thread(target=self._mark_ready_after_boot, daemon=True, name="srcds-ready").start()

    def _mark_ready_after_boot(self) -> None:
        # Prefer a populated native 9106 when a server build provides one.
        # The public/community CS:GO DS reports an empty 9106 after GC welcome
        # and does not create a Valve-style queued reservation from our 9105.
        # Once Source has reached Match_Start, use the exact gscookieid already
        # installed into the engine by CMsgCStrike15Welcome.
        deadline = time.monotonic() + 80.0
        while time.monotonic() < deadline:
            time.sleep(0.5)
            with self._lock:
                if not self.alive() or not self.match_id:
                    return
                match_id = self.match_id
                source_started_at = self.source_match_started_at

            response = read_native_reservation_response(self.cfg["csgo_dir"])
            engine_ready = engine_reservation_is_ready(
                self.cfg["csgo_dir"], match_id
            )
            if (
                engine_ready
                and int(response.get("match_id") or 0) == match_id
                and int(response.get("reservation_id") or 0) > 0
            ):
                time.sleep(1.5)
                with self._lock:
                    if not self.alive() or self.match_id != match_id:
                        return
                    self.reservation_id = int(response["reservation_id"])
                    self.server_id = int(response.get("server_id") or 0)
                    self.reserved_account_ids = {
                        int(x) for x in str(response.get("account_ids") or "").split(",")
                        if x.strip().isdigit() and int(x) > 0
                    }
                    self.ready_match_id = match_id
                    self.ready_at = time.monotonic()
                    self.using_cookie_fallback = False
                    print(
                        f"[agent] native 9106 accepted match {match_id}; "
                        f"reservation={self.reservation_id}; server_id={self.server_id or 'direct-udp'}; waiting for "
                        "first human to enter (bots fill empty slots)"
                    )
                return

            # Public/community Legacy DS can log the empty-9106 cookie
            # fallback in the SRCDS console without leaving a response file
            # visible to this Python process. The server has already received
            # this exact cookie through GCServerWelcome, so once Source has
            # reached Match_Start we can safely mirror the same authoritative
            # cookie into the HTTP coordinator and continue over direct UDP.
            if (
                engine_ready
                and source_started_at
                and time.monotonic() - source_started_at >= 2.5
            ):
                with self._lock:
                    if not self.alive() or self.match_id != match_id:
                        return
                    self.reservation_id = REVIVAL_GAME_SERVER_COOKIE_ID
                    self.server_id = 0
                    self.reserved_account_ids = set(self.expected_account_ids)
                    self.ready_match_id = match_id
                    self.ready_at = time.monotonic()
                    self.using_cookie_fallback = True
                    print(
                        f"[agent] GC welcome cookie fallback accepted match {match_id}; "
                        f"reservation={self.reservation_id}; server_id=direct-udp; waiting for "
                        "first human to enter (bots fill empty slots)"
                    )
                return

        if self.alive():
            print(
                "[agent] srcds never reached a usable matchmaking-ready state "
                "(engine Q reservation was not confirmed)"
            )
        else:
            print("[agent] srcds exited before matchmaking became ready")

    def _handle_server_log_line(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        print("[srcds-log] " + line)

        if 'triggered "Match_Start"' in line:
            with self._lock:
                if not self.source_match_started_at:
                    self.source_match_started_at = time.monotonic()

        m = TEAM_SCORE_RE.search(line)
        if m:
            score = int(m.group(2))
            if m.group(1).upper() == "CT":
                self.ct_score = score
            else:
                self.t_score = score

        seen_account_id = account_id_from_text(line)
        if seen_account_id:
            team_match = PLAYER_TEAM_RE.search(line)
            if team_match:
                team = team_match.group(1).upper()
                if team in ("CT", "TERRORIST"):
                    with self._lock:
                        self.player_teams[seen_account_id] = team

            with self._lock:
                expected = (
                    not self.expected_account_ids
                    or seen_account_id in self.expected_account_ids
                )
                if expected:
                    self.human_presence_seen = True

            # "connected" can happen while the client is still loading for
            # 10-30 seconds. Start Competitive only at Source's authoritative
            # entered-game event.
            if "entered the game" in line.lower():
                self._player_entered(seen_account_id)

        if any(p.search(line) for p in GAME_OVER_PATTERNS):
            self._report_end_once(
                "game_over",
                grace=float(self.cfg.get("post_match_grace_seconds", 25)),
            )

    def _reader(self) -> None:
        """Tail Source's normal L*.log files; srcds owns a real Win32 console."""
        proc = self.proc
        if proc is None:
            return

        logs_dir = os.path.join(self.cfg["csgo_dir"], "csgo", "logs")
        current_path = ""
        position = 0

        def newest_match_log() -> str:
            try:
                candidates = []
                for name in os.listdir(logs_dir):
                    if not re.match(r"^L.*\.log$", name, re.I):
                        continue
                    path = os.path.join(logs_dir, name)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if (
                        name not in self.log_files_before
                        or mtime >= self.log_started_at - 2.0
                    ):
                        candidates.append((mtime, path))
                if not candidates:
                    return ""
                candidates.sort()
                return candidates[-1][1]
            except OSError:
                return ""

        def drain() -> None:
            nonlocal current_path, position
            path = newest_match_log()
            if not path:
                return
            if path != current_path:
                current_path = path
                position = 0
                print(f"[agent] reading Source match log: {os.path.basename(path)}")
            try:
                # Track a byte offset explicitly. TextIO iteration + tell() is
                # unreliable on growing Windows log files and could silently
                # kill/rewind the old tailer before the human join line arrived.
                with open(path, "rb") as fh:
                    fh.seek(position)
                    while True:
                        raw = fh.readline()
                        if not raw:
                            break
                        position = fh.tell()
                        self._handle_server_log_line(
                            raw.decode("utf-8", errors="replace")
                        )
            except Exception as exc:
                print(f"[agent] Source log tail error: {exc}")

        while proc.poll() is None:
            drain()
            time.sleep(0.20)

        drain()
        code = proc.wait()
        # A disappeared SRCDS is diagnostic-worthy regardless of exit code.
        # Only suppress the report when ServerSlot.stop() explicitly asked it
        # to exit.
        if not self.intentional_stop:
            write_srcds_crash_report(self.cfg, code)
        if not self._ended and self.match_id:
            self._report_end_once(f"srcds_exit_{code}")

    def _player_entered(self, account_id: int) -> None:
        with self._lock:
            if self._ended or not self.match_id:
                return
            if self.expected_account_ids and account_id not in self.expected_account_ids:
                return
            self.human_presence_seen = True
            before = len(self.connected_account_ids)
            self.connected_account_ids.add(account_id)
            if len(self.connected_account_ids) != before:
                print(f"[agent] accepted player entered: {account_id} "
                      f"({len(self.connected_account_ids)}/{len(self.expected_account_ids)})")
            should_start = (
                not self.started
                and len(self.connected_account_ids) >= 1
            )

        if should_start:
            self._begin_match()

    def _begin_match(self) -> None:
        with self._lock:
            if self.runtime_applied or self._ended or not self.match_id:
                return
            match_id = self.match_id
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return
            port = int(self.cfg["local_port"])
            password = self.rcon_password

        try:
            # Reassert only the small set that matters at the human-join edge.
            # The full baseline lives in gamemode_competitive_server.cfg and is
            # already loaded after Valve's competitive config.
            send_local_rcon(
                port,
                password,
                (
                    "sv_competitive_official_5v5 1; "
                    "bot_stop 0; bot_freeze 0; bot_dont_shoot 0; "
                    "bot_join_after_player 1; bot_auto_vacate 1; bot_join_team any; "
                    "bot_quota_mode fill; bot_quota 10; "
                    "mp_autokick 0; mp_autoteambalance 0; mp_limitteams 0; "
                    "mp_friendlyfire 1; mp_maxrounds 30; mp_winlimit 0; "
                    "mp_halftime 1; mp_overtime_enable 1; mp_overtime_maxrounds 6; "
                    "mp_match_can_clinch 1; mp_ignore_round_win_conditions 0; "
                    "mp_timelimit 0; mp_match_restart_delay 15; "
                    "mp_competitive_endofmatch_extra_time 20; "
                    "mp_endmatch_votenextmap 0; mp_match_end_restart 0; "
                    "mp_warmup_pausetimer 0; mp_warmup_end"
                ),
            )
            proof = send_local_rcon(
                port,
                password,
                (
                    "sv_competitive_official_5v5; "
                    "bot_quota; bot_quota_mode; bot_join_after_player; "
                    "bot_stop; bot_freeze; mp_maxrounds; mp_winlimit; "
                    "mp_timelimit; mp_match_can_clinch; mp_halftime; "
                    "mp_overtime_enable; mp_friendlyfire; "
                    "mp_warmuptime_all_players_connected; mp_warmup_pausetimer"
                ),
            )
        except Exception as exc:
            print(f"[agent] Competitive RCON apply failed; retrying: {exc}")
            return

        with self._lock:
            if self._ended or self.match_id != match_id or self.runtime_applied:
                return
            self.runtime_applied = True
            if not self.started:
                self.started = True
                self.match_play_started_at = time.monotonic()

        print("[agent] Competitive runtime applied; warmup ended; 5v5 bot fill enabled")
        if proof.strip():
            print("[agent] Competitive cvar proof: " + proof.replace("\n", " | ").strip())

        try:
            post_json(
                self.cfg["backend_url"].rstrip("/") + "/matchmaking/server/started",
                {"match_id": match_id},
            )
        except Exception as exc:
            print(f"[agent] start notification will retry via heartbeat: {exc}")
        print(f"[agent] first human present; bot-filled match {match_id} started")

    def refresh_native_reservation_response(self) -> None:
        with self._lock:
            match_id = self.match_id
            old_reservation = self.reservation_id
            if not match_id or not self.alive():
                return

        if not engine_reservation_is_ready(self.cfg["csgo_dir"], match_id):
            return

        response = read_native_reservation_response(self.cfg["csgo_dir"])
        if int(response.get("match_id") or 0) != match_id:
            return
        new_reservation = int(response.get("reservation_id") or 0)
        new_server_id = int(response.get("server_id") or 0)
        if not new_reservation:
            return

        acknowledged = {
            int(x) for x in str(response.get("account_ids") or "").split(",")
            if x.strip().isdigit() and int(x) > 0
        }

        with self._lock:
            if self.match_id != match_id:
                return
            membership_changed = acknowledged != self.reserved_account_ids
            self.reserved_account_ids = acknowledged
            self.reservation_id = new_reservation
            self.server_id = new_server_id
            self.ready_match_id = match_id
            self.using_cookie_fallback = False
            if new_reservation != old_reservation or membership_changed:
                print(
                    f"[agent] native reservation refreshed for match {match_id}: "
                    f"reservation={new_reservation}, server_id={new_server_id or 'direct-udp'}, "
                    f"accounts={','.join(str(x) for x in sorted(acknowledged))}"
                )

    def refresh_authenticated_players(self) -> None:
        with self._lock:
            if self._ended or not self.match_id or not self.alive():
                return
            expected = set(self.expected_account_ids)
        if not expected:
            return

        auth_dir = server_auth_dir(self.cfg["csgo_dir"])
        for account_id in sorted(expected):
            marker = os.path.join(auth_dir, f"{account_id}.txt")
            if not os.path.isfile(marker):
                continue

            with self._lock:
                first = not self.human_presence_seen
                self.human_presence_seen = True
                should_mark_started = not self.started
                match_id = self.match_id
                if should_mark_started:
                    self.started = True
                    self.match_play_started_at = time.monotonic()

            if first:
                print(
                    f"[agent] authoritative Source auth detected for reserved "
                    f"account {account_id}; pre-join timeout disabled"
                )

            if should_mark_started:
                try:
                    post_json(
                        self.cfg["backend_url"].rstrip("/") + "/matchmaking/server/started",
                        {"match_id": match_id},
                    )
                except Exception as exc:
                    print(f"[agent] start notification retry via heartbeat: {exc}")
                print(
                    f"[agent] Source authenticated reserved human; match "
                    f"{match_id} marked active"
                )

    def refresh_connected_players_via_rcon(self) -> None:
        with self._lock:
            if self._ended or self.started or not self.match_id or not self.alive():
                return
            expected = set(self.expected_account_ids)
        if not expected:
            return

        try:
            status = send_local_rcon(
                int(self.cfg["local_port"]),
                self.rcon_password,
                "status",
            )
        except Exception as exc:
            now = time.monotonic()
            last = getattr(self, "_last_status_error_log", 0.0)
            if now - last >= 10.0:
                print(f"[agent] RCON status check failed: {exc}")
                self._last_status_error_log = now
            return

        found: set[int] = set()
        active: set[int] = set()
        saw_any_human = False
        for raw in status.splitlines():
            upper = raw.upper()
            if "STEAM_" in upper or "[U:1:" in upper or "7656119" in raw:
                if "BOT" not in upper and "HLTV" not in upper:
                    saw_any_human = True
            account_id = account_id_from_text(raw)
            if account_id and account_id in expected:
                found.add(account_id)
                if re.search(r"\bactive\b", raw, re.I):
                    active.add(account_id)

        if found:
            with self._lock:
                self.human_presence_seen = True

        for account_id in sorted(active):
            self._player_entered(account_id)

        # Private one-match server: an unparsed real Steam player is enough to
        # protect the allocation from cleanup, but not enough to start rounds.
        if saw_any_human and not found:
            with self._lock:
                first_fallback = not self.human_presence_seen
                self.human_presence_seen = True
            if first_fallback:
                print("[agent] RCON status shows a human player; preserving active reservation")

    def check_accept_timeout(self) -> None:
        # Do NOT independently kill a native reservation on a wall-clock timer.
        # The coordinator owns cancellation/withdrawal. Previous builds could
        # destroy a live Competitive server after five minutes when join
        # detection missed the player even though Source had accepted them.
        with self._lock:
            if (
                self._ended
                or self.started
                or self.human_presence_seen
                or bool(self.connected_account_ids)
                or not self.ready_at
                or not self.match_id
            ):
                return
            timeout = float(self.cfg.get("accept_timeout_seconds", 300))
            if time.monotonic() - self.ready_at < timeout:
                return
            match_id = self.match_id
            # Log once, then disable this local timer. The heartbeat assignment
            # remains authoritative and explicit cancellation still stops srcds.
            self.ready_at = 0.0
        print(
            f"[agent] pre-join timer reached for match {match_id}; "
            "keeping reservation alive until coordinator withdraws it"
        )

    def enforce_competitive_runtime(self) -> None:
        with self._lock:
            if (
                self._ended
                or not self.started
                or not self.match_id
                or self.proc is None
                or self.proc.poll() is not None
            ):
                return
            now = time.monotonic()
            if now - self.runtime_guard_at < 10.0:
                return
            self.runtime_guard_at = now
            port = int(self.cfg["local_port"])
            password = self.rcon_password

        try:
            send_local_rcon(
                port,
                password,
                (
                    "sv_competitive_official_5v5 1; "
                    "mp_maxrounds 30; mp_winlimit 0; mp_timelimit 0; "
                    "mp_halftime 1; mp_overtime_enable 1; mp_overtime_maxrounds 6; "
                    "mp_match_can_clinch 1; mp_ignore_round_win_conditions 0; "
                    "mp_match_end_restart 0; mp_endmatch_votenextmap 0; "
                    "bot_quota_mode fill; bot_quota 10"
                ),
            )
        except Exception as exc:
            print(f"[agent] Competitive runtime guard retry: {exc}")

    def _report_end_once(self, reason: str, grace: float = 0.0) -> None:
        with self._lock:
            if self._ended or not self.match_id:
                return
            self._ended = True
            match_id = self.match_id
            elapsed = 0
            if self.match_play_started_at:
                elapsed = max(0, int(time.monotonic() - self.match_play_started_at))
            result = {
                "reason": reason,
                "ct_score": self.ct_score,
                "t_score": self.t_score,
                "time_played": elapsed,
                "connected_account_ids": sorted(self.connected_account_ids),
                "player_teams": {
                    str(account_id): team
                    for account_id, team in self.player_teams.items()
                    if account_id in self.expected_account_ids
                },
            }

        # Tell the injected server GC to create the actual reward items and add
        # their preview blocks to CCSGameRules::RecordPlayerItemDrop. Source's
        # own intermission code then broadcasts SendPlayerItemDrops and fires
        # endmatch_cmm_start_reveal_items, which is the real scoreboard reveal.
        trigger_path = os.path.join(
            self.cfg["csgo_dir"], "csgo_gc", "server_match_end_trigger.txt"
        )
        trigger_tmp = trigger_path + ".tmp"
        try:
            with open(trigger_tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(f"match_id={match_id}\n")
                fh.write(f"time_played={elapsed}\n")
                fh.write(f"ct_score={self.ct_score}\n")
                fh.write(f"t_score={self.t_score}\n")
            os.replace(trigger_tmp, trigger_path)
            print(f"[agent] native drop reveal trigger written for match {match_id}")
        except OSError as exc:
            print(f"[agent] failed to write native drop reveal trigger: {exc}")

        # Publish the completed result immediately so XP/rank state reaches the
        # client while intermission is still visible. Item drops themselves are
        # generated exactly once by the server GC trigger above.
        try:
            post_json(
                self.cfg["backend_url"].rstrip("/") + "/matchmaking/server/ended",
                {"match_id": match_id, "result": result},
            )
            print(f"[agent] reported match {match_id} end immediately: {result}")
        except Exception as exc:
            print(f"[agent] failed to report match end: {exc}")

        def finish() -> None:
            if grace > 0:
                print(f"[agent] keeping srcds alive {grace:.0f}s for end-match delivery")
                time.sleep(grace)
            self.stop()

        if grace > 0:
            threading.Thread(target=finish, daemon=True, name="match-end-grace").start()
        else:
            finish()

    def stop(self) -> None:
        proc = self.proc
        self.intentional_stop = True
        self.proc = None
        self.ready_match_id = 0
        self.reservation_id = 0
        self.server_id = 0
        self.reserved_account_ids.clear()
        self.match_id = 0
        self.ready_at = 0.0
        self.source_match_started_at = 0.0
        self.using_cookie_fallback = False
        self.started = False
        self.runtime_applied = False
        self.human_presence_seen = False
        self.expected_account_ids.clear()
        self.connected_account_ids.clear()
        self.player_teams.clear()
        self.match_play_started_at = 0.0
        self.assignment_missing_since = 0.0
        self.launched_at = 0.0
        if proc is None or proc.poll() is not None:
            return
        try:
            send_local_rcon(
                int(self.cfg["local_port"]),
                self.rcon_password,
                "quit",
            )
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def maybe_start_playit(cfg: dict) -> subprocess.Popen | None:
    path = str(cfg.get("playit_exe") or "").strip()
    if not path:
        return None
    if not os.path.isfile(path):
        print(f"[agent] playit_exe not found: {path}")
        return None
    print("[agent] starting playit tunnel agent")
    return subprocess.Popen([path], cwd=os.path.dirname(path))


def main() -> None:
    cfg = load_config()
    maps = installed_maps(cfg["csgo_dir"])
    if not maps:
        raise SystemExit("[agent] no supported BSP maps found under csgo/maps")

    print(f"[agent] {REVIVAL_AGENT_BUILD} active")
    print("[agent] installed matchmaking maps: " + ", ".join(maps))
    print(f"[agent] public tunnel: {cfg['public_host']}:{cfg['public_port']}")
    playit = maybe_start_playit(cfg)
    slot = ServerSlot(cfg)
    base = cfg["backend_url"].rstrip("/")
    agent_session_id = secrets.token_hex(8)
    print(f"[agent] session id: {agent_session_id}")

    try:
        while True:
            slot.refresh_native_reservation_response()
            slot.refresh_authenticated_players()
            slot.enforce_competitive_runtime()
            flush_server_reward_bridge(cfg)
            body = {
                "agent_id": cfg["agent_id"],
                "agent_session_id": agent_session_id,
                "public_host": cfg["public_host"],
                "public_port": int(cfg["public_port"]),
                "server_version": read_csgo_server_version(cfg["csgo_dir"]),
                "maps": maps,
                "ready_match_id": slot.ready_match_id,
                "reservation_id": slot.reservation_id,
                "server_id": slot.server_id,
                "reserved_account_ids": sorted(slot.reserved_account_ids),
                "started_match_id": slot.match_id if slot.started else 0,
            }
            try:
                reply = post_json(base + "/matchmaking/server/heartbeat", body)
                assignment = reply.get("assignment")
                if isinstance(assignment, dict):
                    slot.assignment_missing_since = 0.0
                    slot.start(assignment)
                elif slot.alive() and not slot.started:
                    # Never tear down a GC-active server merely because the HTTP
                    # coordinator omitted the assignment. Once the engine has a
                    # reservation, check_accept_timeout() owns cleanup. Before
                    # readiness, allow a full five-minute boot/recovery window.
                    now = time.monotonic()
                    if not slot.assignment_missing_since:
                        slot.assignment_missing_since = now
                        print("[agent] assignment temporarily absent; keeping srcds alive")
                    if (
                        not slot.ready_match_id
                        and slot.launched_at
                        and now - slot.launched_at >= 300.0
                    ):
                        print("[agent] no assignment/readiness for 300s; stopping stale srcds")
                        slot.stop()
                slot.check_accept_timeout()
            except (urllib.error.URLError, ValueError, OSError) as exc:
                print(f"[agent] heartbeat failed: {exc}")
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("\n[agent] stopping")
    finally:
        slot.stop()
        if playit and playit.poll() is None:
            playit.terminate()


if __name__ == "__main__":
    main()
