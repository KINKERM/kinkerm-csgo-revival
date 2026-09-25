#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re

MARKER = "REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1"
SERVER_ID_MARKER = "REVIVAL_SERVER_ID_EXPORT_V1"
QUEUE_RESERVE_MARKER = "REVIVAL_ENGINE_QUEUE_RESERVE_V1"
PLATFORM_INTERFACE_MARKER = "REVIVAL_PLATFORM_RESOLVE_INTERFACE_V1"
LOCAL_SOCACHE_AUTH_MARKER = "REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1"
NATIVE_DROP_REVEAL_MARKER = "REVIVAL_NATIVE_DROP_REVEAL_V1"
PLATFORM_PATTERN_MARKER = "REVIVAL_PLATFORM_FIND_PATTERN_V1"
RICH_PRESENCE_MARKER = "REVIVAL_MATCHMAKING_RICH_PRESENCE_V1"


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

    if RICH_PRESENCE_MARKER not in patched:
        rich_anchor = "static uint64_t GetUserSteamId(HSteamPipe pipe, HSteamUser user)"
        if rich_anchor not in patched:
            print("[patch_steam_hook] ERROR: client Steam user anchor missing for rich presence hook")
            return 17

        rich_code = r'''
#ifdef _WIN32
static void HookCreate(const char *name, void *target, void *hook, void **bridge);

using RevivalSetRichPresenceFn =
    bool (__thiscall *)(ISteamFriends *, const char *, const char *);
static RevivalSetRichPresenceFn s_revOriginalSetRichPresence = nullptr;

static bool RevivalQueuedMatchRichPresenceActive()
{
    std::ifstream in("csgo_gc/mm_state.txt", std::ios::binary);
    if (!in.is_open())
        return false;

    std::string line;
    while (std::getline(in, line))
    {
        if (line == "state=reserved" || line == "state=in_match")
            return true;
    }
    return false;
}

static bool __fastcall Hk_RevivalSetRichPresence(
    ISteamFriends *friends, void *, const char *key, const char *value)
{
    if (!s_revOriginalSetRichPresence)
        return false;

    if (!RevivalQueuedMatchRichPresenceActive() || !key)
        return s_revOriginalSetRichPresence(friends, key, value);

    if (!strcmp(key, "game:server") && value && !strcmp(value, "community"))
    {
        Platform::Print("REVIVAL_MATCHMAKING_RICH_PRESENCE_V1 game:server community -> kv\n");
        return s_revOriginalSetRichPresence(friends, key, "kv");
    }

    if (!strcmp(key, "game:act") && value && !strcmp(value, "community"))
        return s_revOriginalSetRichPresence(friends, key, nullptr);

    if (!strcmp(key, "status") && value && !strncmp(value, "Community ", 10))
    {
        const std::string officialStatus = value + 10;
        return s_revOriginalSetRichPresence(
            friends, key,
            officialStatus.empty() ? "Playing CS:GO" : officialStatus.c_str());
    }

    return s_revOriginalSetRichPresence(friends, key, value);
}

static void RevivalInstallMatchmakingRichPresenceHook(
    HSteamPipe pipe, HSteamUser user)
{
    static bool attempted = false;
    if (attempted)
        return;
    attempted = true;

    ISteamFriends *friends =
        s_actualSteamClient->GetISteamFriends(user, pipe, "SteamFriends015");
    if (!friends)
    {
        Platform::Print("REVIVAL_MATCHMAKING_RICH_PRESENCE_V1 SteamFriends015 unavailable\n");
        return;
    }

    // CS:GO 2018/Legacy uses SteamFriends015. SetRichPresence is vtable slot 43.
    void **vtable = *reinterpret_cast<void ***>(friends);
    void *target = vtable[43];
    if (!target)
    {
        Platform::Print("REVIVAL_MATCHMAKING_RICH_PRESENCE_V1 vtable slot 43 unavailable\n");
        return;
    }

    HookCreate(
        "ISteamFriends::SetRichPresence",
        target,
        reinterpret_cast<void *>(&Hk_RevivalSetRichPresence),
        reinterpret_cast<void **>(&s_revOriginalSetRichPresence));
    Platform::Print("REVIVAL_MATCHMAKING_RICH_PRESENCE_V1 hook installed\n");
}
#endif

'''
        patched = patched.replace(rich_anchor, rich_code + rich_anchor, 1)

        client_anchor = (
            "            s_clientGC = new GCWrapper<ClientGC, NetworkingClient>"
        )
        client_pos = patched.find(client_anchor)
        if client_pos < 0:
            print("[patch_steam_hook] ERROR: ClientGC construction anchor missing for rich presence hook")
            return 18
        client_line_end = patched.find("\n", client_pos)
        if client_line_end < 0:
            print("[patch_steam_hook] ERROR: ClientGC construction line malformed")
            return 18
        patched = (
            patched[:client_line_end + 1]
            + "#ifdef _WIN32\n"
            + "            RevivalInstallMatchmakingRichPresenceHook(pipe, user);\n"
            + "#endif\n"
            + patched[client_line_end + 1:]
        )

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
        Platform::Print("REVIVAL_SERVER_ID_EXPORT_V1 serverid=%llu\n", value);
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

    ph = platform_h.read_text(encoding="utf-8")
    if "FindModulePattern" not in ph:
        ph_anchor = "void *ResolveModuleInterface(const char *moduleName, const char *version);"
        if ph_anchor not in ph:
            print("[patch_steam_hook] ERROR: ResolveModuleInterface declaration missing")
            return 12
        ph = ph.replace(
            ph_anchor,
            ph_anchor + "\nvoid *FindModulePattern(const char *moduleName, "
            + "const unsigned char *pattern, const char *mask);",
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
        Print("REVIVAL_PLATFORM_RESOLVE_INTERFACE_V1 %s/%s\n", moduleName, version);
    return result;
}

