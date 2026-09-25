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

    # Match the dedicated-server callback guard without assuming the exact
    # surrounding source revision/formatting. We only remove an if (!...BLoggedOn())
    # whose entire body is an immediate return.
    pattern = re.compile(
        r'(?P<indent>^[ \\t]*)'
        r'if[ \\t]*\\([ \\t]*![^\\r\\n]*?BLoggedOn[ \\t]*\\([ \\t]*\\)[ \\t]*\\)[ \\t]*'
        r'(?:\\r?\\n)?'
        r'(?:(?:[ \\t]*\\{[ \\t]*(?:\\r?\\n)?[ \\t]*return[ \\t]*;[ \\t]*(?:\\r?\\n)?[ \\t]*\\})'
        r'|(?:return[ \\t]*;))',
        re.MULTILINE,
    )

    def repl(m: re.Match[str]) -> str:
        i = m.group("indent")
        return (
            f"{i}// Revival local GC delivery must not depend on Steam master login.\\n"
            f"{i}static bool s_revAllowOfflineGcPrinted = false;\\n"
            f"{i}if (!s_revAllowOfflineGcPrinted)\\n"
            f"{i}{{{{\\n"
            f'{i}    Platform::Print("{MARKER} active\\\\n");\\n'
            f"{i}    s_revAllowOfflineGcPrinted = true;\\n"
            f"{i}}}}}"
        )

    patched, count = pattern.subn(repl, text, count=1)
    if count != 1:
        print("[patch_steam_hook] ERROR: could not patch the BLoggedOn early-return block")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "BLoggedOn" in line:
                print(f"[patch_steam_hook] BLoggedOn at line {lineno}: {line.strip()}")
        return 3

    path.write_text(patched, encoding="utf-8", newline="\n")
    print(f"[patch_steam_hook] patched {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
