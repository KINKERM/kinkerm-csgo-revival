#!/usr/bin/env python3
"""CS:GO Revival - one-click auto-installer for friends.

A friend runs this ONE file and it will, with no manual editing:
  1. auto-detect their CS:GO Legacy install (via Steam's libraryfolders.vdf)
  2. auto-detect their SteamID64 (via Steam's loginusers.vdf / registry)
  3. download the revival "pack" (patched csgo_gc + our tuned config.txt + our
     custom items_game.txt with Kinkerm's Case and everything we added) and
     overlay the revival files into the CS:GO install without replacing stock csgo.exe
  4. write launcher.cfg for them (server pre-filled)
  5. sync their inventory from the server and launch the game (Steam P2P ready)

Everyone who runs this ends up on the *same* csgo_gc build + config + items, which
is exactly what Steam P2P lobbies need to be compatible.

HOST: edit the two constants below once, then share this single file (plus a
published pack zip - see launcher/build_pack.py) with your friends.

Standard library only. Works on Windows / Linux / macOS.
"""

from __future__ import annotations

import io
import os
import re
import sys
import zipfile
import shutil
import subprocess
import urllib.request
import urllib.error

# ==========================================================================
# HOST CONFIG - edit these two once, then hand this file to your friends.
# ==========================================================================
# Your inventory server (Tailscale Funnel public HTTPS URL -> local :8787):
SERVER_URL = "https://cuckersfun.tail52305f.ts.net"
# The published pack zip (a GitHub Release asset works great). It must extract
# so that csgo_gc.dll / config.txt / items_game.txt land in the CS:GO install.
# See launcher/build_pack.py to build & upload it.
PACK_URL = "https://github.com/KINKERM/kinkerm-csgo-revival/releases/latest/download/csgo-revival-pack.zip"
# ==========================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
CSGO_APP = "Counter-Strike Global Offensive"


def log(msg: str) -> None:
    print(f"[install] {msg}", flush=True)


# ---------------------------------------------------------------------------
# tiny VDF helpers (regex-based; good enough for the two files we read)
# ---------------------------------------------------------------------------
def _vdf_pairs(text: str) -> list[tuple[str, str]]:
    return re.findall(r'"([^"]+)"\s+"([^"]*)"', text)


def steam_root() -> str | None:
    candidates: list[str] = []
    if sys.platform.startswith("win"):
        try:
            import winreg  # type: ignore
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        val, _ = winreg.QueryValueEx(k, "SteamPath")
                        if val:
                            candidates.append(val)
                except OSError:
                    pass
        except Exception:
            pass
        candidates += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    elif sys.platform == "darwin":
        candidates.append(os.path.expanduser("~/Library/Application Support/Steam"))
    else:
        candidates += [
            os.path.expanduser("~/.steam/steam"),
            os.path.expanduser("~/.local/share/Steam"),
            os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam"),  # flatpak
        ]
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "steamapps")):
            return os.path.normpath(c)
    return None


def library_paths(root: str) -> list[str]:
    """All Steam library roots (steamapps folders) from libraryfolders.vdf."""
    libs = [os.path.join(root, "steamapps")]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for key, val in _vdf_pairs(text):
            if key == "path":
                p = os.path.join(val, "steamapps")
                if os.path.isdir(p):
                    libs.append(os.path.normpath(p))
    # de-dup preserving order
    seen, out = set(), []
    for p in libs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def find_csgo_dir() -> str | None:
    root = steam_root()
    if not root:
        return None
    for steamapps in library_paths(root):
        candidate = os.path.join(steamapps, "common", CSGO_APP)
        if os.path.isdir(candidate):
            return candidate
    return None


