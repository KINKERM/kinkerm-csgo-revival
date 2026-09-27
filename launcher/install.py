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
import runpy
import sys
import zipfile
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error

# ==========================================================================
# HOST CONFIG - edit these two once, then hand this file to your friends.
# ==========================================================================
# Your inventory server (Tailscale Funnel public HTTPS URL -> local :8787):
SERVER_URL = "https://cuckersfun.tail52305f.ts.net"
# The published pack zip (a GitHub Release asset works great). It must extract
# so that csgo_gc/csgo_gc.dll / config.txt / items_game.txt land in the CS:GO install.
# See launcher/build_pack.py to build & upload it.
PACK_URL = "https://github.com/KINKERM/CSGO-revival-public/releases/download/csgorevival/csgo-revival-pack.zip"
INSERTION2_WORKSHOP_IDS = ("2395333051", "2760936305")
STEAMCMD_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
SEVENZR_URL = "https://github.com/ip7z/7zip/releases/download/26.03/7zr.exe"
# ==========================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
CSGO_APP_DIRS = ("csgo legacy", "Counter-Strike Global Offensive")


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
        for dirname in CSGO_APP_DIRS:
            candidate = os.path.join(steamapps, "common", dirname)
            if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "csgo")):
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


def _find_7zip() -> str | None:
    candidates = [
        shutil.which("7z"),
        shutil.which("7zz"),
        shutil.which("7za"),
    ]
    if sys.platform.startswith("win"):
        candidates += [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "7-Zip", "7z.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "7-Zip", "7z.exe"),
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path

    # Fully unattended fallback: fetch the official standalone 7zr.exe.
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        tools_dir = os.path.join(base, "CSGO-Revival", "tools")
        os.makedirs(tools_dir, exist_ok=True)
        sevenzr = os.path.join(tools_dir, "7zr.exe")
        if not os.path.isfile(sevenzr):
            try:
                log("downloading official 7-Zip extractor helper...")
                with open(sevenzr, "wb") as fh:
                    fh.write(download(SEVENZR_URL))
            except Exception as exc:
                log(f"could not download 7-Zip extractor helper: {exc}")
                return None
        return sevenzr if os.path.isfile(sevenzr) else None

    return None


def _steamcmd_item_dir(workshop_id: str) -> str | None:
    if not sys.platform.startswith("win"):
        return None

    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    steamcmd_dir = os.path.join(base, "CSGO-Revival", "steamcmd")
    exe = os.path.join(steamcmd_dir, "steamcmd.exe")

    if not os.path.isfile(exe):
        log("downloading Valve SteamCMD (no CS2 install required)...")
        os.makedirs(steamcmd_dir, exist_ok=True)
        blob = download(STEAMCMD_URL)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            zf.extractall(steamcmd_dir)

    log(f"downloading Insertion II Workshop item {workshop_id} with SteamCMD...")
    proc = subprocess.run(
        [
            exe,
            "+login", "anonymous",
            "+workshop_download_item", "730", workshop_id, "validate",
            "+quit",
        ],
        cwd=steamcmd_dir,
        timeout=900,
    )
    if proc.returncode:
        log(f"SteamCMD exited with code {proc.returncode}")
        return None

    item_dir = os.path.join(
        steamcmd_dir, "steamapps", "workshop", "content", "730", workshop_id
    )
    return item_dir if os.path.isdir(item_dir) else None


def _workshop_item_dirs() -> list[str]:
    root = steam_root()
    if not root:
        return []
    out: list[str] = []
    for steamapps in library_paths(root):
        base = os.path.join(steamapps, "workshop", "content", "730")
        for wid in INSERTION2_WORKSHOP_IDS:
            item = os.path.join(base, wid)
            if os.path.isdir(item):
                out.append(item)
    return out


def _copy_insertion2_payload(src_root: str, csgo_dir: str) -> bool:
    """Install the complete Insertion II payload, not only the BSP."""
    bsp = ""
    for base, dirs, files in os.walk(src_root):
        for name in files:
            if name.lower() == "cs_insertion2.bsp":
                bsp = os.path.join(base, name)
                break
        if bsp:
            break
    if not bsp:
        return False

    csgo_root = os.path.join(csgo_dir, "csgo")
    maps_dir = os.path.join(csgo_root, "maps")
    os.makedirs(maps_dir, exist_ok=True)

    # Copy every map companion sitting next to the BSP. The NAV is required in
    # practice: otherwise Legacy Source can sit forever at
    # "Downloading maps/cs_insertion2.nav... 0%".
    bsp_dir = os.path.dirname(bsp)
    copied = []
    for name in os.listdir(bsp_dir):
        low = name.lower()
        if low.startswith("cs_insertion2."):
            src = os.path.join(bsp_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(maps_dir, name))
                copied.append(name)

    # Determine the payload's csgo/game root from the directory that owns maps/.
    payload_game_root = os.path.dirname(bsp_dir) if os.path.basename(bsp_dir).lower() == "maps" else src_root

    # Preserve optional supporting content from Workshop/legacy archives.
    # Copying these directories is safe because this payload is dedicated to
    # Insertion II and avoids missing radar/overview/material resources.
    for folder in ("materials", "models", "resource", "scripts", "sound"):
        src = os.path.join(payload_game_root, folder)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(csgo_root, folder), dirs_exist_ok=True)

    # Some archives add map sidecars under a second maps/ path.
    src_maps = os.path.join(payload_game_root, "maps")
    if os.path.isdir(src_maps) and os.path.normcase(src_maps) != os.path.normcase(bsp_dir):
        for name in os.listdir(src_maps):
            low = name.lower()
            if low.startswith("cs_insertion2."):
                src = os.path.join(src_maps, name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(maps_dir, name))
                    if name not in copied:
                        copied.append(name)

    required_bsp = os.path.join(maps_dir, "cs_insertion2.bsp")
    if not os.path.isfile(required_bsp):
        return False

    log("Insertion II payload installed: " + ", ".join(sorted(copied)))
    return True


