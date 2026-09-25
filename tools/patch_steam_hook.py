#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
import sys

MARKER = "REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    args = ap.parse_args()

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"[patch_steam_hook] ERROR: missing {path}")
        return 2

    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("[patch_steam_hook] already patched")
        return 0

    pattern = re.compile(
        r'(?P<indent>^[ \t]*)assert\(s_steamGameServer\);[ \t]*\r?\n'
        r'(?P=indent)if[ \t]*\(!s_steamGameServer->BLoggedOn\(\)\)[ \t]*\r?\n'
        r'(?P=indent)\{[ \t]*\r?\n'
        r'(?P=indent)[ \t]+return;[ \t]*\r?\n'
        r'(?P=indent)\}',
        re.MULTILINE,
    )

    def repl(m: re.Match[str]) -> str:
        i = m.group("indent")
        return (
            f"{i}assert(s_steamGameServer);\n"
            f"{i}// Revival's local fake GC must keep delivering queued messages even\n"
            f"{i}// when Steam master-server login is temporarily unavailable.\n"
            f"{i}static bool s_revAllowOfflineGcPrinted = false;\n"
            f"{i}if (!s_revAllowOfflineGcPrinted)\n"
            f"{i}{{\n"
            f'{i}    Platform::Print("{MARKER} active\\n");\n'
            f"{i}    s_revAllowOfflineGcPrinted = true;\n"
            f"{i}}}"
        )

    patched, count = pattern.subn(repl, text, count=1)
    if count != 1:
        print("[patch_steam_hook] ERROR: could not find the BLoggedOn early-return block")
        return 3

    path.write_text(patched, encoding="utf-8", newline="\n")
    print(f"[patch_steam_hook] patched {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