def find_steamid64() -> str | None:
    root = steam_root()
    if not root:
        return None
    login = os.path.join(root, "config", "loginusers.vdf")
    if not os.path.exists(login):
        return None
    with open(login, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    # blocks look like:  "76561198...."  {  ... "MostRecent" "1" ... }
    best = None
    for m in re.finditer(r'"(7656119\d{10})"\s*\{(.*?)\}', text, re.DOTALL):
        sid, body = m.group(1), m.group(2)
        if best is None:
            best = sid
        if re.search(r'"MostRecent"\s+"1"', body):
            return sid
    return best


def prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"[install] {question}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


# ---------------------------------------------------------------------------
# download + extract the pack
# ---------------------------------------------------------------------------
def download(url: str) -> bytes:
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "csgo-revival-installer"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def repack_panorama(csgo_dir: str, zf: zipfile.ZipFile) -> None:
    panorama_dir = os.path.join(csgo_dir, "csgo", "panorama")
    pbin_tool = os.path.join(panorama_dir, "pbin.py")
    code_pbin = os.path.join(panorama_dir, "code.pbin")
    original_pbin = os.path.join(panorama_dir, "_code.pbin")
    table_file = os.path.join(panorama_dir, "code.pbin.table")
    stage_dir = os.path.join(panorama_dir, "panorama")

    for path, label in ((pbin_tool, "pbin.py"), (code_pbin, "code.pbin")):
        if not os.path.isfile(path):
            log(f"Panorama repack failed: missing {label}: {path}")
            sys.exit(3)

    if not os.path.isfile(original_pbin):
        shutil.copy2(code_pbin, original_pbin)
        log("saved original Panorama archive as _code.pbin")

    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir)
    if os.path.isfile(table_file):
        os.remove(table_file)

    unpack = subprocess.run(
        [sys.executable, pbin_tool, "unpack", "_code.pbin"],
        cwd=panorama_dir,
    )
    if unpack.returncode:
        log(f"pbin.py unpack failed with exit code {unpack.returncode}")
        sys.exit(3)

    # Overlay the Panorama source directly from the downloaded pack into the
    # freshly unpacked PBIN staging tree. This also preserves nested panorama/
    # content without relying on the loose install tree.
    prefix = "csgo/panorama/"
    for member in zf.infolist():
        name = member.filename.replace("\\", "/")
        if member.is_dir() or not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        if not rel or rel in {"pbin.py", "code.pbin", "_code.pbin", "code.pbin.table"}:
            continue
        dest = os.path.abspath(os.path.join(stage_dir, rel))
        base = os.path.abspath(stage_dir)
        if not dest.startswith(base + os.sep):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)

    # PBIN slots are fixed-size. Compact only the matchmaking files modified by
    # this branch so their packed payloads stay below the original slot sizes.
    xml_path = os.path.join(stage_dir, "layout", "mainmenu_play.xml")
    js_path = os.path.join(stage_dir, "scripts", "mainmenu_play.js")
    css_path = os.path.join(stage_dir, "styles", "mainmenu_play.css")

    with open(xml_path, "r", encoding="utf-8-sig") as fh:
        xml = fh.read()
    xml = re.sub(r">\s+<", "><", xml)
    with open(xml_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(xml)

    for path in (js_path, css_path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = fh.read()
        compact = "\n".join(
            line.rstrip() for line in raw.splitlines() if line.strip()
        )
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(compact)

    packed = subprocess.run([sys.executable, pbin_tool, "pack"], cwd=panorama_dir)
    if packed.returncode:
        log(f"pbin.py pack failed with exit code {packed.returncode}")
        sys.exit(3)

    patched = subprocess.run(
        [sys.executable, pbin_tool, "patch_panorama"],
        cwd=panorama_dir,
    )
    if patched.returncode:
        log(f"pbin.py patch_panorama failed with exit code {patched.returncode}")
        sys.exit(3)

    with open(code_pbin, "rb") as fh:
        packed_bytes = fh.read()
    if (b"RevivalSingleQueuePanel" not in packed_bytes
            or b"Revival has exactly one official Competitive queue" not in packed_bytes):
        log("rebuilt code.pbin is missing Revival Competitive queue markers")
        sys.exit(3)

    log("Panorama code.pbin rebuilt and panorama.dll patch verified")


def install_pack(csgo_dir: str) -> None:
    if "CHANGE_ME" in PACK_URL or "CHANGE_ME" in SERVER_URL:
        log("HOST hasn't set SERVER_URL / PACK_URL in install.py yet - ask them.")
        sys.exit(1)
    try:
        blob = download(PACK_URL)
    except urllib.error.URLError as exc:
        log(f"could not download the pack: {exc}")
        sys.exit(2)

    log(f"got {len(blob)} bytes; extracting into {csgo_dir}")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        # basic zip-slip guard
        base = os.path.abspath(csgo_dir)
        for member in zf.namelist():
            dest = os.path.abspath(os.path.join(csgo_dir, member))
            if not dest.startswith(base + os.sep) and dest != base:
                log(f"skipping unsafe path in pack: {member}")
                continue
        zf.extractall(csgo_dir)
        repack_panorama(csgo_dir, zf)
    log("pack installed (runtime + GC/config/items + rebuilt Panorama code.pbin).")


def write_launcher_cfg(csgo_dir: str, steam_id: str) -> str:
    cfg = os.path.join(HERE, "launcher.cfg")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(
            "# auto-generated by install.py\n"
            f"server_url={SERVER_URL}\n"
            f"steam_id={steam_id}\n"
            f"csgo_dir={csgo_dir}\n"
            "game_args=-steam -game csgo -novid\n"
            "launch_game=1\n"
            "sync_token=\n"
        )
    log(f"wrote {cfg}")
    return cfg


def main() -> None:
    print("=" * 62)
    print("  CS:GO Revival - automatic setup")
    print("=" * 62)

    csgo_dir = find_csgo_dir()
    if csgo_dir:
        log(f"found CS:GO: {csgo_dir}")
    else:
        log("could not auto-detect your CS:GO Legacy install.")
        log("open Steam -> right-click CS2 -> Properties -> Betas -> csgo_legacy,")
        log("then find the folder containing csgo.exe / csgo_linux64.")
        csgo_dir = prompt("path to your CS:GO install folder")
        if not csgo_dir or not os.path.isdir(csgo_dir):
            log("no valid folder given, aborting.")
            sys.exit(1)

    steam_id = find_steamid64()
    if steam_id:
        log(f"found SteamID64: {steam_id}")
        if prompt("is that your account? (y/n)", "y").lower().startswith("n"):
            steam_id = ""
    if not steam_id:
        steam_id = prompt("enter your SteamID64 (17 digits, from steamid.io)")
        if not re.fullmatch(r"7656119\d{10}", steam_id or ""):
            log("that doesn't look like a SteamID64, aborting.")
            sys.exit(1)

    install_pack(csgo_dir)
    write_launcher_cfg(csgo_dir, steam_id)

    log("setup complete. Syncing inventory and launching...")
    # hand off to the normal launcher (sync inventory + launch the game)
    launcher = os.path.join(HERE, "launcher.py")
    if os.path.exists(launcher):
        os.execv(sys.executable, [sys.executable, launcher])
    else:
        log("launcher.py not found next to install.py - run it manually to play.")


if __name__ == "__main__":
    main()
