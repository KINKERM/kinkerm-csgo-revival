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


def add_tree(zf: zipfile.ZipFile, src_dir: str, arc_prefix: str = "") -> int:
    n = 0
    for base, _dirs, files in os.walk(src_dir):
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, src_dir)
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
    args = ap.parse_args()

    for path, what in ((args.csgo_gc_dir, "--csgo-gc-dir"),
                       (args.items_game, "items_game.txt"),
                       (args.config, "config.txt")):
        if not os.path.exists(path):
            print(f"[build_pack] missing {what}: {path}")
            sys.exit(1)

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