'''
        pc = pc.replace(close_anchor, helper + close_anchor, 1)
        platform_cpp.write_text(pc, encoding="utf-8", newline="\n")

    pc = platform_cpp.read_text(encoding="utf-8")
    if "#include <cstring>" not in pc:
        first_include = pc.find("#include")
        if first_include >= 0:
            line_end = pc.find("\n", first_include)
            pc = pc[:line_end + 1] + "#include <cstring>\n" + pc[line_end + 1:]
            platform_cpp.write_text(pc, encoding="utf-8", newline="\n")
    if PLATFORM_PATTERN_MARKER not in pc:
        close_anchor = "} // namespace Platform"
        if close_anchor not in pc:
            print("[patch_steam_hook] ERROR: platform_windows.cpp namespace anchor missing for pattern scanner")
            return 13
        helper = r'''
void *FindModulePattern(const char *moduleName, const unsigned char *pattern, const char *mask)
{
    HMODULE module = GetModuleHandleA(moduleName);
    if (!module || !pattern || !mask)
        return nullptr;

    const auto *dos = reinterpret_cast<const IMAGE_DOS_HEADER *>(module);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return nullptr;
    const auto *nt = reinterpret_cast<const IMAGE_NT_HEADERS *>(
        reinterpret_cast<const unsigned char *>(module) + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
        return nullptr;

    const size_t imageSize = static_cast<size_t>(nt->OptionalHeader.SizeOfImage);
    const size_t patternSize = std::strlen(mask);
    if (!patternSize || patternSize > imageSize)
        return nullptr;

    const auto *base = reinterpret_cast<const unsigned char *>(module);
    for (size_t i = 0; i + patternSize <= imageSize; ++i)
    {
        bool match = true;
        for (size_t j = 0; j < patternSize; ++j)
        {
            if (mask[j] == 'x' && base[i + j] != pattern[j])
            {
                match = false;
                break;
            }
        }
        if (match)
        {
            Print("REVIVAL_PLATFORM_FIND_PATTERN_V1 %s +0x%zx\n", moduleName, i);
            return const_cast<unsigned char *>(base + i);
        }
    }
    return nullptr;
}

'''
        pc = pc.replace(close_anchor, helper + close_anchor, 1)
        platform_cpp.write_text(pc, encoding="utf-8", newline="\n")

    if not already_queue_reserve:
        include_anchor = '#include "platform.h"'
        if include_anchor in patched and "#include <fstream>" not in patched:
            patched = patched.replace(include_anchor, include_anchor + "\n#include <fstream>", 1)

        # Add the main-thread engine reservation dispatcher immediately before
        # SteamGameServer_RunCallbacks, then teach the server HostEvent switch
        # to invoke it. This exact callback drain is present in the old tree.
        run_anchor = "static void Hk_SteamGameServer_RunCallbacks()"
        if run_anchor not in patched:
            print("[patch_steam_hook] ERROR: server callback anchor missing")
            return 9

        bridge = r'''
#ifdef _WIN32
static bool RevivalInstallNativeDropRevealHooks();

static bool RevivalDispatchReserveServerForQueuedGame(
    uint64_t matchId, const std::vector<uint8_t> &payload)
{
    static void *s_engineServer = nullptr;
    if (!s_engineServer)
    {
        s_engineServer = Platform::ResolveModuleInterface("engine.dll", "VEngineServer023");
        if (!s_engineServer)
        {
            Platform::Print("REVIVAL_ENGINE_QUEUE_RESERVE_V1 VEngineServer023 not found\n");
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
        "REVIVAL_ENGINE_QUEUE_RESERVE_V1 result=%d match=%llu payload=%s\n",
        ok ? 1 : 0, static_cast<unsigned long long>(matchId),
        payloadString.c_str());

    if (ok)
    {
        // Separate readiness file: the Python agent must not advertise this
        // allocation until the Source engine itself accepted the queued
        // reservation. This prevents a valid-looking 9106 cookie fallback from
        // racing ahead of the actual 0x21/0x25 ready-up state.
        std::ofstream ready("csgo_gc/engine_reservation_ready.txt",
            std::ios::binary | std::ios::trunc);
        if (ready.is_open())
        {
            ready << "match_id=" << matchId << "\n";
            ready << "payload=" << payloadString << "\n";
            ready.flush();
        }
    }
    return ok;
}
#endif

'''
        patched = patched.replace(run_anchor, bridge + run_anchor, 1)

        # Target only the server-side HostEvent switch. Older compatible
        # csgo_gc revisions pass event.id directly; newer ones cast it to
        # uint32_t. Accept both instead of pinning the patcher to one spelling.
        server_case_pattern = re.compile(
            r'(?P<block>'
            r'            case HostEvent::NetMessage:\r?\n'
            r'                s_serverGC->m_networking\.SendMessage\('
            r'(?:\(uint32_t\))?event\.id, event\.buffer\.data\(\), '
            r'static_cast<uint32_t>\(event\.buffer\.size\(\)\)\);\r?\n'
            r'                break;\r?\n'
            r')'
        )
        m = server_case_pattern.search(patched)
        if not m:
            print("[patch_steam_hook] ERROR: server HostEvent switch anchor missing")
            return 10
        server_case_new = m.group("block") + r'''
            case HostEvent::ReserveServerForQueuedGame:
#ifdef _WIN32
                RevivalDispatchReserveServerForQueuedGame(event.id, event.buffer);
#else
                Platform::Print("REVIVAL_ENGINE_QUEUE_RESERVE_V1 unsupported platform\n");
#endif
                break;
'''
        patched = patched[:m.start()] + server_case_new + patched[m.end():]

    if NATIVE_DROP_REVEAL_MARKER not in patched:
        init_anchor = "static bool InitializeSteamAPI(void *steamApi, bool dedicated)"
        if init_anchor not in patched:
            print("[patch_steam_hook] ERROR: InitializeSteamAPI anchor missing for native drop hook")
            return 14

        hook_code = r'''
#ifdef _WIN32
using RevivalRewardMatchEndDropsFn = void (__thiscall *)(void *, bool);
using RevivalRecordPlayerItemDropFn =
    void (__thiscall *)(void *, const CEconItemPreviewDataBlock *);

static RevivalRewardMatchEndDropsFn s_revOriginalRewardMatchEndDrops = nullptr;
static RevivalRecordPlayerItemDropFn s_revRecordPlayerItemDrop = nullptr;
static void *s_revGameRules = nullptr;
static bool s_revNativeDropRevealInstalled = false;

static void __fastcall Hk_RevivalRewardMatchEndDrops(
    void *gameRules, void *, bool aborted)
{
    s_revGameRules = gameRules;
    Platform::Print(
        "REVIVAL_NATIVE_DROP_REVEAL_V1 captured CCSGameRules=%p aborted=%d\n",
        gameRules, aborted ? 1 : 0);

    if (s_revOriginalRewardMatchEndDrops)
        s_revOriginalRewardMatchEndDrops(gameRules, aborted);
}

static bool RevivalInstallNativeDropRevealHooks()
{
    if (s_revNativeDropRevealInstalled)
        return true;

    static uint32_t retryCount = 0;

    static const unsigned char RewardPattern[] = {
        0x55,0x8B,0xEC,0x83,0xE4,0xF8,0xA1,0,0,0,0,0x83,0xEC,0x1C,0xB9
    };
    static const unsigned char RecordPattern[] = {
        0x55,0x8B,0xEC,0x53,0x8B,0xD9,0x33,0xD2,0x56,0x57,0x8B,0x7D,0x08
    };

    void *reward = Platform::FindModulePattern(
        "server.dll", RewardPattern, "xxxxxxx????xxxx");
    void *record = Platform::FindModulePattern(
        "server.dll", RecordPattern, "xxxxxxxxxxxxx");

    if (!reward || !record)
    {
        ++retryCount;
        if (retryCount == 1 || (retryCount % 128u) == 0)
        {
            Platform::Print(
                "REVIVAL_NATIVE_DROP_REVEAL_V1 waiting for server.dll signatures reward=%p record=%p retry=%u\n",
                reward, record, retryCount);
        }
        return false;
    }

    s_revRecordPlayerItemDrop =
        reinterpret_cast<RevivalRecordPlayerItemDropFn>(record);
    HookCreate(
        "CCSGameRules::RewardMatchEndDrops",
        reward,
        reinterpret_cast<void *>(&Hk_RevivalRewardMatchEndDrops),
        reinterpret_cast<void **>(&s_revOriginalRewardMatchEndDrops));

    s_revNativeDropRevealInstalled = true;
    Platform::Print(
        "REVIVAL_NATIVE_DROP_REVEAL_V1 hooks installed reward=%p record=%p\n",
        reward, record);
    return true;
}

static bool RevivalRecordPlayerItemDrop(
    const std::vector<uint8_t> &payload)
{
    if (!s_revGameRules || !s_revRecordPlayerItemDrop)
    {
        Platform::Print(
            "REVIVAL_NATIVE_DROP_REVEAL_V1 record skipped gamerules=%p fn=%p\n",
            s_revGameRules, reinterpret_cast<void *>(s_revRecordPlayerItemDrop));
        return false;
    }

    CEconItemPreviewDataBlock item;
    if (!item.ParseFromArray(payload.data(), static_cast<int>(payload.size())))
    {
        Platform::Print(
            "REVIVAL_NATIVE_DROP_REVEAL_V1 preview parse failed (%zu bytes)\n",
            payload.size());
        return false;
    }

    s_revRecordPlayerItemDrop(s_revGameRules, &item);
    Platform::Print(
        "REVIVAL_NATIVE_DROP_REVEAL_V1 recorded account=%u item=%llu def=%u\n",
        item.accountid(), static_cast<unsigned long long>(item.itemid()),
        item.defindex());
    return true;
}
#endif

'''
        patched = patched.replace(init_anchor, hook_code + init_anchor, 1)

        callback_anchor = (
            "static void Hk_SteamGameServer_RunCallbacks()\n"
            "{\n"
            "    Og_SteamGameServer_RunCallbacks();"
        )
        if callback_anchor not in patched:
            print("[patch_steam_hook] ERROR: server callback body missing for native drop retry")
            return 19
        patched = patched.replace(
            callback_anchor,
            callback_anchor
            + "\n\n#ifdef _WIN32\n"
            + "    RevivalInstallNativeDropRevealHooks();\n"
            + "#endif",
            1,
        )

        install_anchor = "    INLINE_HOOK(SteamGameServer_RunCallbacks);"
        if install_anchor not in patched:
            print("[patch_steam_hook] ERROR: SteamGameServer_RunCallbacks install anchor missing")
            return 15
        patched = patched.replace(
            install_anchor,
            install_anchor
            + "\n#ifdef _WIN32\n"
            + "    if (dedicated)\n"
            + "        RevivalInstallNativeDropRevealHooks();\n"
            + "#endif",
            1,
        )

        reserve_case_anchor = '''            case HostEvent::ReserveServerForQueuedGame:
#ifdef _WIN32
                RevivalDispatchReserveServerForQueuedGame(event.id, event.buffer);
#else
                Platform::Print("REVIVAL_ENGINE_QUEUE_RESERVE_V1 unsupported platform\\n");
#endif
                break;
'''
        if reserve_case_anchor not in patched:
            print("[patch_steam_hook] ERROR: reserve HostEvent case missing for native drop bridge")
            return 16
        patched = patched.replace(
            reserve_case_anchor,
            reserve_case_anchor + r'''
            case HostEvent::RecordPlayerItemDrop:
#ifdef _WIN32
                RevivalRecordPlayerItemDrop(event.buffer);
#else
                Platform::Print("REVIVAL_NATIVE_DROP_REVEAL_V1 unsupported platform\n");
#endif
                break;
''',
            1,
        )

    if LOCAL_SOCACHE_AUTH_MARKER not in patched:
        auth_anchor = (
            "            s_serverGC->m_networking.ClientConnected("
            "steamID.ConvertToUint64(), pAuthTicket, cbAuthTicket);"
        )
        if auth_anchor not in patched:
            print("[patch_steam_hook] ERROR: BeginAuthSession ClientConnected anchor missing")
            return 11
        auth_new = auth_anchor + (
            "\n            s_serverGC->m_gc.PostToGC("
            "GCEvent::ClientLocalInventoryRequest, steamID.ConvertToUint64(), nullptr, 0);"
            "\n            Platform::Print(\"REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1 player=%llu\\n\", "
            "steamID.ConvertToUint64());"
        )
        patched = patched.replace(auth_anchor, auth_new, 1)

    path.write_text(patched, encoding="utf-8", newline="\n")

    verify = path.read_text(encoding="utf-8")
    ph_verify = platform_h.read_text(encoding="utf-8")
    pc_verify = platform_cpp.read_text(encoding="utf-8")
    expected_offline_log = f'Platform::Print("{MARKER} active\\n");'
    if (MARKER not in verify or SERVER_ID_MARKER not in verify
            or QUEUE_RESERVE_MARKER not in verify
            or LOCAL_SOCACHE_AUTH_MARKER not in verify
            or NATIVE_DROP_REVEAL_MARKER not in verify
            or RICH_PRESENCE_MARKER not in verify
            or expected_offline_log not in verify
            or "ResolveModuleInterface" not in ph_verify
            or "FindModulePattern" not in ph_verify
            or PLATFORM_INTERFACE_MARKER not in pc_verify
            or PLATFORM_PATTERN_MARKER not in pc_verify):
        print("[patch_steam_hook] ERROR: marker verification failed after write")
        return 4

    print(f"[patch_steam_hook] patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
