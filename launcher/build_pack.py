#!/usr/bin/env python3
"""HOST helper: assemble the revival "pack" zip that install.py hands to friends.

The pack extracts directly over a CS:GO Legacy install so every friend ends up
with an IDENTICAL setup (required for Steam P2P lobbies):

  <root>/csgo_gc/csgo_gc.dll               <- patched GC loaded by the x86 launcher
  <root>/csgo_revival.exe                   <- patched client launcher
  Dedicated-server launchers are intentionally NOT shipped in the public pack.
  <root>/csgo_gc/config.txt                 <- this repo's tuned gc-config/config.txt
  <root>/csgo/scripts/items/items_game.txt  <- this repo's custom items_game.txt

You can point --csgo-gc-dir at EITHER:
  * an extracted csgo_gc release folder (runtime files already at its root), OR
  * your csgo_gc SOURCE/BUILD tree (e.g. csgo_gc_clean) - this script will then
    auto-harvest the built runtime files out of build\\...\\Release\\ for you.

Usage:
  python3 build_pack.py --csgo-gc-dir "C:\\Users\\you\\Documents\\csgo_gc_clean"
  python3 build_pack.py --csgo-gc-dir /path/to/extracted/release [--out pack.zip]

Then upload the resulting zip to a GitHub Release and point install.py's PACK_URL
at it (e.g. .../releases/latest/download/csgo-revival-pack.zip).
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# csgo_gc runtime files, per platform. The GC library is essential; the launcher
# executables replace csgo.exe / srcds.exe / csgo_linux64 etc.
GC_LIBS = ("csgo_gc.dll", "libcsgo_gc.so", "libcsgo_gc_client.so",
           "libcsgo_gc.dylib", "csgo_gc.dylib")
CLIENT_LAUNCHERS = ("csgo.exe", "csgo_linux64", "csgo_osx64",
                    "csgo_win64.exe", "csgo_linux")
SERVER_LAUNCHERS = ("srcds.exe", "srcds_linux", "srcds_win64.exe",
                    "srcds_linux64")
LAUNCHERS = CLIENT_LAUNCHERS + SERVER_LAUNCHERS
RUNTIME_NAMES = set(GC_LIBS) | set(LAUNCHERS)

# dirs we never descend into
SKIP_DIRS = {".git", ".github", ".vs", ".idea", "__pycache__"}


def harvest(src_dir: str) -> dict[str, str]:
    """Find built runtime files anywhere under src_dir; prefer Release over Debug.

    Returns {filename_at_pack_root: absolute_source_path}.
    """
    best: dict[str, tuple[int, str]] = {}
    for base, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        low = base.lower()
        for name in files:
            if name not in RUNTIME_NAMES:
                continue
            if "cmakefiles" in low:
                score = -1
            elif "release" in low:
                score = 3
            elif os.path.dirname(os.path.abspath(os.path.join(base, name))) == os.path.abspath(src_dir):
                score = 2          # sitting right at the root (extracted release)
            elif "debug" in low:
                score = 1
            else:
                score = 2
            prev = best.get(name)
            if prev is None or score > prev[0]:
                best[name] = (score, os.path.join(base, name))
    return {name: path for name, (s, path) in best.items() if s >= 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csgo-gc-dir", required=True,
                    help="your extracted csgo_gc release folder OR your csgo_gc "
                         "source/build tree (build outputs are auto-harvested)")
    ap.add_argument("--items-game", default=os.path.join(REPO, "items_game.txt"))
    ap.add_argument("--config", default=os.path.join(REPO, "gc-config", "config.txt"))
    ap.add_argument("--panorama", default=os.path.join(REPO, "panorama"),
                    help="panorama UI folder to ship as csgo/panorama (operation UI etc.)")
    ap.add_argument("--out", default=os.path.join(HERE, "csgo-revival-pack.zip"))
    ap.add_argument("--force", action="store_true",
                    help="build even if no csgo_gc runtime files were found")
    args = ap.parse_args()

    for path, what in ((args.csgo_gc_dir, "--csgo-gc-dir"),
                       (args.items_game, "items_game.txt"),
                       (args.config, "config.txt")):
        if not os.path.exists(path):
            print(f"[build_pack] missing {what}: {path}")
            sys.exit(1)

    runtime = harvest(args.csgo_gc_dir)
    gc_lib = [n for n in runtime if n in GC_LIBS]
    client_launchers = [n for n in runtime if n in CLIENT_LAUNCHERS]

    if runtime:
        print("[build_pack] harvested runtime files:")
        for name, path in sorted(runtime.items()):
            rel = os.path.relpath(path, args.csgo_gc_dir)
            print(f"    {name:22s} <- {rel}")
    if not gc_lib:
        print("[build_pack] ERROR: no csgo_gc library (csgo_gc.dll / libcsgo_gc.so) "
              "found anywhere under that folder.")
        print("[build_pack] Did the build succeed? Look for build\\csgo_gc\\Release\\csgo_gc.dll")
        print("[build_pack] and build\\launcher\\Release\\csgo.exe. Point --csgo-gc-dir at")
        print("[build_pack] the source/build tree, or at an extracted release.")
        if not args.force:
            sys.exit(3)
    elif not client_launchers:
        print("[build_pack] WARNING: found the GC library but no client launcher.")
        print("[build_pack] Make sure the csgo launcher target built too.")
        if not args.force:
            print("[build_pack] aborting; re-run with --force to pack anyway.")
            sys.exit(3)

    if "csgo_gc.dll" in runtime:
        with open(runtime["csgo_gc.dll"], "rb") as fh:
            dll_blob = fh.read()
        for marker in (
            b"REVIVAL_MM_BRIDGE_CLEAN_V1",
            b"REVIVAL_SERVER_RESERVATION_RETRY_V4",
            b"REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1",
            b"REVIVAL_SERVER_ID_EXPORT_V1",
            b"REVIVAL_CLIENT_COOKIE_RESERVE_V3",
            b"REVIVAL_CLIENT_DIRECT_UDP_V1",
            b"REVIVAL_CLIENT_READY_FLOW_V1",
            b"REVIVAL_CLIENT_ACCEPT_WATCH_V1",
            b"REVIVAL_CLIENT_DIRECT_ACCEPT_ROUTE_V2",
            b"REVIVAL_LIVE_DROPIN_ACCEPT_V6",
            b"REVIVAL_PARTY_QUEUE_ROSTER_V1",
            b"REVIVAL_PARTY_CLIENT_ADOPT_V2",
            b"REVIVAL_JOIN_IN_PROGRESS_G_V1",
            b"REVIVAL_PVP_MISSION_STATS_V1",
            b"REVIVAL_CUSTOM_PVP_MISSIONS_V1",
            b"REVIVAL_OPERATION_REPLAY_V1",
            b"REVIVAL_Q_SLOT_PAD_V1",
            b"REVIVAL_SERVER_ACCEPT_ROSTER_V1",
            b"REVIVAL_ENGINE_QUEUE_RESERVE_V1",
            b"REVIVAL_SERVER_LOCAL_SOCACHE_V1",
            b"REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1",
            b"REVIVAL_SERVER_PLAYER_AUTH_V1",
            b"REVIVAL_SERVER_REWARD_BRIDGE_V1",
            b"REVIVAL_REWARD_SPOOL_QUEUE_V1",
            b"REVIVAL_CLIENT_REWARD_BRIDGE_V1",
            b"REVIVAL_GUARANTEED_MATCH_DROPS_V1",
            b"REVIVAL_REPEATABLE_MISSIONS_V5",
            b"REVIVAL_NATIVE_ACTIVE_QUEST_V1",
            b"REVIVAL_OPERATION_SELECTION_BRIDGE_V2",
            b"REVIVAL_OPERATION_SCHEMA_V2",
            b"REVIVAL_OPERATION_CARD_PARSE_V4",
            b"REVIVAL_OPERATION_PROGRESS_CACHE_V1",
            b"REVIVAL_LIVE_OPERATION_ROUNDS_V1",
            b"REVIVAL_LIVE_OPERATION_FINAL_V1",
            b"REVIVAL_OPERATION_END_AUTHORITY_V1",
            b"REVIVAL_LIVE_OPERATION_NO_REASSERT_V1",
            b"REVIVAL_OPERATION_COMPLETION_PERSIST_V1",
            b"REVIVAL_STORAGE_UNITS_V1",
            b"REVIVAL_EARNED_DROPS_ONLY_V1",
            b"REVIVAL_KEYLESS_CASES_V1",
            b"REVIVAL_OPERATION_SUMMARY_REPAIR_V1",
            b"REVIVAL_SYNTHETIC_MATCH_END_V1",
            b"REVIVAL_NATIVE_DROP_REVEAL_V1",
            b"REVIVAL_NATIVE_ENDMATCH_UI_V1",
            b"REVIVAL_PROGRESS_BUNDLE_V2",
            b"REVIVAL_SERVER_ITEM_AUTHORITY_V1",
            b"REVIVAL_SERVER_DROP_IMPORT_V2",
            b"REVIVAL_NATIVE_RANK_STATE_V2",
            b"REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V3",
            b"REVIVAL_CLIENT_USERMESSAGE_UI_V1",
            b"REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V1",
            b"REVIVAL_NATIVE_DROP_CRASH_GUARD_V1",
            b"REVIVAL_NATIVE_DROP_TIMING_V3",
            b"REVIVAL_NATIVE_DROP_BUNDLE_V1",
            b"REVIVAL_SERVER_DROP_IMPORT_V1",
            b"REVIVAL_NATIVE_UNBOX_CHAT_V2",
            b"REVIVAL_SERVER_UNBOX_CHAT_RELAY_V1",
        ):
            if marker not in dll_blob:
                print(f"[build_pack] ERROR: stale csgo_gc.dll, missing {marker.decode()}")
                sys.exit(5)
        print("[build_pack] verified current matchmaking DLL markers")

    mainmenu_js = os.path.join(args.panorama, "scripts", "mainmenu.js")
    store_js = os.path.join(args.panorama, "scripts", "mainmenu_store.js")
    if os.path.isfile(mainmenu_js) and os.path.isfile(store_js):
        with open(mainmenu_js, "r", encoding="utf-8", errors="replace") as fh:
            mainmenu_text = fh.read()
        with open(store_js, "r", encoding="utf-8", errors="replace") as fh:
            store_text = fh.read()

        # The bottom panel must remain because it carries the Operation shop
        # banner. Its JS must be our operation-only variant so coupons/free
        # case/capsule tabs are never populated.
        if "mainmenu_store.xml" not in mainmenu_text:
            print("[build_pack] ERROR: Operation shop bottom panel is missing")
            sys.exit(6)
        if "REVIVAL_OPERATION_STORE_ONLY_V1" not in store_text:
            print("[build_pack] ERROR: main-menu store is not in Operation-only mode")
            sys.exit(6)
        print("[build_pack] verified Operation-only bottom shop panel")

    decodable_js = os.path.join(args.panorama, "scripts", "popups", "popup_capability_decodable.js")
    if os.path.isfile(decodable_js):
        with open(decodable_js, "r", encoding="utf-8", errors="replace") as fh:
            if "REVIVAL_KEYLESS_CASES_V1" not in fh.read():
                print("[build_pack] ERROR: keyless earned-case Panorama patch is missing")
                sys.exit(6)
        print("[build_pack] verified keyless earned-case UI")

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        packed_runtime = 0
        for name, path in runtime.items():
            # Public/client packs never ship the dedicated-server wrapper.
            # Laptop updates preserve their already-installed server launcher,
            # avoiding needless unsigned EXE churn and AV false positives.
            if name in SERVER_LAUNCHERS:
                continue
            # The Win32 launcher loads the GC from <root>\\csgo_gc\\csgo_gc.dll.
            # It does NOT load a root-level csgo_gc.dll.
            if name.lower() == "csgo_gc.dll":
                pack_name = "csgo_gc/csgo_gc.dll"
            elif name.lower() == "csgo.exe":
                # Keep Steam's csgo.exe untouched; use our launcher beside it.
                pack_name = "csgo_revival.exe"
            else:
                pack_name = name
            zf.write(path, pack_name)
            packed_runtime += 1
        print(f"[build_pack] added {packed_runtime} client runtime file(s); server launcher omitted")
        zf.write(args.config, "csgo_gc/config.txt")
        print("[build_pack] added csgo_gc/config.txt")
        zf.write(args.items_game, "csgo/scripts/items/items_game.txt")
        print("[build_pack] added csgo/scripts/items/items_game.txt")
        launcher_py = os.path.join(HERE, "launcher.py")
        if not os.path.isfile(launcher_py):
            print(f"[build_pack] ERROR: missing public launcher runtime: {launcher_py}")
            sys.exit(4)
        zf.write(launcher_py, "revival/launcher.py")
        print("[build_pack] added revival/launcher.py")
        pbin_tool = os.path.join(REPO, "tools", "pbin.py")
        if not os.path.isfile(pbin_tool):
            print(f"[build_pack] ERROR: missing Panorama PBIN tool: {pbin_tool}")
            sys.exit(4)
        zf.write(pbin_tool, "csgo/panorama/pbin.py")
        print("[build_pack] added csgo/panorama/pbin.py")
        if os.path.isdir(args.panorama):
            pn = 0
            for base, dirs, files in os.walk(args.panorama):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for name in files:
                    full = os.path.join(base, name)
                    rel = os.path.relpath(full, args.panorama)
                    zf.write(full, ("csgo/panorama/" + rel).replace(os.sep, "/"))
                    pn += 1
            print(f"[build_pack] added {pn} panorama file(s) under csgo/panorama/")
        else:
            print("[build_pack] (no panorama folder found - skipping UI)")

    size = os.path.getsize(args.out)
    print(f"[build_pack] wrote {args.out} ({size/1_048_576:.1f} MB)")
    print("[build_pack] upload this to a GitHub Release, then set install.py PACK_URL.")


if __name__ == "__main__":
    main()
