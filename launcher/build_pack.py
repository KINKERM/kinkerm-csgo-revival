#!/usr/bin/env python3
"""HOST helper: assemble the revival "pack" zip that install.py hands to friends.

The pack extracts directly over a CS:GO Legacy install so every friend ends up
with an IDENTICAL setup (required for Steam P2P lobbies):

  <root>/csgo_gc/csgo_gc.dll               <- patched GC loaded by the x86 launcher
  <root>/csgo_revival.exe, srcds.exe        <- patched revival launchers
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
LAUNCHERS = ("csgo.exe", "srcds.exe", "csgo_linux64", "srcds_linux",
             "csgo_osx64", "csgo_win64.exe", "srcds_win64.exe",
             "csgo_linux", "srcds_linux64")
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
    launchers = [n for n in runtime if n in LAUNCHERS]

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
    elif not launchers:
        print("[build_pack] WARNING: found the GC library but no launcher exe "
              "(csgo.exe/srcds.exe). Friends need the launcher to boot the GC.")
        print("[build_pack] Make sure the launcher target built too.")
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
            b"REVIVAL_SERVER_ACCEPT_ROSTER_V1",
            b"REVIVAL_ENGINE_QUEUE_RESERVE_V1",
            b"REVIVAL_SERVER_LOCAL_SOCACHE_V1",
            b"REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1",
            b"REVIVAL_SERVER_PLAYER_AUTH_V1",
            b"REVIVAL_SERVER_REWARD_BRIDGE_V1",
            b"REVIVAL_REWARD_SPOOL_QUEUE_V1",
            b"REVIVAL_CLIENT_REWARD_BRIDGE_V1",
            b"REVIVAL_GUARANTEED_MATCH_DROPS_V1",
            b"REVIVAL_SYNTHETIC_MATCH_END_V1",
            b"REVIVAL_NATIVE_DROP_REVEAL_V1",
            b"REVIVAL_NATIVE_ENDMATCH_UI_V1",
            b"REVIVAL_NATIVE_DROP_CRASH_GUARD_V1",
            b"REVIVAL_NATIVE_DROP_TIMING_V3",
            b"REVIVAL_NATIVE_DROP_BUNDLE_V1",
            b"REVIVAL_SERVER_DROP_IMPORT_V1",
        ):
            if marker not in dll_blob:
                print(f"[build_pack] ERROR: stale csgo_gc.dll, missing {marker.decode()}")
                sys.exit(5)
        print("[build_pack] verified current matchmaking DLL markers")

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in runtime.items():
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
        print(f"[build_pack] added {len(runtime)} runtime file(s) in launcher layout")
        zf.write(args.config, "csgo_gc/config.txt")
        print("[build_pack] added csgo_gc/config.txt")
        zf.write(args.items_game, "csgo/scripts/items/items_game.txt")
        print("[build_pack] added csgo/scripts/items/items_game.txt")
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
