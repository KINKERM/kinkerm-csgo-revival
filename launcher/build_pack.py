#!/usr/bin/env python3
"""HOST helper: assemble the revival "pack" zip that install.py hands to friends.

The pack extracts directly over a CS:GO Legacy install so every friend ends up
with an IDENTICAL setup (required for Steam P2P lobbies):

  <root>/                         <- your built, PATCHED csgo_gc release contents
  <root>/csgo_gc/config.txt       <- this repo's tuned gc-config/config.txt
  <root>/csgo/scripts/items/items_game.txt  <- this repo's custom items_game.txt

Usage:
  python3 build_pack.py --csgo-gc-dir /path/to/your/built/csgo_gc_release \
                        [--out csgo-revival-pack.zip]

Where --csgo-gc-dir is the folder containing your compiled csgo_gc build (the
patched csgo_gc.dll and the replacement game executables) laid out exactly as it
should sit in the CS:GO install root.

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

# csgo_gc runtime markers - at least one MUST sit at the root of --csgo-gc-dir
MARKERS = ("csgo_gc.dll", "csgo.exe", "srcds.exe", "csgo_linux64",
           "srcds_linux", "csgo_osx64", "libcsgo_gc.so", "libcsgo_gc.dylib")

# dev/build junk we never want in a distributable pack
SKIP_DIRS = {".git", ".vs", ".idea", "build", "cmake-build-debug",
             "cmake-build-release", "CMakeFiles", "__pycache__", ".github"}
SKIP_EXT = {".obj", ".pdb", ".ilk", ".exp", ".o", ".a", ".log", ".tlog"}
SKIP_FILES = {"CMakeCache.txt", ".gitignore", ".gitattributes"}


def looks_like_junk(rel: str, name: str) -> bool:
    parts = set(rel.replace("\\", "/").split("/"))
    if parts & SKIP_DIRS:
        return True
    if name in SKIP_FILES:
        return True
    if os.path.splitext(name)[1].lower() in SKIP_EXT:
        return True
    return False


def add_tree(zf: zipfile.ZipFile, src_dir: str, arc_prefix: str = "") -> int:
    n = 0
    for base, dirs, files in os.walk(src_dir):
        # prune junk dirs in-place so we don't descend into them
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, src_dir)
            if looks_like_junk(rel, name):
                continue
            arc = os.path.join(arc_prefix, rel) if arc_prefix else rel
            zf.write(full, arc.replace(os.sep, "/"))
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csgo-gc-dir", required=True,
                    help="folder with your compiled PATCHED csgo_gc build "
                         "(overlays the CS:GO install root)")
    ap.add_argument("--items-game", default=os.path.join(REPO, "items_game.txt"),
                    help="custom items_game.txt (default: repo copy)")
    ap.add_argument("--config", default=os.path.join(REPO, "gc-config", "config.txt"),
                    help="csgo_gc config.txt (default: repo gc-config/config.txt)")
    ap.add_argument("--out", default=os.path.join(HERE, "csgo-revival-pack.zip"))
    ap.add_argument("--force", action="store_true",
                    help="build even if csgo_gc runtime files aren't found at the root")
    args = ap.parse_args()

    for path, what in ((args.csgo_gc_dir, "--csgo-gc-dir"),
                       (args.items_game, "items_game.txt"),
                       (args.config, "config.txt")):
        if not os.path.exists(path):
            print(f"[build_pack] missing {what}: {path}")
            sys.exit(1)

    # --- sanity-check the csgo_gc folder before packing ---
    root_entries = sorted(os.listdir(args.csgo_gc_dir))
    found = [m for m in MARKERS if os.path.isfile(os.path.join(args.csgo_gc_dir, m))]
    print(f"[build_pack] --csgo-gc-dir top level ({len(root_entries)} entries): "
          f"{', '.join(root_entries[:20])}{' ...' if len(root_entries) > 20 else ''}")
    if found:
        print(f"[build_pack] OK - found runtime file(s) at root: {', '.join(found)}")
    else:
        print("[build_pack] WARNING: none of the expected csgo_gc runtime files "
              f"({', '.join(MARKERS[:4])}, ...) are at the ROOT of that folder.")
        print("[build_pack] The pack will NOT work unless csgo_gc.dll + csgo.exe sit at")
        print("[build_pack] the top level. Point --csgo-gc-dir at the folder you EXTRACT")
        print("[build_pack] the csgo_gc release into (the one that overlays the CS:GO root).")
        if not args.force:
            print("[build_pack] aborting. Re-run with --force to build anyway.")
            sys.exit(3)

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        n = add_tree(zf, args.csgo_gc_dir)
        print(f"[build_pack] added {n} csgo_gc file(s) at root")
        zf.write(args.config, "csgo_gc/config.txt")
        print("[build_pack] added csgo_gc/config.txt")
        zf.write(args.items_game, "csgo/scripts/items/items_game.txt")
        print("[build_pack] added csgo/scripts/items/items_game.txt")

    size = os.path.getsize(args.out)
    print(f"[build_pack] wrote {args.out} ({size/1_048_576:.1f} MB)")
    print("[build_pack] upload this to a GitHub Release, then set install.py PACK_URL.")


if __name__ == "__main__":
    main()
