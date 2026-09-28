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

# Curated revival queue: the maps we actually want players seeing.
# Keep this intentionally small; installed_maps() filters this list against the
# BSPs present on the laptop and NEVER appends unrelated installed maps.
MAP_POOL = (
    "de_dust2",
    "de_mirage",
    "de_cache",
    "de_cbble",
    "de_inferno",
    "de_ancient",
    "de_nuke",
    "de_overpass",
    "de_vertigo",
    "de_train",
    "cs_insertion2",
)

# Same hardcoded gscookieid used by the injected GC in CMsgCStrike15Welcome.
# Public/community CS:GO DS builds can report an empty 9106 after welcome and
# never turn our 9105 into a Valve-style queued reservation. Source's built-in
# R<pointer> fallback and the client GC both use this exact cookie.
REVIVAL_GAME_SERVER_COOKIE_ID = 0x293A206F6C6C6548
REVIVAL_AGENT_BUILD = "REVIVAL_AGENT_PUBLIC_RELEASE_V51"

GAME_OVER_PATTERNS = (
    re.compile(r'World triggered "Game_Over"', re.I),
    re.compile(r'Game Over:', re.I),
    re.compile(r'Going to intermission(?:\.\.\.)?$', re.I),
    re.compile(r'\bGAMEPHASE_MATCH_ENDED\b', re.I),
)
TEAM_SCORE_RE = re.compile(r'Team "(CT|TERRORIST)" scored "(\d+)"', re.I)
STEAM2_RE = re.compile(r'STEAM_[0-5]:(\d):(\d+)', re.I)
STEAM3_RE = re.compile(r'\[U:1:(\d+)\]', re.I)
STEAM64_RE = re.compile(r'\b(7656119\d{10})\b')
PLAYER_TEAM_RE = re.compile(r'<(CT|TERRORIST)>', re.I)

GRENADE_KILL_WEAPONS = {"hegrenade", "inferno", "molotov", "incgrenade"}
SNIPER_WEAPONS = {"awp", "ssg08", "scar20", "g3sg1"}
RIFLE_WEAPONS = {"ak47", "m4a1", "m4a1_silencer", "aug", "sg556", "famas", "galilar"}
PISTOL_WEAPONS = {"glock", "hkp2000", "usp_silencer", "p250", "fiveseven", "tec9", "deagle", "revolver", "elite", "cz75a"}
SMG_WEAPONS = {"mac10", "mp9", "mp7", "mp5sd", "ump45", "p90", "bizon"}
SHOTGUN_WEAPONS = {"nova", "xm1014", "mag7", "sawedoff"}
HEAVY_WEAPONS = {"m249", "negev"}


def revival_kill_category(weapon: str) -> str:
    w = weapon.lower().removeprefix("weapon_")
    if w in GRENADE_KILL_WEAPONS:
        return "grenade"
    if w.startswith("knife") or w == "bayonet":
        return "knife"
    if w in SNIPER_WEAPONS:
        return "sniper"
    if w in RIFLE_WEAPONS:
        return "rifle"
    if w in PISTOL_WEAPONS:
        return "pistol"
    if w in SMG_WEAPONS:
        return "smg"
    if w in SHOTGUN_WEAPONS:
        return "shotgun"
    if w in HEAVY_WEAPONS:
        return "heavy"
    return ""



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


