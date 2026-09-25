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

GAME_OVER_PATTERNS = (
    re.compile(r'World triggered "Game_Over"', re.I),
    re.compile(r'Game Over:', re.I),
    re.compile(r'Match_End', re.I),
)
TEAM_SCORE_RE = re.compile(r'Team "(CT|TERRORIST)" scored "(\d+)"', re.I)
STEAM2_RE = re.compile(r'STEAM_[0-5]:(\d):(\d+)', re.I)
STEAM3_RE = re.compile(r'\[U:1:(\d+)\]', re.I)


def account_id_from_log_line(line: str) -> int:
    if "entered the game" not in line.lower():
        return 0
    m = STEAM3_RE.search(line)
    if m:
        return int(m.group(1))
    m = STEAM2_RE.search(line)
    if m:
        return int(m.group(2)) * 2 + int(m.group(1))
    return 0


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
    cfg.setdefault("accept_timeout_seconds", 90)
    cfg.setdefault("post_match_grace_seconds", 25)
    return cfg


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
    path = os.path.join(cfg_dir, "revival_competitive.cfg")
    token = str(steam_account_token or "").replace('"', '').strip()
    text = r"""hostname "Kinkerm CS:GO Revival Competitive"
sv_lan 0
sv_password ""
sv_cheats 0
sv_pure 0
sv_allow_votes 1
sv_deadtalk 1
sv_hibernate_when_empty 0
sv_hibernate_postgame_delay 5
sv_allow_lobby_connect_only 0
sv_setsteamaccount "__REVIVAL_STEAM_TOKEN__"
log on

bot_quota 10
bot_quota_mode fill
bot_join_after_player 0
bot_auto_vacate 1
bot_join_team any
mp_autokick 0
mp_autoteambalance 1
mp_limitteams 2
mp_friendlyfire 1
sv_game_mode_flags 0
mp_maxrounds 30
mp_overtime_enable 1
mp_match_can_clinch 1
mp_do_warmup_period 1
mp_warmuptime 3600
mp_warmup_pausetimer 1
mp_freezetime 15
mp_roundtime 1.92
mp_roundtime_defuse 1.92
mp_match_restart_delay 15
mp_endmatch_votenextmap 0
mp_match_end_restart 0
"""
    text = text.replace("__REVIVAL_STEAM_TOKEN__", token)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def reservation_paths(csgo_dir: str) -> tuple[str, str]:
    gc_dir = os.path.join(csgo_dir, "csgo_gc")
    os.makedirs(gc_dir, exist_ok=True)
    return (
        os.path.join(gc_dir, "server_reservation.txt"),
        os.path.join(gc_dir, "server_reservation_response.txt"),
    )


