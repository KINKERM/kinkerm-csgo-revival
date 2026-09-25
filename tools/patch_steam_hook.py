#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re

MARKER = "REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1"
SERVER_ID_MARKER = "REVIVAL_SERVER_ID_EXPORT_V1"
QUEUE_RESERVE_MARKER = "REVIVAL_ENGINE_QUEUE_RESERVE_V1"
PLATFORM_INTERFACE_MARKER = "REVIVAL_PLATFORM_RESOLVE_INTERFACE_V1"


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
    already_queue_reserve = QUEUE_RESERVE_MARKER in text

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
        # The user's existing Win32 tree uses the older Steam proxy layout.
        # Do not import a newer steam_hook.cpp. That newer file requires C++20
        # abbreviated templates and generated proxy headers absent from the
        # working tree. The old hook already uses SteamGameServer(), so export
        # the server SteamID through that stable API instead.
        include_anchor = '#include "networking_server.h"'
        if include_anchor not in patched:
            print("[patch_steam_hook] ERROR: could not locate networking_server.h include")
            return 5

        helper = r'''
uint64_t RevivalGameServerSteamId()
{
    ISteamGameServer *server = SteamGameServer();
    if (!server)
        return 0;

    const CSteamID steamId = server->GetSteamID();
    if (!steamId.IsValid())
        return 0;

    const uint64_t value = steamId.ConvertToUint64();
    static uint64_t s_lastPrinted = 0;
    if (value && value != s_lastPrinted)
    {
        Platform::Print("REVIVAL_SERVER_ID_EXPORT_V1 serverid=%llu\\n", value);
        s_lastPrinted = value;
    }
    return value;
}
'''
        patched = patched.replace(include_anchor, include_anchor + "\n" + helper, 1)

    # The queued Accept flow needs IVEngineServer::ReserveServerForQueuedGame,
    # but this old csgo_gc tree has no generic module-interface resolver. Patch
    # the sibling platform files in-place so they stay from the user's own
    # compatible revision.
    source_dir = path.parent
    platform_h = source_dir / "platform.h"
    platform_cpp = source_dir / "platform_windows.cpp"
    if not platform_h.is_file() or not platform_cpp.is_file():
        print("[patch_steam_hook] ERROR: platform.h/platform_windows.cpp missing")
        return 6

    ph = platform_h.read_text(encoding="utf-8")
    pc = platform_cpp.read_text(encoding="utf-8")

    if "ResolveModuleInterface" not in ph:
        ph_anchor = "bool PatchServerBrowserAppId(uint32_t appId);"
        if ph_anchor not in ph:
            print("[patch_steam_hook] ERROR: platform.h anchor missing")
            return 7
        ph = ph.replace(
            ph_anchor,
            ph_anchor + "\n\n// Revival: resolve a Source CreateInterface export without importing windows.h in steam_hook.cpp.\n"
            + "void *ResolveModuleInterface(const char *moduleName, const char *version);",
            1,
        )
        platform_h.write_text(ph, encoding="utf-8", newline="\n")

    if PLATFORM_INTERFACE_MARKER not in pc:
        close_anchor = "} // namespace Platform"
        if close_anchor not in pc:
            print("[patch_steam_hook] ERROR: platform_windows.cpp namespace anchor missing")
            return 8
        helper = r'''
void *ResolveModuleInterface(const char *moduleName, const char *version)
{
    HMODULE module = GetModuleHandleA(moduleName);
    if (!module)
        return nullptr;

    using CreateInterfaceFn = void *(*)(const char *, int *);
    auto createInterface = reinterpret_cast<CreateInterfaceFn>(
        GetProcAddress(module, "CreateInterface"));
    if (!createInterface)
        return nullptr;

    void *result = createInterface(version, nullptr);
    if (result)
        Print("REVIVAL_PLATFORM_RESOLVE_INTERFACE_V1 %s/%s\\n", moduleName, version);
    return result;
}

'''
        pc = pc.replace(close_anchor, helper + close_anchor, 1)
        platform_cpp.write_text(pc, encoding="utf-8", newline="\n")

    if not already_queue_reserve:
        # Add the main-thread engine reservation dispatcher immediately before
        # SteamGameServer_RunCallbacks, then teach the server HostEvent switch
        # to invoke it. This exact callback drain is present in the old tree.
        run_anchor = "static void Hk_SteamGameServer_RunCallbacks()"
        if run_anchor not in patched:
            print("[patch_steam_hook] ERROR: server callback anchor missing")
            return 9

        bridge = r'''
#ifdef _WIN32
static bool RevivalDispatchReserveServerForQueuedGame(const std::vector<uint8_t> &payload)
{
    static void *s_engineServer = nullptr;
    if (!s_engineServer)
    {
        s_engineServer = Platform::ResolveModuleInterface("engine.dll", "VEngineServer023");
        if (!s_engineServer)
        {
            Platform::Print("REVIVAL_ENGINE_QUEUE_RESERVE_V1 VEngineServer023 not found\\n");
            return false;
        }
    }

    std::string payloadString(
        reinterpret_cast<const char *>(payload.data()), payload.size());

    // Win32 CS:GO Legacy VEngineServer023: slot 149 is
    // CVEngineServer::ReserveServerForQueuedGame(const char *).
    using ReserveFn = bool(__thiscall *)(void *, const char *);
    void **vtable = *reinterpret_cast<void ***>(s_engineServer);
    auto reserve = reinterpret_cast<ReserveFn>(vtable[149]);
    const bool ok = reserve(s_engineServer, payloadString.c_str());
    Platform::Print(
        "REVIVAL_ENGINE_QUEUE_RESERVE_V1 result=%d payload=%s\\n",
        ok ? 1 : 0, payloadString.c_str());
    return ok;
}
#endif

'''
        patched = patched.replace(run_anchor, bridge + run_anchor, 1)

        # Target only the server-side HostEvent switch. The NetMessage line is
        # unique in that block because server networking takes recipient id.
        server_case_anchor = r'''            case HostEvent::NetMessage:
                s_serverGC->m_networking.SendMessage((uint32_t)event.id, event.buffer.data(), static_cast<uint32_t>(event.buffer.size()));
                break;
'''
        if server_case_anchor not in patched:
            print("[patch_steam_hook] ERROR: server HostEvent switch anchor missing")
            return 10
        server_case_new = server_case_anchor + r'''
            case HostEvent::ReserveServerForQueuedGame:
#ifdef _WIN32
                RevivalDispatchReserveServerForQueuedGame(event.buffer);
#else
                Platform::Print("REVIVAL_ENGINE_QUEUE_RESERVE_V1 unsupported platform\n");
#endif
                break;
'''
        patched = patched.replace(server_case_anchor, server_case_new, 1)

    path.write_text(patched, encoding="utf-8", newline="\n")

    verify = path.read_text(encoding="utf-8")
    ph_verify = platform_h.read_text(encoding="utf-8")
    pc_verify = platform_cpp.read_text(encoding="utf-8")
    if (MARKER not in verify or SERVER_ID_MARKER not in verify
            or QUEUE_RESERVE_MARKER not in verify
            or "ResolveModuleInterface" not in ph_verify
            or PLATFORM_INTERFACE_MARKER not in pc_verify):
        print("[patch_steam_hook] ERROR: marker verification failed after write")
        return 4

    print(f"[patch_steam_hook] patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
