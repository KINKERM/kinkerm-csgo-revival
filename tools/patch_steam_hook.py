#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re

MARKER = "REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1"
SERVER_ID_MARKER = "REVIVAL_SERVER_ID_EXPORT_V1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    args = ap.parse_args()

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"[patch_steam_hook] ERROR: missing {path}")
        return 2

    text = path.read_text(encoding="utf-8")

    already_offline = MARKER in text
    already_server_id = SERVER_ID_MARKER in text
    if already_offline and already_server_id:
        print("[patch_steam_hook] already patched")
        return 0

    # Match only an immediate-return guard whose condition contains BLoggedOn().
    # Supports both:
    #   if (!x->BLoggedOn()) { return; }
    # and:
    #   if (!x->BLoggedOn())
    #   {
    #       return;
    #   }
    pattern = re.compile(
        r'(?P<indent>^[ \t]*)'
        r'if[ \t]*\([ \t]*![^\r\n]*?BLoggedOn[ \t]*\([ \t]*\)[ \t]*\)[ \t]*'
        r'(?:\r?\n)?'
        r'(?:'
            r'[ \t]*\{[ \t]*(?:\r?\n)?[ \t]*return[ \t]*;[ \t]*(?:\r?\n)?[ \t]*\}'
            r'|'
            r'return[ \t]*;'
        r')',
        re.MULTILINE,
    )

    def repl(m: re.Match[str]) -> str:
        i = m.group("indent")
        return "\n".join([
            f"{i}// Revival local GC delivery must not depend on Steam master login.",
            f"{i}static bool s_revAllowOfflineGcPrinted = false;",
            f"{i}if (!s_revAllowOfflineGcPrinted)",
            i + "{",
            i + f'    Platform::Print("{MARKER} active\\n");',
            i + "    s_revAllowOfflineGcPrinted = true;",
            i + "}",
        ])

    patched, count = pattern.subn(repl, text, count=1) if not already_offline else (text, 1)

    if count != 1:
        print("[patch_steam_hook] ERROR: could not patch the BLoggedOn early-return block")
        found = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if "BLoggedOn" in line:
                found = True
                print(f"[patch_steam_hook] BLoggedOn at line {lineno}: {line.strip()}")
        if not found:
            print("[patch_steam_hook] No BLoggedOn() occurrence exists in this source file.")
        return 3

    if not already_server_id:
        anchor = "static ISteamGameServer *s_steamGameServer;"
        if anchor not in patched:
            print("[patch_steam_hook] ERROR: could not locate s_steamGameServer declaration")
            return 5
        patched = patched.replace(anchor, "static ISteamGameServer *s_steamGameServer;\n\nuint64_t RevivalGameServerSteamId()\n{\n    if (!s_steamGameServer)\n        return 0;\n\n    const CSteamID steamId = s_steamGameServer->GetSteamID();\n    if (!steamId.IsValid())\n        return 0;\n\n    const uint64_t value = steamId.ConvertToUint64();\n    static uint64_t s_lastPrinted = 0;\n    if (value && value != s_lastPrinted)\n    {\n        Platform::Print(\"${SERVER_ID_MARKER} serverid=%llu\\\\n\", value);\n        s_lastPrinted = value;\n    }\n    return value;\n}", 1)

    path.write_text(patched, encoding="utf-8", newline="\n")

    verify = path.read_text(encoding="utf-8")
    if MARKER not in verify or SERVER_ID_MARKER not in verify:
        print("[patch_steam_hook] ERROR: marker verification failed after write")
        return 4

    print(f"[patch_steam_hook] patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