def write_native_reservation(
    csgo_dir: str, assignment: dict, *, clear_response: bool = True
) -> None:
    request_path, response_path = reservation_paths(csgo_dir)
    if clear_response:
        try:
            os.remove(response_path)
        except OSError:
            pass

    account_ids = [
        int(x) for x in assignment.get("account_ids", []) if int(x) > 0
    ]
    lines = [
        f"match_id={int(assignment.get('match_id') or 0)}",
        f"game_type={int(assignment.get('game_type') or 8)}",
        f"server_version={int(assignment.get('client_version') or 0)}",
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
                if key in ("match_id", "reservation_id"):
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


def send_local_rcon(port: int, password: str, command: str) -> None:
    """Send one command to the local Source server without touching its stdin."""
    with socket.create_connection(("127.0.0.1", int(port)), timeout=3.0) as sock:
        sock.settimeout(3.0)
        sock.sendall(_rcon_packet(101, 3, password))  # SERVERDATA_AUTH

        authed = False
        for _ in range(3):
            request_id, packet_type, _ = _recv_rcon(sock)
            if packet_type == 2:  # SERVERDATA_AUTH_RESPONSE
                if request_id == -1:
                    raise PermissionError("RCON authentication failed")
                authed = True
                break
        if not authed:
            raise ConnectionError("RCON authentication response not received")

        sock.sendall(_rcon_packet(102, 2, command))  # SERVERDATA_EXECCOMMAND


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
        self.reserved_account_ids: set[int] = set()
        self.ct_score = 0
        self.t_score = 0
        self.expected_account_ids: set[int] = set()
        self.connected_account_ids: set[int] = set()
        self.ready_at = 0.0
        self.started = False
        self._lock = threading.RLock()
        self._ended = False
        self.rcon_password = secrets.token_hex(16)

    def alive(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def start(self, assignment: dict) -> None:
        match_id = int(assignment.get("match_id") or 0)
        if not match_id:
            return
        with self._lock:
            if self.alive() and self.match_id == match_id:
                new_accounts = {
                    int(x) for x in assignment.get("account_ids", []) if int(x) > 0
                }
                added = new_accounts.difference(self.expected_account_ids)
                self.expected_account_ids.update(new_accounts)
                if added:
                    # Keep the request file current. If ServerGC has not read
                    # it yet, early drop-ins become part of the initial native
                    # reservation. Never delete a 9106 response for a live
                    # server while doing this.
                    write_native_reservation(
                        self.cfg["csgo_dir"], assignment, clear_response=False
                    )
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
            write_native_reservation(self.cfg["csgo_dir"], assignment)
            cmd = [
                srcds,
                "-game", "csgo",
                "-console",
                "-usercon",
                "-secure",
                "-tickrate", "64",
                "-port", str(int(self.cfg["local_port"])),
                "-maxplayers_override", "10",
                "+game_type", "0",
                "+game_mode", "1",
                "+map", map_name,
                "+exec", "revival_competitive.cfg",
                "+rcon_password", self.rcon_password,
            ]
            extra = str(self.cfg.get("extra_srcds_args") or "").strip()
            if extra:
                cmd.extend(extra.split())

            print(f"[agent] starting match {match_id} on {map_name} @ 64 tick")
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                cmd,
                cwd=self.cfg["csgo_dir"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # Do NOT redirect stdin. CTextConsoleWin32 calls
                # GetNumberOfConsoleInputEvents() on STD_INPUT_HANDLE and
                # hard-errors if it is a Python pipe.
                stdin=None,
                text=True,
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            set_above_normal(self.proc)
            self.match_id = match_id
            self.ready_match_id = 0
            self.reservation_id = 0
            self.reserved_account_ids.clear()
            self.ct_score = 0
            self.t_score = 0
            self.expected_account_ids = {
                int(x) for x in assignment.get("account_ids", []) if int(x) > 0
            }
            self.connected_account_ids.clear()
            self.ready_at = 0.0
            self.started = False
            self._ended = False
            threading.Thread(target=self._reader, daemon=True, name="srcds-output").start()
            threading.Thread(target=self._mark_ready_after_boot, daemon=True, name="srcds-ready").start()

    def _mark_ready_after_boot(self) -> None:
        # Do not advertise the server until the injected ServerGC has sent native
        # 9105 to srcds and captured srcds' native 9106 reservation response.
        # This guarantees clients receive the exact reservation id the game
        # server accepted instead of a coordinator-invented cookie.
        deadline = time.monotonic() + 80.0
        while time.monotonic() < deadline:
            time.sleep(0.5)
            with self._lock:
                if not self.alive() or not self.match_id:
                    return
                match_id = self.match_id

            response = read_native_reservation_response(self.cfg["csgo_dir"])
            if (
                int(response.get("match_id") or 0) == match_id
                and int(response.get("reservation_id") or 0) > 0
            ):
                # Give Source a little extra time after accepting the reservation
                # to finish its map/network startup on slow hardware.
                time.sleep(1.5)
                with self._lock:
                    if not self.alive() or self.match_id != match_id:
                        return
                    self.reservation_id = int(response["reservation_id"])
                    self.reserved_account_ids = {
                        int(x) for x in str(response.get("account_ids") or "").split(",")
                        if x.strip().isdigit() and int(x) > 0
                    }
                    self.ready_match_id = match_id
                    self.ready_at = time.monotonic()
                    print(
                        f"[agent] native 9106 accepted match {match_id}; "
                        f"reservation={self.reservation_id}; waiting for "
                        "first human to enter (bots fill empty slots)"
                    )
                return

        print("[agent] srcds never produced native 9106 reservation response; "
              "coordinator will cancel/requeue this allocation")

    def _reader(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                print("[srcds] " + line)
            m = TEAM_SCORE_RE.search(line)
            if m:
                score = int(m.group(2))
                if m.group(1).upper() == "CT":
                    self.ct_score = score
                else:
                    self.t_score = score

            account_id = account_id_from_log_line(line)
            if account_id:
                self._player_entered(account_id)

            if any(p.search(line) for p in GAME_OVER_PATTERNS):
                self._report_end_once(
                    "game_over",
                    grace=float(self.cfg.get("post_match_grace_seconds", 25)),
                )
        code = proc.wait()
        if not self._ended and self.match_id:
            self._report_end_once(f"srcds_exit_{code}")

    def _player_entered(self, account_id: int) -> None:
        with self._lock:
            if self._ended or not self.match_id:
                return
            if self.expected_account_ids and account_id not in self.expected_account_ids:
                return
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
            if self.started or self._ended or not self.match_id:
                return
            self.started = True
            match_id = self.match_id
            proc = self.proc
            if proc and proc.poll() is None:
                try:
                    send_local_rcon(
                        int(self.cfg["local_port"]),
                        self.rcon_password,
                        "mp_warmup_pausetimer 0; mp_warmup_end",
                    )
                except Exception as exc:
                    print(f"[agent] failed to end warmup via RCON: {exc}")

        try:
            post_json(
                self.cfg["backend_url"].rstrip("/") + "/matchmaking/server/started",
                {"match_id": match_id},
            )
        except Exception as exc:
            print(f"[agent] start notification will retry via heartbeat: {exc}")
        print(f"[agent] first human entered; bot-filled match {match_id} started")

    def refresh_native_reservation_response(self) -> None:
        with self._lock:
            match_id = self.match_id
            old_reservation = self.reservation_id
            if not match_id or not self.alive():
                return

        response = read_native_reservation_response(self.cfg["csgo_dir"])
        if int(response.get("match_id") or 0) != match_id:
            return
        new_reservation = int(response.get("reservation_id") or 0)
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
            self.ready_match_id = match_id
            if new_reservation != old_reservation or membership_changed:
                print(
                    f"[agent] native reservation refreshed for match {match_id}: "
                    f"reservation={new_reservation}, "
                    f"accounts={','.join(str(x) for x in sorted(acknowledged))}"
                )

    def check_accept_timeout(self) -> None:
        with self._lock:
            if (
                self._ended
                or self.started
                or not self.ready_at
                or not self.match_id
            ):
                return
            timeout = float(self.cfg.get("accept_timeout_seconds", 90))
            expired = time.monotonic() - self.ready_at >= timeout
        if expired:
            print("[agent] nobody joined the new server before timeout; cancelling reservation")
            self._report_end_once("accept_timeout")

    def _report_end_once(self, reason: str, grace: float = 0.0) -> None:
        with self._lock:
            if self._ended or not self.match_id:
                return
            self._ended = True
            match_id = self.match_id
            result = {
                "reason": reason,
                "ct_score": self.ct_score,
                "t_score": self.t_score,
                "connected_account_ids": sorted(self.connected_account_ids),
            }

        def finish() -> None:
            if grace > 0:
                print(f"[agent] keeping srcds alive {grace:.0f}s for end-match GC/drop delivery")
                time.sleep(grace)
            try:
                post_json(
                    self.cfg["backend_url"].rstrip("/") + "/matchmaking/server/ended",
                    {"match_id": match_id, "result": result},
                )
                print(f"[agent] reported match {match_id} end: {result}")
            except Exception as exc:
                print(f"[agent] failed to report match end: {exc}")
            self.stop()

        if grace > 0:
            threading.Thread(target=finish, daemon=True, name="match-end-grace").start()
        else:
            finish()

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        self.ready_match_id = 0
        self.reservation_id = 0
        self.reserved_account_ids.clear()
        self.match_id = 0
        self.ready_at = 0.0
        self.started = False
        self.expected_account_ids.clear()
        self.connected_account_ids.clear()
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

    print("[agent] installed matchmaking maps: " + ", ".join(maps))
    print(f"[agent] public tunnel: {cfg['public_host']}:{cfg['public_port']}")
    playit = maybe_start_playit(cfg)
    slot = ServerSlot(cfg)
    base = cfg["backend_url"].rstrip("/")

    try:
        while True:
            slot.refresh_native_reservation_response()
            body = {
                "agent_id": cfg["agent_id"],
                "public_host": cfg["public_host"],
                "public_port": int(cfg["public_port"]),
                "maps": maps,
                "ready_match_id": slot.ready_match_id,
                "reservation_id": slot.reservation_id,
                "reserved_account_ids": sorted(slot.reserved_account_ids),
                "started_match_id": slot.match_id if slot.started else 0,
            }
            try:
                reply = post_json(base + "/matchmaking/server/heartbeat", body)
                assignment = reply.get("assignment")
                if isinstance(assignment, dict):
                    slot.start(assignment)
                elif slot.alive() and not slot.started:
                    # The coordinator can withdraw an allocation if native 9106
                    # never arrives. Do not leave a dead warmup server consuming
                    # RAM/CPU on the 4 GB laptop.
                    print("[agent] coordinator withdrew unstarted allocation; stopping srcds")
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