def _extract_workshop_legacy(archive: str, dest: str) -> bool:
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
            return True
    except (OSError, zipfile.BadZipFile):
        pass

    seven = _find_7zip()
    if not seven:
        return False
    proc = subprocess.run(
        [seven, "x", "-y", f"-o{dest}", archive],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def install_insertion2(csgo_dir: str) -> None:
    maps_dir = os.path.join(csgo_dir, "csgo", "maps")
    target = os.path.join(maps_dir, "cs_insertion2.bsp")
    if os.path.isfile(target):
        log("Insertion II already installed.")
        return

    def try_cache() -> bool:
        for item_dir in _workshop_item_dirs():
            if _copy_insertion2_payload(item_dir, csgo_dir):
                return True
            archives = [
                os.path.join(item_dir, n)
                for n in os.listdir(item_dir)
                if n.lower().endswith("legacy.bin")
            ]
            for archive in archives:
                with tempfile.TemporaryDirectory(prefix="revival-insertion2-") as tmp:
                    if _extract_workshop_legacy(archive, tmp) and _copy_insertion2_payload(tmp, csgo_dir):
                        return True
        return False

    if try_cache():
        log("installed Insertion II from your existing Steam Workshop cache.")
        return

    # Direct path: use Valve SteamCMD so users do NOT need the CS2 client installed.
    for wid in INSERTION2_WORKSHOP_IDS:
        item_dir = _steamcmd_item_dir(wid)
        if not item_dir:
            continue
        if _copy_insertion2_payload(item_dir, csgo_dir):
            log("installed Insertion II directly with SteamCMD.")
            return

        archives = [
            os.path.join(item_dir, n)
            for n in os.listdir(item_dir)
            if n.lower().endswith("legacy.bin")
        ]
        for archive in archives:
            with tempfile.TemporaryDirectory(prefix="revival-insertion2-") as tmp:
                if _extract_workshop_legacy(archive, tmp) and _copy_insertion2_payload(tmp, csgo_dir):
                    log("installed Insertion II directly with SteamCMD.")
                    return

    log("SteamCMD downloaded the Workshop item but cs_insertion2.bsp could not be extracted.")
    log(f"Final required file: {target}")
    sys.exit(4)


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

    # Legacy PBIN has fixed per-script capacities. Mirror the host repacker's
    # compaction so public installs fit the same slots as the tested host build.
    xml_path = os.path.join(stage_dir, "layout", "mainmenu_play.xml")
    js_path = os.path.join(stage_dir, "scripts", "mainmenu_play.js")
    css_path = os.path.join(stage_dir, "styles", "mainmenu_play.css")
    operation_js = os.path.join(stage_dir, "scripts", "operation", "operation_mainmenu.js")
    mission_card_js = os.path.join(stage_dir, "scripts", "operation", "operation_mission_card.js")
    operation_missions_js = os.path.join(stage_dir, "scripts", "operation", "operation_missions.js")
    operation_util_js = os.path.join(stage_dir, "scripts", "operation", "operation_util.js")
    mission_context_js = os.path.join(stage_dir, "scripts", "context_menus", "context_menu_select_mission_card.js")
    activate_mission_js = os.path.join(stage_dir, "scripts", "popups", "popup_activate_mission.js")
    hud_mission_js = os.path.join(stage_dir, "scripts", "hud", "hudmissionpanel.js")
    mainmenu_root_js = os.path.join(stage_dir, "scripts", "mainmenu.js")
    mainmenu_store_js = os.path.join(stage_dir, "scripts", "mainmenu_store.js")
    decodable_js = os.path.join(stage_dir, "scripts", "popups", "popup_capability_decodable.js")
    inspect_async_js = os.path.join(stage_dir, "scripts", "popups", "popup_inspect_async-bar.js")
    inspect_purchase_js = os.path.join(stage_dir, "scripts", "popups", "popup_inspect_purchase-bar.js")

    with open(xml_path, "r", encoding="utf-8-sig") as fh:
        xml = fh.read()
    xml = re.sub(r">\s+<", "><", xml)
    with open(xml_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(xml)

    # Match REPACK_PANORAMA.ps1 exactly: first do the conservative trim pass on
    # every Operation/mission file touched by the revival.
    conservative_paths = (
        js_path,
        css_path,
        operation_js,
        mission_card_js,
        operation_missions_js,
        operation_util_js,
        mission_context_js,
        activate_mission_js,
        hud_mission_js,
    )
    for path in conservative_paths:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = fh.read()
        compact = "\n".join(
            line.rstrip() for line in raw.splitlines() if line.strip()
        )
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(compact)

    # Tight PBIN slots need the same aggressive pass as the host compiler:
    # strip indentation, blank lines, and whole-line // comments only.
    aggressive_paths = (
        activate_mission_js,
        hud_mission_js,
        operation_js,
        mission_card_js,
        mainmenu_root_js,
        mainmenu_store_js,
        decodable_js,
        inspect_async_js,
        inspect_purchase_js,
    )
    for path in aggressive_paths:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = fh.read()
        compact = "\n".join(
            line.strip()
            for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("//")
        )
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(compact)

    log(
        "PBIN compaction matched host repacker; popup_activate_mission.js="
        + str(os.path.getsize(activate_mission_js))
        + " B"
    )

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
    if (b"m_revivalValidationMapGroup" not in packed_bytes
            or b"_GetRevivalValidationMapGroup" not in packed_bytes):
        log("rebuilt code.pbin is missing real stock-mapgroup Revival markers")
        sys.exit(3)
    if b"itemsByCategory.operation = _OperationStoreSetupObj" not in packed_bytes:
        log("rebuilt code.pbin is missing the Operation-only shop")
        sys.exit(3)
    if b"revivalkeylesscase" not in packed_bytes:
        log("rebuilt code.pbin is missing keyless case support")
        sys.exit(3)

    log("Panorama code.pbin rebuilt and release markers verified")


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
    runtime_dir = os.path.join(csgo_dir, "revival")
    os.makedirs(runtime_dir, exist_ok=True)
    cfg = os.path.join(runtime_dir, "launcher.cfg")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(
            "# auto-generated by install.py\n"
            f"server_url={SERVER_URL}\n"
            f"steam_id={steam_id}\n"
            f"csgo_dir={csgo_dir}\n"
            "game_args=-steam -game csgo -novid\n"
            "launch_game=1\n"
            "sync_token=\n"
            "data_epoch=0\n"
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
        log("find the CS:GO Legacy folder containing the csgo subfolder.")
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
    install_insertion2(csgo_dir)
    cfg = write_launcher_cfg(csgo_dir, steam_id)

    log("setup complete. Syncing inventory and launching...")
    # The public launcher runtime is shipped inside the release pack, so
    # install.py is genuinely the only file a new player needs beforehand.
    launcher = os.path.join(csgo_dir, "revival", "launcher.py")
    if os.path.exists(launcher):
        # Run the installed launcher in this same Python process. This avoids a
        # Windows argv quoting edge case where "C:\\Program Files\\..." was
        # being split and Python tried to open "C:\\program".
        sys.argv = [launcher, cfg]
        runpy.run_path(launcher, run_name="__main__")
    else:
        log("release pack is incomplete: revival/launcher.py is missing.")
        sys.exit(5)


if __name__ == "__main__":
    main()