def current_steam_account_token(fallback: str = "") -> str:
    """Reload the GSLT from server_agent.json before every new srcds launch."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            live = json.load(fh)
        return str(live.get("steam_account_token") or "").strip()
    except (OSError, ValueError, TypeError):
        return str(fallback or "").strip()


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
    cfg.setdefault("post_match_grace_seconds", 60)
    cfg["post_match_grace_seconds"] = max(
        60.0, float(cfg.get("post_match_grace_seconds", 60))
    )
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

    # Only advertise the curated matchmaking pool. A map still has to be
    # physically installed on the laptop, so a missing BSP is safely skipped.
    found = {
        name[:-4].lower()
        for name in os.listdir(maps_dir)
        if name.lower().endswith(".bsp")
    }
    return [m for m in MAP_POOL if m in found]


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
sv_allowdownload 1
sv_allowupload 0
net_maxfilesize 64
sv_hibernate_when_empty 0
sv_hibernate_postgame_delay 5
__REVIVAL_STEAM_ACCOUNT_LINE__
log on
"""
    steam_line = f'sv_setsteamaccount "{token}"' if token else ""
    early = early.replace("__REVIVAL_STEAM_ACCOUNT_LINE__", steam_line)
    with open(early_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(early)

    # CS:GO loads this AFTER gamemode_competitive.cfg. The server log explicitly
    # attempts this file on every competitive map, making it the correct final
    # override point for the revival's matchmaking runtime.
    late_path = os.path.join(cfg_dir, "gamemode_competitive_server.cfg")
    late = r"""// CS:GO Revival - final matchmaking overrides
sv_competitive_official_5v5 1
sv_allowdownload 1
sv_allowupload 0
net_maxfilesize 64
bot_quota 10
bot_quota_mode fill
bot_join_after_player 1
bot_auto_vacate 1
bot_join_team any
bot_stop 0
bot_freeze 0
bot_dont_shoot 0

// Matchmaking-style friendly-fire punishment. Source owns the warning/kick
// accounting so it behaves consistently for every connected human.
mp_autokick 1
mp_tkpunish 0
mp_spawnprotectiontime 5
mp_td_dmgtowarn 200
mp_td_dmgtokick 300
mp_td_spawndmgthreshold 50
mp_autoteambalance 0
mp_limitteams 0
mp_friendlyfire 1
ff_damage_reduction_bullets 0.33
ff_damage_reduction_grenade 0.85
ff_damage_reduction_grenade_self 1
ff_damage_reduction_other 0.4
cash_player_killed_teammate -300
mp_maxrounds 16
mp_winlimit 0
mp_halftime 1
mp_overtime_enable 0
mp_overtime_maxrounds 0
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


def read_engine_reservation_ready(csgo_dir: str) -> dict[str, object]:
    out: dict[str, object] = {"match_id": 0, "account_ids": [], "mode": ""}
    try:
        with open(
            engine_reservation_ready_path(csgo_dir),
            "r",
            encoding="utf-8",
            errors="replace",
        ) as fh:
            payload = ""
            for raw in fh:
                line = raw.strip()
                if line.startswith("match_id="):
                    try:
                        out["match_id"] = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
                elif line.startswith("payload="):
                    payload = line.split("=", 1)[1]
            if payload:
                if payload[0:1] in ("Q", "G"):
                    out["mode"] = payload[0]
                ids: list[int] = []
                for token in re.findall(r"\[([0-9A-Fa-f]+)\]", payload):
                    try:
                        value = int(token, 16)
                    except ValueError:
                        continue
                    if value > 0:
                        ids.append(value)
                out["account_ids"] = ids
    except OSError:
        pass
    return out


def engine_reservation_is_ready(csgo_dir: str, match_id: int) -> bool:
    ready = read_engine_reservation_ready(csgo_dir)
    return int(ready.get("match_id") or 0) == int(match_id)


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
        f"map={str(assignment.get('map') or '').strip()}",
        f"live_joinable={1 if assignment.get('live_joinable') else 0}",
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
        self.queued_account_ids: set[int] = set()
        self.live_joinable = False
        self.ct_score = 0
        self.t_score = 0
        self.expected_account_ids: set[int] = set()
        self.connected_account_ids: set[int] = set()
        self.player_teams: dict[int, str] = {}
        self.player_rounds_won: dict[int, int] = {}
        self.player_kill_stats: dict[int, dict[str, int]] = {}
        self.player_weapon_kills: dict[int, dict[str, int]] = {}
        self.recent_kill_lines: dict[str, float] = {}
        # Track the two logical squads independently of physical CT/T sides.
        # MR8 swaps CT/T at halftime, but a player's squad identity must not
        # change. This is the authority for live mission rounds and match wins.
        self.side_squads: dict[str, str] = {"CT": "A", "TERRORIST": "B"}
        self.player_squads: dict[int, str] = {}
        self.squad_scores: dict[str, int] = {"A": 0, "B": 0}
        self.last_side_score: dict[str, int] = {"CT": 0, "TERRORIST": 0}
        self.total_scored_rounds = 0
        self.match_play_started_at = 0.0
        self.ready_at = 0.0
        self.source_match_started_at = 0.0
        self.using_cookie_fallback = False
        self.live_joinable = False
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
                new_live_joinable = bool(assignment.get("live_joinable"))
                mode_changed = new_live_joinable != self.live_joinable
                self.expected_account_ids.update(new_accounts)
                if added:
                    sync_server_player_inventories(
                        self.cfg, assignment, clear_existing=False
                    )
                if added or mode_changed:
                    write_native_reservation(
                        self.cfg["csgo_dir"], assignment, clear_response=False
                    )
                    self.live_joinable = new_live_joinable
                    if mode_changed:
                        print(
                            f"[agent] REVIVAL_JOIN_IN_PROGRESS_G_V1 match {match_id} "
                            f"mode={'G' if new_live_joinable else 'Q'}"
                        )
                    if added:
                        print(
                            "[agent] drop-in player(s) staged for live match "
                            f"{match_id}: {', '.join(str(x) for x in sorted(added))}"
                        )
                return
            self.stop()

            map_name = str(assignment.get("map") or "de_dust2")
            srcds = find_srcds(self.cfg["csgo_dir"])
            live_token = current_steam_account_token(
                self.cfg.get("steam_account_token", "")
            )
            self.cfg["steam_account_token"] = live_token
            ensure_match_cfg(
                self.cfg["csgo_dir"],
                live_token,
            )
            print(
                "[agent] REVIVAL_GSLT_HOT_RELOAD_V1 "
                + ("loaded token for new srcds" if live_token else "no token configured")
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
                "-tournament", "revival",
                "-tournament_extra_casters_slots", "10",
                "+game_type", "0",
                "+game_mode", "1",
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
            print("[agent] REVIVAL_Q_SLOT_PAD_V1 tournament extra-slot mode active (10 human slots, bots fill)")
            print("[agent] reservation slot args: -tournament revival -tournament_extra_casters_slots 10 -maxplayers_override 10")
            if os.name == "nt":
                creationflags = (
                    subprocess.CREATE_NEW_CONSOLE
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                creationflags = 0
            try:
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
            except OSError as exc:
                if getattr(exc, "winerror", None) == 225:
                    print(
                        "[agent] REVIVAL_WINDOWS_DEFENDER_BLOCK_V1 Windows blocked "
                        f"the SRCDS launch as malware/PUA: {srcds}"
                    )
                    print(
                        "[agent] Open Windows Security > Virus & threat protection > "
                        "Protection history, review the detection, and allow/restore "
                        "only this known revival server file if you trust your build."
                    )
                raise
            set_above_normal(self.proc)
            self.match_id = match_id
            self.ready_match_id = 0
            self.reservation_id = 0
            self.server_id = 0
            self.reserved_account_ids.clear()
            self.queued_account_ids.clear()
            self.ct_score = 0
            self.t_score = 0
            self.expected_account_ids = {
                int(x) for x in assignment.get("account_ids", []) if int(x) > 0
            }
            self.connected_account_ids.clear()
            self.player_teams.clear()
            self.player_rounds_won.clear()
            self.player_kill_stats.clear()
            self.player_weapon_kills.clear()
            self.recent_kill_lines.clear()
            self.side_squads = {"CT": "A", "TERRORIST": "B"}
            self.player_squads.clear()
            self.squad_scores = {"A": 0, "B": 0}
            self.last_side_score = {"CT": 0, "TERRORIST": 0}
            self.total_scored_rounds = 0
            self.match_play_started_at = 0.0
            self.ready_at = 0.0
            self.source_match_started_at = 0.0
            self.using_cookie_fallback = False
            self.live_joinable = bool(assignment.get("live_joinable"))
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
            engine_state = read_engine_reservation_ready(self.cfg["csgo_dir"])
            engine_ready = int(engine_state.get("match_id") or 0) == match_id
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
                    self.queued_account_ids = {
                        int(x) for x in engine_state.get("account_ids", [])
                        if int(x) > 0
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
                    self.queued_account_ids = {
                        int(x) for x in engine_state.get("account_ids", [])
                        if int(x) > 0
                    }
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

        lower_line = line.lower()
        if (
            "revival_" in lower_line
            or "[revival]" in lower_line
            or "fatal error" in lower_line
            or "steam account token has expired" in lower_line
            or "matchmaking server: native" in lower_line
        ):
            print("[srcds] " + line)

        if 'triggered "Match_Start"' in line:
            with self._lock:
                if not self.source_match_started_at:
                    self.source_match_started_at = time.monotonic()

        # Track authoritative PvP mission kill stats from Source logs.
        if ' killed "' in line and ' with "' in line:
            now = time.monotonic()
            with self._lock:
                previous = self.recent_kill_lines.get(line, 0.0)
                duplicate_kill_line = now - previous < 3.0
                self.recent_kill_lines[line] = now
                if len(self.recent_kill_lines) > 256:
                    cutoff = now - 5.0
                    self.recent_kill_lines = {
                        key: seen for key, seen in self.recent_kill_lines.items()
                        if seen >= cutoff
                    }

            attacker_part = line.split(' killed "', 1)[0]
            attacker_id = account_id_from_text(attacker_part)
            weapon_match = re.search(r' with "([^"]+)"(.*)        if m:
            score = int(m.group(2))
            side = m.group(1).upper()
            scoring_event = False
            halftime_flip = False
            with self._lock:
                # The same Source scoring line can appear in both console.log
                # and the L*.log. Deduplicate by the side's latest score value,
                # but NEVER derive progress from score-old_score: CT/T score
                # values can move backwards when sides swap at halftime.
                previous_logged_score = self.last_side_score.get(side, 0)
                scoring_event = score > 0 and score != previous_logged_score
                self.last_side_score[side] = score

                if side == "CT":
                    self.ct_score = score
                else:
                    self.t_score = score

                if scoring_event:
                    winning_squad = self.side_squads.get(side, "")
                    if winning_squad:
                        self.squad_scores[winning_squad] = (
                            self.squad_scores.get(winning_squad, 0) + 1
                        )
                        for account_id in self.expected_account_ids:
                            if self.player_squads.get(account_id) == winning_squad:
                                self.player_rounds_won[account_id] = (
                                    self.player_rounds_won.get(account_id, 0) + 1
                                )

                    self.total_scored_rounds += 1

                    # Short Competitive is MR8: after eight completed rounds,
                    # logical squad A/B stays fixed while physical CT/T swaps.
                    if self.total_scored_rounds == 8:
                        self.side_squads = {
                            "CT": self.side_squads.get("TERRORIST", "B"),
                            "TERRORIST": self.side_squads.get("CT", "A"),
                        }
                        halftime_flip = True

                match_id = self.match_id
                live_rounds = dict(self.player_rounds_won)
                squad_scores = dict(self.squad_scores)

            if scoring_event and match_id:
                print(
                    f"[agent] REVIVAL_LIVE_OPERATION_ROUNDS_V2 "
                    f"match={match_id} score={self.ct_score}-{self.t_score} "
                    f"squads={squad_scores} player_rounds={live_rounds}"
                )
            if halftime_flip and match_id:
                print(
                    f"[agent] REVIVAL_SHORT_MATCH_HALFTIME_V1 "
                    f"match={match_id} physical-sides-swapped logical-squads-preserved"
                )

        seen_account_id = account_id_from_text(line)
        if seen_account_id:
            team_match = PLAYER_TEAM_RE.search(line)
            if team_match:
                team = team_match.group(1).upper()
                if team in ("CT", "TERRORIST"):
                    with self._lock:
                        self.player_teams[seen_account_id] = team
                        # Assign a logical squad once. Do not overwrite it after
                        # halftime just because the player's physical side flips.
                        if seen_account_id not in self.player_squads:
                            squad = self.side_squads.get(team, "")
                            if squad:
                                self.player_squads[seen_account_id] = squad
                                self.player_rounds_won.setdefault(
                                    seen_account_id, 0
                                )

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
            with self._lock:
                valid_live_match = (
                    self.started
                    and self.human_presence_seen
                    and bool(self.connected_account_ids)
                    and not self._ended
                )
            if not valid_live_match:
                return
            print("[agent] match end detected")
            self._report_end_once(
                "game_over",
                grace=float(self.cfg.get("post_match_grace_seconds", 70)),
            )

    def _reader(self) -> None:
        """Tail Source's normal L*.log files; srcds owns a real Win32 console."""
        proc = self.proc
        if proc is None:
            return

        logs_dir = os.path.join(self.cfg["csgo_dir"], "csgo", "logs")
        console_path = os.path.join(self.cfg["csgo_dir"], "csgo", "console.log")
        current_path = ""
        position = 0
        console_position = 0

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

        def drain_console() -> None:
            nonlocal console_position
            try:
                if not os.path.isfile(console_path):
                    return
                with open(console_path, "rb") as fh:
                    size = os.fstat(fh.fileno()).st_size
                    if size < console_position:
                        console_position = 0
                    fh.seek(console_position)
                    while True:
                        raw = fh.readline()
                        if not raw:
                            break
                        console_position = fh.tell()
                        self._handle_server_log_line(
                            raw.decode("utf-8", errors="replace")
                        )
            except Exception:
                return

        while proc.poll() is None:
            drain()
            drain_console()
            time.sleep(0.20)

        drain()
        drain_console()
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
                    "mp_autokick 1; mp_tkpunish 0; mp_spawnprotectiontime 5; "
                    "mp_td_dmgtowarn 200; mp_td_dmgtokick 300; "
                    "mp_td_spawndmgthreshold 50; "
                    "mp_autoteambalance 0; mp_limitteams 0; "
                    "mp_friendlyfire 1; "
                    "ff_damage_reduction_bullets 0.33; "
                    "ff_damage_reduction_grenade 0.85; "
                    "ff_damage_reduction_grenade_self 1; "
                    "ff_damage_reduction_other 0.4; "
                    "cash_player_killed_teammate -300; "
                    "mp_maxrounds 16; mp_winlimit 0; mp_halftime 1; "
                    "mp_overtime_enable 0; "
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
                    "mp_overtime_enable; mp_friendlyfire; mp_autokick; "
                    "mp_tkpunish; mp_spawnprotectiontime; "
                    "mp_td_dmgtowarn; mp_td_dmgtokick; "
                    "mp_td_spawndmgthreshold; "
                    "ff_damage_reduction_bullets; "
                    "ff_damage_reduction_grenade; "
                    "ff_damage_reduction_grenade_self; "
                    "ff_damage_reduction_other; cash_player_killed_teammate; "
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

        print("[agent] Competitive match started")

        print(
            "[agent] REVIVAL_TEAMKILL_RULES_V1 active "
            "(warn=200 damage, kick=300 damage, spawn=50/5s)"
        )

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

        engine_state = read_engine_reservation_ready(self.cfg["csgo_dir"])
        if int(engine_state.get("match_id") or 0) != match_id:
            return
        queued_accounts = {
            int(x) for x in engine_state.get("account_ids", [])
            if int(x) > 0
        }

        response = read_native_reservation_response(self.cfg["csgo_dir"])
        if int(response.get("match_id") or 0) != match_id:
            return
        new_reservation = int(response.get("reservation_id") or 0)
        new_server_id = int(response.get("server_id") or 0)
        engine_mode = str(engine_state.get("mode") or "").upper()

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
            queue_changed = queued_accounts != self.queued_account_ids
            self.reserved_account_ids = acknowledged
            self.queued_account_ids = queued_accounts
            self.reservation_id = new_reservation
            self.server_id = new_server_id
            self.ready_match_id = match_id
            self.using_cookie_fallback = False
            if new_reservation != old_reservation or membership_changed or queue_changed:
                print(
                    f"[agent] REVIVAL_LATEJOIN_PENDING_ROSTER_V1 reservation refreshed "
                    f"for match {match_id}: reservation={new_reservation}, "
                    f"server_id={new_server_id or 'direct-udp'}, "
                    f"accounts={','.join(str(x) for x in sorted(acknowledged))}, "
                    f"queued={','.join(str(x) for x in sorted(queued_accounts))}, "
                    f"engine_mode={engine_mode}"
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
                    "sv_allowdownload 1; sv_allowupload 0; net_maxfilesize 64; "
                    "mp_timelimit 0; mp_maxrounds 16; mp_winlimit 0; "
                    "mp_halftime 1; mp_overtime_enable 0; "
                    "mp_autokick 1; mp_tkpunish 0; mp_spawnprotectiontime 5; "
                    "mp_td_dmgtowarn 200; mp_td_dmgtokick 300; "
                    "mp_td_spawndmgthreshold 50; mp_friendlyfire 1; "
                    "ff_damage_reduction_bullets 0.33; "
                    "ff_damage_reduction_grenade 0.85; "
                    "ff_damage_reduction_grenade_self 1; "
                    "ff_damage_reduction_other 0.4; "
                    "cash_player_killed_teammate -300; "
                    "mp_match_can_clinch 1; mp_ignore_round_win_conditions 0; "
                    "mp_match_end_restart 0; mp_endmatch_votenextmap 0; "
                    "bot_quota_mode fill; bot_auto_vacate 1; bot_quota 10"
                ),
            )
        except Exception:
            return

    def _report_end_once(self, reason: str, grace: float = 0.0) -> None:
        with self._lock:
            if self._ended or not self.match_id:
                return
            self._ended = True
            match_id = self.match_id
            elapsed = 0
            if self.match_play_started_at:
                elapsed = max(0, int(time.monotonic() - self.match_play_started_at))
            player_rounds = {
                str(account_id): int(self.player_rounds_won.get(account_id, 0))
                for account_id in self.expected_account_ids
            }
            player_won: dict[str, bool] = {}
            player_tied: dict[str, bool] = {}
            for account_id in self.expected_account_ids:
                squad = self.player_squads.get(account_id, "")
                ours = int(self.squad_scores.get(squad, 0)) if squad else 0
                other_squad = "B" if squad == "A" else ("A" if squad == "B" else "")
                theirs = int(self.squad_scores.get(other_squad, 0)) if other_squad else 0
                player_won[str(account_id)] = bool(squad and ours > theirs)
                player_tied[str(account_id)] = bool(squad and ours == theirs)

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
                "player_rounds_won": player_rounds,
                "player_won": player_won,
                "player_tied": player_tied,
                "logical_squad_scores": dict(self.squad_scores),
                "total_scored_rounds": int(self.total_scored_rounds),
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
                for account_id, team in sorted(self.player_teams.items()):
                    if account_id in self.expected_account_ids:
                        fh.write(f"team_{account_id}={team}\n")
                for account_id in sorted(self.expected_account_ids):
                    key = str(account_id)
                    kill_stats = self.player_kill_stats.get(account_id, {})
                    weapon_stats = self.player_weapon_kills.get(account_id, {})
                    for stat_name in (
                        "kills", "headshots", "noscopes", "through_smoke",
                        "blind", "wallbang", "grenade", "knife", "sniper",
                        "rifle", "pistol", "smg", "shotgun", "heavy"
                    ):
                        fh.write(
                            f"{stat_name}_{account_id}="
                            f"{int(kill_stats.get(stat_name, 0))}\n"
                        )
                    weapon_summary = ",".join(
                        f"{name}:{int(count)}"
                        for name, count in sorted(weapon_stats.items())
                        if count > 0
                    )
                    fh.write(f"weapons_{account_id}={weapon_summary}\n")
                    fh.write(
                        f"rounds_{account_id}=
                        f"{int(result['player_rounds_won'].get(key, 0))}\n"
                    )
                    fh.write(
                        f"won_{account_id}="
                        f"{1 if result['player_won'].get(key, False) else 0}\n"
                    )
                    fh.write(
                        f"tied_{account_id}="
                        f"{1 if result['player_tied'].get(key, False) else 0}\n"
                    )
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
        self.queued_account_ids.clear()
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
        self.player_rounds_won.clear()
        self.side_squads = {"CT": "A", "TERRORIST": "B"}
        self.player_squads.clear()
        self.squad_scores = {"A": 0, "B": 0}
        self.last_side_score = {"CT": 0, "TERRORIST": 0}
        self.total_scored_rounds = 0
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


def spool_item_acknowledgements(cfg: dict, acknowledgements: object) -> None:
    if not isinstance(acknowledgements, list) or not acknowledgements:
        return

    outbox = os.path.join(cfg["csgo_dir"], "csgo_gc", "server_item_acks")
    os.makedirs(outbox, exist_ok=True)

    for entry in acknowledgements:
        if not isinstance(entry, dict):
            continue
        steamid = str(entry.get("steamid") or "").strip()
        payload_b64 = str(entry.get("payload_b64") or "").strip()
        if not steamid.isdigit() or not payload_b64:
            continue
        try:
            payload = base64.b64decode(payload_b64, validate=True)
        except Exception:
            continue
        if not payload or len(payload) > 64 * 1024:
            continue

        stamp = time.time_ns()
        final_path = os.path.join(outbox, f"{steamid}_{stamp}.bin")
        temp_path = final_path + ".tmp"
        try:
            with open(temp_path, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, final_path)
            print(
                f"[agent] REVIVAL_SERVER_UNBOX_CHAT_RELAY_V1 queued "
                f"native item acknowledgement for {steamid} ({len(payload)} bytes)"
            )
        except OSError as exc:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            print(f"[agent] failed to spool native item acknowledgement: {exc}")


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
    last_reset_generation: int | None = None

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
                "queued_account_ids": sorted(slot.queued_account_ids),
                "engine_reservation_mode": str(
                    read_engine_reservation_ready(cfg["csgo_dir"]).get("mode") or ""
                ),
                "started_match_id": slot.match_id if slot.started else 0,
                # Live score/team data lets the desktop GC mirror Operation
                # round-win progress during the match instead of waiting for
                # the final MatchEndRunRewardDrops fallback.
                "ct_score": slot.ct_score,
                "t_score": slot.t_score,
                "player_teams": {
                    str(account_id): team
                    for account_id, team in slot.player_teams.items()
                    if account_id in slot.expected_account_ids
                },
                "player_rounds_won": {
                    str(account_id): int(rounds)
                    for account_id, rounds in slot.player_rounds_won.items()
                    if account_id in slot.expected_account_ids
                },
            }
            try:
                reply = post_json(base + "/matchmaking/server/heartbeat", body)
                spool_item_acknowledgements(cfg, reply.get("item_acks"))
                reset_generation = int(reply.get("reset_generation") or 0)
                if last_reset_generation is None:
                    last_reset_generation = reset_generation
                elif reset_generation != last_reset_generation:
                    print(
                        f"[agent] REVIVAL_ADMIN_RESET_V1 generation "
                        f"{last_reset_generation}->{reset_generation}; stopping live srcds"
                    )
                    last_reset_generation = reset_generation
                    slot.stop()

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
, line, re.I)
            if attacker_id and weapon_match and not duplicate_kill_line:
                weapon = weapon_match.group(1).lower().removeprefix("weapon_")
                modifiers = weapon_match.group(2).lower()
                with self._lock:
                    if (not self.expected_account_ids
                            or attacker_id in self.expected_account_ids):
                        stats = self.player_kill_stats.setdefault(attacker_id, {
                            "kills": 0, "headshots": 0, "noscopes": 0,
                            "through_smoke": 0, "blind": 0, "wallbang": 0,
                            "grenade": 0, "knife": 0, "sniper": 0,
                            "rifle": 0, "pistol": 0, "smg": 0,
                            "shotgun": 0, "heavy": 0,
                        })
                        stats["kills"] += 1
                        if "headshot" in modifiers:
                            stats["headshots"] += 1
                        if ("noscope" in modifiers or "no_scope" in modifiers
                                or "no-scop" in modifiers or "unscoped" in modifiers):
                            stats["noscopes"] += 1
                        if "thrusmoke" in modifiers or "through smoke" in modifiers:
                            stats["through_smoke"] += 1
                        if "attackerblind" in modifiers or "blind" in modifiers:
                            stats["blind"] += 1
                        if "penetrated" in modifiers or "wallbang" in modifiers:
                            stats["wallbang"] += 1
                        category = revival_kill_category(weapon)
                        if category:
                            stats[category] += 1
                        weapons = self.player_weapon_kills.setdefault(attacker_id, {})
                        weapons[weapon] = weapons.get(weapon, 0) + 1
                        print(
                            f"[agent] REVIVAL_PVP_MISSION_STATS_V1 account={attacker_id} "
                            f"weapon={weapon} total={stats['kills']} "
                            f"hs={stats['headshots']} ns={stats['noscopes']} "
                            f"grenade={stats['grenade']}"
                        )

        m = TEAM_SCORE_RE.search(line)
        if m:
            score = int(m.group(2))
            side = m.group(1).upper()
            scoring_event = False
            halftime_flip = False
            with self._lock:
                # The same Source scoring line can appear in both console.log
                # and the L*.log. Deduplicate by the side's latest score value,
                # but NEVER derive progress from score-old_score: CT/T score
                # values can move backwards when sides swap at halftime.
                previous_logged_score = self.last_side_score.get(side, 0)
                scoring_event = score > 0 and score != previous_logged_score
                self.last_side_score[side] = score

                if side == "CT":
                    self.ct_score = score
                else:
                    self.t_score = score

                if scoring_event:
                    winning_squad = self.side_squads.get(side, "")
                    if winning_squad:
                        self.squad_scores[winning_squad] = (
                            self.squad_scores.get(winning_squad, 0) + 1
                        )
                        for account_id in self.expected_account_ids:
                            if self.player_squads.get(account_id) == winning_squad:
                                self.player_rounds_won[account_id] = (
                                    self.player_rounds_won.get(account_id, 0) + 1
                                )

                    self.total_scored_rounds += 1

                    # Short Competitive is MR8: after eight completed rounds,
                    # logical squad A/B stays fixed while physical CT/T swaps.
                    if self.total_scored_rounds == 8:
                        self.side_squads = {
                            "CT": self.side_squads.get("TERRORIST", "B"),
                            "TERRORIST": self.side_squads.get("CT", "A"),
                        }
                        halftime_flip = True

                match_id = self.match_id
                live_rounds = dict(self.player_rounds_won)
                squad_scores = dict(self.squad_scores)

            if scoring_event and match_id:
                print(
                    f"[agent] REVIVAL_LIVE_OPERATION_ROUNDS_V2 "
                    f"match={match_id} score={self.ct_score}-{self.t_score} "
                    f"squads={squad_scores} player_rounds={live_rounds}"
                )
            if halftime_flip and match_id:
                print(
                    f"[agent] REVIVAL_SHORT_MATCH_HALFTIME_V1 "
                    f"match={match_id} physical-sides-swapped logical-squads-preserved"
                )

        seen_account_id = account_id_from_text(line)
        if seen_account_id:
            team_match = PLAYER_TEAM_RE.search(line)
            if team_match:
                team = team_match.group(1).upper()
                if team in ("CT", "TERRORIST"):
                    with self._lock:
                        self.player_teams[seen_account_id] = team
                        # Assign a logical squad once. Do not overwrite it after
                        # halftime just because the player's physical side flips.
                        if seen_account_id not in self.player_squads:
                            squad = self.side_squads.get(team, "")
                            if squad:
                                self.player_squads[seen_account_id] = squad
                                self.player_rounds_won.setdefault(
                                    seen_account_id, 0
                                )

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
            with self._lock:
                valid_live_match = (
                    self.started
                    and self.human_presence_seen
                    and bool(self.connected_account_ids)
                    and not self._ended
                )
            if not valid_live_match:
                return
            print("[agent] match end detected")
            self._report_end_once(
                "game_over",
                grace=float(self.cfg.get("post_match_grace_seconds", 70)),
            )

    def _reader(self) -> None:
        """Tail Source's normal L*.log files; srcds owns a real Win32 console."""
        proc = self.proc
        if proc is None:
            return

        logs_dir = os.path.join(self.cfg["csgo_dir"], "csgo", "logs")
        console_path = os.path.join(self.cfg["csgo_dir"], "csgo", "console.log")
        current_path = ""
        position = 0
        console_position = 0

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

        def drain_console() -> None:
            nonlocal console_position
            try:
                if not os.path.isfile(console_path):
                    return
                with open(console_path, "rb") as fh:
                    size = os.fstat(fh.fileno()).st_size
                    if size < console_position:
                        console_position = 0
                    fh.seek(console_position)
                    while True:
                        raw = fh.readline()
                        if not raw:
                            break
                        console_position = fh.tell()
                        self._handle_server_log_line(
                            raw.decode("utf-8", errors="replace")
                        )
            except Exception:
                return

        while proc.poll() is None:
            drain()
            drain_console()
            time.sleep(0.20)

        drain()
        drain_console()
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
                    "mp_autokick 1; mp_tkpunish 0; mp_spawnprotectiontime 5; "
                    "mp_td_dmgtowarn 200; mp_td_dmgtokick 300; "
                    "mp_td_spawndmgthreshold 50; "
                    "mp_autoteambalance 0; mp_limitteams 0; "
                    "mp_friendlyfire 1; "
                    "ff_damage_reduction_bullets 0.33; "
                    "ff_damage_reduction_grenade 0.85; "
                    "ff_damage_reduction_grenade_self 1; "
                    "ff_damage_reduction_other 0.4; "
                    "cash_player_killed_teammate -300; "
                    "mp_maxrounds 16; mp_winlimit 0; mp_halftime 1; "
                    "mp_overtime_enable 0; "
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
                    "mp_overtime_enable; mp_friendlyfire; mp_autokick; "
                    "mp_tkpunish; mp_spawnprotectiontime; "
                    "mp_td_dmgtowarn; mp_td_dmgtokick; "
                    "mp_td_spawndmgthreshold; "
                    "ff_damage_reduction_bullets; "
                    "ff_damage_reduction_grenade; "
                    "ff_damage_reduction_grenade_self; "
                    "ff_damage_reduction_other; cash_player_killed_teammate; "
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

        print("[agent] Competitive match started")

        print(
            "[agent] REVIVAL_TEAMKILL_RULES_V1 active "
            "(warn=200 damage, kick=300 damage, spawn=50/5s)"
        )

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

        engine_state = read_engine_reservation_ready(self.cfg["csgo_dir"])
        if int(engine_state.get("match_id") or 0) != match_id:
            return
        queued_accounts = {
            int(x) for x in engine_state.get("account_ids", [])
            if int(x) > 0
        }

        response = read_native_reservation_response(self.cfg["csgo_dir"])
        if int(response.get("match_id") or 0) != match_id:
            return
        new_reservation = int(response.get("reservation_id") or 0)
        new_server_id = int(response.get("server_id") or 0)
        engine_mode = str(engine_state.get("mode") or "").upper()

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
            queue_changed = queued_accounts != self.queued_account_ids
            self.reserved_account_ids = acknowledged
            self.queued_account_ids = queued_accounts
            self.reservation_id = new_reservation
            self.server_id = new_server_id
            self.ready_match_id = match_id
            self.using_cookie_fallback = False
            if new_reservation != old_reservation or membership_changed or queue_changed:
                print(
                    f"[agent] REVIVAL_LATEJOIN_PENDING_ROSTER_V1 reservation refreshed "
                    f"for match {match_id}: reservation={new_reservation}, "
                    f"server_id={new_server_id or 'direct-udp'}, "
                    f"accounts={','.join(str(x) for x in sorted(acknowledged))}, "
                    f"queued={','.join(str(x) for x in sorted(queued_accounts))}, "
                    f"engine_mode={engine_mode}"
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
                    "sv_allowdownload 1; sv_allowupload 0; net_maxfilesize 64; "
                    "mp_timelimit 0; mp_maxrounds 16; mp_winlimit 0; "
                    "mp_halftime 1; mp_overtime_enable 0; "
                    "mp_autokick 1; mp_tkpunish 0; mp_spawnprotectiontime 5; "
                    "mp_td_dmgtowarn 200; mp_td_dmgtokick 300; "
                    "mp_td_spawndmgthreshold 50; mp_friendlyfire 1; "
                    "ff_damage_reduction_bullets 0.33; "
                    "ff_damage_reduction_grenade 0.85; "
                    "ff_damage_reduction_grenade_self 1; "
                    "ff_damage_reduction_other 0.4; "
                    "cash_player_killed_teammate -300; "
                    "mp_match_can_clinch 1; mp_ignore_round_win_conditions 0; "
                    "mp_match_end_restart 0; mp_endmatch_votenextmap 0; "
                    "bot_quota_mode fill; bot_auto_vacate 1; bot_quota 10"
                ),
            )
        except Exception:
            return

    def _report_end_once(self, reason: str, grace: float = 0.0) -> None:
        with self._lock:
            if self._ended or not self.match_id:
                return
            self._ended = True
            match_id = self.match_id
            elapsed = 0
            if self.match_play_started_at:
                elapsed = max(0, int(time.monotonic() - self.match_play_started_at))
            player_rounds = {
                str(account_id): int(self.player_rounds_won.get(account_id, 0))
                for account_id in self.expected_account_ids
            }
            player_won: dict[str, bool] = {}
            player_tied: dict[str, bool] = {}
            for account_id in self.expected_account_ids:
                squad = self.player_squads.get(account_id, "")
                ours = int(self.squad_scores.get(squad, 0)) if squad else 0
                other_squad = "B" if squad == "A" else ("A" if squad == "B" else "")
                theirs = int(self.squad_scores.get(other_squad, 0)) if other_squad else 0
                player_won[str(account_id)] = bool(squad and ours > theirs)
                player_tied[str(account_id)] = bool(squad and ours == theirs)

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
                "player_rounds_won": player_rounds,
                "player_won": player_won,
                "player_tied": player_tied,
                "logical_squad_scores": dict(self.squad_scores),
                "total_scored_rounds": int(self.total_scored_rounds),
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
                for account_id, team in sorted(self.player_teams.items()):
                    if account_id in self.expected_account_ids:
                        fh.write(f"team_{account_id}={team}\n")
                for account_id in sorted(self.expected_account_ids):
                    key = str(account_id)
                    fh.write(
                        f"rounds_{account_id}="
                        f"{int(result['player_rounds_won'].get(key, 0))}\n"
                    )
                    fh.write(
                        f"won_{account_id}="
                        f"{1 if result['player_won'].get(key, False) else 0}\n"
                    )
                    fh.write(
                        f"tied_{account_id}="
                        f"{1 if result['player_tied'].get(key, False) else 0}\n"
                    )
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
        self.queued_account_ids.clear()
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
        self.player_rounds_won.clear()
        self.side_squads = {"CT": "A", "TERRORIST": "B"}
        self.player_squads.clear()
        self.squad_scores = {"A": 0, "B": 0}
        self.last_side_score = {"CT": 0, "TERRORIST": 0}
        self.total_scored_rounds = 0
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


def spool_item_acknowledgements(cfg: dict, acknowledgements: object) -> None:
    if not isinstance(acknowledgements, list) or not acknowledgements:
        return

    outbox = os.path.join(cfg["csgo_dir"], "csgo_gc", "server_item_acks")
    os.makedirs(outbox, exist_ok=True)

    for entry in acknowledgements:
        if not isinstance(entry, dict):
            continue
        steamid = str(entry.get("steamid") or "").strip()
        payload_b64 = str(entry.get("payload_b64") or "").strip()
        if not steamid.isdigit() or not payload_b64:
            continue
        try:
            payload = base64.b64decode(payload_b64, validate=True)
        except Exception:
            continue
        if not payload or len(payload) > 64 * 1024:
            continue

        stamp = time.time_ns()
        final_path = os.path.join(outbox, f"{steamid}_{stamp}.bin")
        temp_path = final_path + ".tmp"
        try:
            with open(temp_path, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, final_path)
            print(
                f"[agent] REVIVAL_SERVER_UNBOX_CHAT_RELAY_V1 queued "
                f"native item acknowledgement for {steamid} ({len(payload)} bytes)"
            )
        except OSError as exc:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            print(f"[agent] failed to spool native item acknowledgement: {exc}")


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
    last_reset_generation: int | None = None

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
                "queued_account_ids": sorted(slot.queued_account_ids),
                "engine_reservation_mode": str(
                    read_engine_reservation_ready(cfg["csgo_dir"]).get("mode") or ""
                ),
                "started_match_id": slot.match_id if slot.started else 0,
                # Live score/team data lets the desktop GC mirror Operation
                # round-win progress during the match instead of waiting for
                # the final MatchEndRunRewardDrops fallback.
                "ct_score": slot.ct_score,
                "t_score": slot.t_score,
                "player_teams": {
                    str(account_id): team
                    for account_id, team in slot.player_teams.items()
                    if account_id in slot.expected_account_ids
                },
                "player_rounds_won": {
                    str(account_id): int(rounds)
                    for account_id, rounds in slot.player_rounds_won.items()
                    if account_id in slot.expected_account_ids
                },
            }
            try:
                reply = post_json(base + "/matchmaking/server/heartbeat", body)
                spool_item_acknowledgements(cfg, reply.get("item_acks"))
                reset_generation = int(reply.get("reset_generation") or 0)
                if last_reset_generation is None:
                    last_reset_generation = reset_generation
                elif reset_generation != last_reset_generation:
                    print(
                        f"[agent] REVIVAL_ADMIN_RESET_V1 generation "
                        f"{last_reset_generation}->{reset_generation}; stopping live srcds"
                    )
                    last_reset_generation = reset_generation
                    slot.stop()

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
