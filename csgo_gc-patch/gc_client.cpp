#include "stdafx.h"
#include "gc_client.h"
#include "graffiti.h"
#include "keyvalue.h"

#include <fstream>
#include <algorithm>

#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <sstream>
#include <string>
#include <unordered_map>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>
#include <funchook.h>
#pragma comment(lib, "ws2_32.lib")
#endif

namespace
{
constexpr const char *MatchmakingRequestPath = "csgo_gc/mm_request.txt";
constexpr const char *MatchmakingStatePath = "csgo_gc/mm_state.txt";
constexpr const char *MatchmakingRewardPath = "csgo_gc/mm_reward.bin";

// Panorama's con_logfile path can be rooted at either the game directory or
// the csgo subdirectory depending on how this Legacy build was launched.
// Poll all harmless local candidates and consume whichever exists.
constexpr const char *OperationMissionBridgePaths[] = {
    "revival_mission_select.log",
    "csgo/revival_mission_select.log",
    "csgo_gc/revival_mission_select.log",
};

constexpr int RevivalUserMsgServerRankRevealAll = 50;
constexpr int RevivalUserMsgServerRankUpdate = 52;
constexpr int RevivalUserMsgXpUpdate = 65;

void RevivalAppendVarint(std::vector<uint8_t> &out, uint64_t value)
{
    while (value >= 0x80)
    {
        out.push_back(static_cast<uint8_t>((value & 0x7Fu) | 0x80u));
        value >>= 7;
    }
    out.push_back(static_cast<uint8_t>(value));
}

void RevivalAppendLengthDelimited(
    std::vector<uint8_t> &out, uint8_t fieldTag,
    const void *data, size_t size)
{
    out.push_back(fieldTag);
    RevivalAppendVarint(out, size);
    const auto *begin = reinterpret_cast<const uint8_t *>(data);
    out.insert(out.end(), begin, begin + size);
}

bool RevivalBuildEndMatchUiMessages(
    uint32_t accountId,
    uint32_t oldRank, uint32_t newRank, uint32_t wins,
    uint32_t oldLevel, uint32_t oldXp,
    uint32_t oldProfileWeek, uint32_t oldWeeklyBaseXp,
    uint32_t newProfileWeek, uint32_t newWeeklyBaseXp,
    uint32_t awardedXp,
    std::vector<uint8_t> &rankMsg,
    std::vector<uint8_t> &xpMsg)
{
#ifdef _WIN32
    // Build the exact CCSUsrMsg_ServerRankUpdate wire payload:
    // repeated RankUpdate rank_update = 1.
    std::vector<uint8_t> rankInner;
    rankInner.push_back(0x08); // account_id = 1
    RevivalAppendVarint(rankInner, accountId);
    rankInner.push_back(0x10); // rank_old = 2
    RevivalAppendVarint(rankInner, oldRank);
    rankInner.push_back(0x18); // rank_new = 3
    RevivalAppendVarint(rankInner, newRank);
    rankInner.push_back(0x20); // num_wins = 4
    RevivalAppendVarint(rankInner, wins);

    // rank_change = 5 (fixed32 float) and rank_type_id = 6 are part of
    // Valve's stock RankUpdate object. Some client builds ignore incomplete
    // skill-group records even when account/ranks/wins are present.
    rankInner.push_back(0x2D);
    const float rankChange =
        newRank > oldRank ? 1.0f : (newRank < oldRank ? -1.0f : 0.0f);
    const auto *rankChangeBytes =
        reinterpret_cast<const uint8_t *>(&rankChange);
    rankInner.insert(
        rankInner.end(), rankChangeBytes,
        rankChangeBytes + sizeof(rankChange));

    rankInner.push_back(0x30); // rank_type_id = 6
    RevivalAppendVarint(rankInner, 6); // Competitive

    rankMsg.clear();
    RevivalAppendLengthDelimited(
        rankMsg, 0x0A, rankInner.data(), rankInner.size());

    // Build the exact CCSUsrMsg_XpUpdate payload. The end-of-match Panorama
    // reads current_level/current_xp as PRE-AWARD state and animates each
    // xp_progress_data chunk from there.
    CMsgGCCstrike15_v2_GC2ServerNotifyXPRewarded xp;
    xp.set_account_id(accountId);
    xp.set_current_xp(oldXp);
    xp.set_current_level(oldLevel);

    const uint64_t weeklyStart =
        oldProfileWeek == newProfileWeek ? oldWeeklyBaseXp : 0u;
    const uint64_t weeklyEnd = newWeeklyBaseXp;
    const uint32_t baseXp = weeklyEnd >= weeklyStart
        ? static_cast<uint32_t>(
            std::min<uint64_t>(weeklyEnd - weeklyStart, UINT32_MAX))
        : 0u;

    auto overlap = [](uint64_t begin, uint64_t finish,
                      uint64_t lo, uint64_t hi) -> uint64_t
    {
        const uint64_t a = std::max(begin, lo);
        const uint64_t b = std::min(finish, hi);
        return b > a ? b - a : 0;
    };
    auto cumulativeBonus = [](uint64_t raw) -> uint64_t
    {
        const uint64_t triple = std::min<uint64_t>(raw * 3, 3500);
        const uint64_t secondRaw = raw > 1167 ? raw - 1167 : 0;
        const uint64_t single = std::min<uint64_t>(secondRaw, 1500);
        return triple + single;
    };

    if (awardedXp)
    {
        const uint32_t normalBase = static_cast<uint32_t>(
            overlap(weeklyStart, weeklyEnd, 0, 6167));
        const uint32_t reducedBase =
            baseXp > normalBase ? baseXp - normalBase : 0;
        const uint32_t reducedAward = static_cast<uint32_t>(
            (static_cast<uint64_t>(reducedBase) * 175u) / 1000u);
        const uint32_t bonusAward = static_cast<uint32_t>(
            cumulativeBonus(weeklyEnd) - cumulativeBonus(weeklyStart));

        if (normalBase)
        {
            auto *p = xp.add_xp_progress_data();
            p->set_xp_points(normalBase);
            p->set_xp_category(2); // CompetitiveRoundWins
        }
        if (reducedAward)
        {
            auto *p = xp.add_xp_progress_data();
            p->set_xp_points(reducedAward);
            p->set_xp_category(52); // CompetitiveRoundWinsReduced
        }
        if (bonusAward)
        {
            auto *p = xp.add_xp_progress_data();
            p->set_xp_points(bonusAward);
            p->set_xp_category(3); // BonusBoost
        }

        const uint32_t encoded =
            normalBase + reducedAward + bonusAward;
        if (encoded < awardedXp)
        {
            auto *p = xp.add_xp_progress_data();
            p->set_xp_points(awardedXp - encoded);
            p->set_xp_category(2);
        }
    }

    std::string xpInner;
    xp.SerializeToString(&xpInner);
    xpMsg.clear();
    RevivalAppendLengthDelimited(
        xpMsg, 0x0A, xpInner.data(), xpInner.size());

    Platform::Print(
        "REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V1 built rank52=%zu xp65=%zu "
        "old_rank=%u new_rank=%u wins=%u old_level=%u old_xp=%u award=%u\n",
        rankMsg.size(), xpMsg.size(),
        oldRank, newRank, wins, oldLevel, oldXp, awardedXp);
    return !rankMsg.empty() && !xpMsg.empty();
#else
    (void)accountId; (void)oldRank; (void)newRank; (void)wins;
    (void)oldLevel; (void)oldXp; (void)oldProfileWeek;
    (void)oldWeeklyBaseXp; (void)newProfileWeek;
    (void)newWeeklyBaseXp; (void)awardedXp;
    (void)rankMsg; (void)xpMsg;
    return false;
#endif
}

bool WriteMatchmakingBridgeFile(const char *path, const std::string &text)
{
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out.is_open())
        return false;
    out.write(text.data(), static_cast<std::streamsize>(text.size()));
    return out.good();
}

std::unordered_map<std::string, std::string> ReadMatchmakingBridgeFile(const char *path)
{
    std::unordered_map<std::string, std::string> out;
    std::ifstream in(path, std::ios::binary);
    if (!in.is_open())
        return out;

    std::string line;
    while (std::getline(in, line))
    {
        const size_t eq = line.find('=');
        if (eq == std::string::npos)
            continue;
        std::string key = line.substr(0, eq);
        std::string value = line.substr(eq + 1);
        if (!key.empty())
            out[key] = value;
    }
    return out;
}

uint64_t BridgeU64(const std::unordered_map<std::string, std::string> &kv,
    const char *key, uint64_t fallback = 0)
{
    auto it = kv.find(key);
    if (it == kv.end() || it->second.empty())
        return fallback;
    char *end = nullptr;
    unsigned long long value = std::strtoull(it->second.c_str(), &end, 10);
    return (end && *end == '\0') ? static_cast<uint64_t>(value) : fallback;
}

std::vector<uint32_t> BridgeU32List(
    const std::unordered_map<std::string, std::string> &kv, const char *key)
{
    std::vector<uint32_t> out;
    auto it = kv.find(key);
    if (it == kv.end())
        return out;

    std::istringstream ss(it->second);
    std::string part;
    while (std::getline(ss, part, ','))
    {
        if (part.empty())
            continue;
        char *end = nullptr;
        unsigned long value = std::strtoul(part.c_str(), &end, 10);
        if (end && *end == '\0' && value <= UINT32_MAX)
            out.push_back(static_cast<uint32_t>(value));
    }
    return out;
}

std::string RevivalNumericServerAddress(uint32_t ip, uint32_t port)
{
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "%u.%u.%u.%u:%u",
        (ip >> 24) & 0xff,
        (ip >> 16) & 0xff,
        (ip >> 8) & 0xff,
        ip & 0xff,
        port);
    return buffer;
}

#ifdef _WIN32
constexpr uint32_t RevivalConnectionlessHeader = 0xFFFFFFFFu;
constexpr uint8_t RevivalReserveCheckResponseOpcode = 0x25;
constexpr size_t RevivalReserveCheckResponseSize = 19;

std::atomic<bool> g_revAcceptArmed{ false };
std::atomic<bool> g_revAcceptFullyAccepted{ false };
std::atomic<uint32_t> g_revAcceptIp{ 0 };
std::atomic<uint16_t> g_revAcceptPort{ 0 };
std::atomic<uint32_t> g_revAcceptExpectedPlayers{ 0 };

uint32_t RevivalReadU32(const uint8_t *data)
{
    uint32_t value = 0;
    memcpy(&value, data, sizeof(value));
    return value;
}

using RevivalWSARecvFromFn = int(WSAAPI *)(SOCKET, LPWSABUF, DWORD, LPDWORD, LPDWORD,
    sockaddr *, LPINT, LPWSAOVERLAPPED, LPWSAOVERLAPPED_COMPLETION_ROUTINE);
RevivalWSARecvFromFn g_revOriginalWSARecvFrom = nullptr;

void RevivalInspectReserveCheckResponse(
    const uint8_t *packet, const sockaddr *from, int fromLen)
{
    if (!g_revAcceptArmed.load(std::memory_order_relaxed))
        return;
    if (RevivalReadU32(packet) != RevivalConnectionlessHeader
        || packet[4] != RevivalReserveCheckResponseOpcode)
        return;
    if (!from || fromLen < static_cast<int>(sizeof(sockaddr_in))
        || from->sa_family != AF_INET)
        return;

    const sockaddr_in *fromIn = reinterpret_cast<const sockaddr_in *>(from);
    const uint32_t expectedIp = g_revAcceptIp.load(std::memory_order_relaxed);
    const uint16_t expectedPort = g_revAcceptPort.load(std::memory_order_relaxed);
    const uint32_t actualIp = ntohl(fromIn->sin_addr.s_addr);
    const uint16_t actualPort = ntohs(fromIn->sin_port);

    Platform::Print(
        "REVIVAL_CLIENT_ACCEPT_WATCH_V1 raw 0x25 from=%u.%u.%u.%u:%u expected=%u.%u.%u.%u:%u\n",
        (actualIp >> 24) & 0xff, (actualIp >> 16) & 0xff,
        (actualIp >> 8) & 0xff, actualIp & 0xff,
        static_cast<unsigned>(actualPort),
        (expectedIp >> 24) & 0xff, (expectedIp >> 16) & 0xff,
        (expectedIp >> 8) & 0xff, expectedIp & 0xff,
        static_cast<unsigned>(expectedPort));

    if ((expectedIp && actualIp != expectedIp)
        || (expectedPort && actualPort != expectedPort))
        return;

    const uint32_t stage = RevivalReadU32(packet + 13);
    const uint8_t awaiting = packet[17];
    const uint8_t total = packet[18];

    Platform::Print(
        "REVIVAL_CLIENT_ACCEPT_WATCH_V1 0x25 stage=%u awaiting=%u total=%u\n",
        stage, awaiting, total);

    const uint32_t expectedPlayers =
        g_revAcceptExpectedPlayers.load(std::memory_order_relaxed);
    if (expectedPlayers && awaiting != 0x7f && total != expectedPlayers)
    {
        Platform::Print(
            "matchmaking: WARNING reservation roster total=%u expected=%u\n",
            total, expectedPlayers);
    }

    if (stage == 2 && awaiting == 0
        && g_revAcceptArmed.exchange(false, std::memory_order_acq_rel))
    {
        g_revAcceptFullyAccepted.store(true, std::memory_order_release);
        Platform::Print(
            "matchmaking: stock reservation reached stage 2 awaiting=0; final connect reserve pending\n");
    }
}

int WSAAPI RevivalHookWSARecvFrom(
    SOCKET s, LPWSABUF buffers, DWORD bufferCount, LPDWORD bytesReceived,
    LPDWORD flags, sockaddr *from, LPINT fromLen,
    LPWSAOVERLAPPED overlapped,
    LPWSAOVERLAPPED_COMPLETION_ROUTINE completion)
{
    const int result = g_revOriginalWSARecvFrom(
        s, buffers, bufferCount, bytesReceived, flags, from, fromLen,
        overlapped, completion);

    if (result == 0 && !overlapped && bufferCount == 1 && buffers
        && bytesReceived && *bytesReceived == RevivalReserveCheckResponseSize)
    {
        RevivalInspectReserveCheckResponse(
            reinterpret_cast<const uint8_t *>(buffers[0].buf),
            from, fromLen ? *fromLen : 0);
    }
    return result;
}

void RevivalInstallAcceptWatcher()
{
    static bool attempted = false;
    if (attempted)
        return;
    attempted = true;

    HMODULE ws2 = GetModuleHandleA("ws2_32.dll");
    if (!ws2)
        ws2 = LoadLibraryA("ws2_32.dll");
    void *target = ws2
        ? reinterpret_cast<void *>(GetProcAddress(ws2, "WSARecvFrom"))
        : nullptr;
    if (!target)
    {
        Platform::Print(
            "REVIVAL_CLIENT_ACCEPT_WATCH_V1 failed: ws2_32!WSARecvFrom unavailable\n");
        return;
    }

    funchook_t *hook = funchook_create();
    void *bridge = target;
    if (!hook
        || funchook_prepare(hook, &bridge,
            reinterpret_cast<void *>(RevivalHookWSARecvFrom)) != 0
        || funchook_install(hook, 0) != 0)
    {
        Platform::Print(
            "REVIVAL_CLIENT_ACCEPT_WATCH_V1 failed: funchook install failed\n");
        return;
    }

    g_revOriginalWSARecvFrom =
        reinterpret_cast<RevivalWSARecvFromFn>(bridge);
    Platform::Print(
        "REVIVAL_CLIENT_ACCEPT_WATCH_V1 active (stock 0x25 stage watcher)\n");
}

void RevivalArmAcceptWatcher(
    uint32_t serverIp, uint16_t serverPort, uint32_t expectedPlayers)
{
    g_revAcceptIp.store(serverIp, std::memory_order_relaxed);
    g_revAcceptPort.store(serverPort, std::memory_order_relaxed);
    g_revAcceptExpectedPlayers.store(expectedPlayers, std::memory_order_relaxed);
    g_revAcceptFullyAccepted.store(false, std::memory_order_relaxed);
    g_revAcceptArmed.store(true, std::memory_order_release);
    Platform::Print(
        "matchmaking: armed stock Accept watcher ip=%u.%u.%u.%u port=%u roster=%u\n",
        (serverIp >> 24) & 0xff, (serverIp >> 16) & 0xff,
        (serverIp >> 8) & 0xff, serverIp & 0xff,
        static_cast<unsigned>(serverPort), expectedPlayers);
}

void RevivalDisarmAcceptWatcher()
{
    g_revAcceptArmed.store(false, std::memory_order_release);
    g_revAcceptFullyAccepted.store(false, std::memory_order_release);
}
#else
void RevivalInstallAcceptWatcher() {}
void RevivalArmAcceptWatcher(uint32_t, uint16_t, uint32_t) {}
void RevivalDisarmAcceptWatcher() {}
#endif

} // namespace

ClientGC::ClientGC(uint64_t steamId)
    : m_steamId{ steamId }
    , m_inventory{ steamId }
{
    // also called from ServerGC's constructor
    Graffiti::Initialize();

    StartThread();
    RevivalInstallAcceptWatcher();

    Platform::Print("ClientGC spawned for user %llu\n", steamId);
}

ClientGC::~ClientGC()
{
    StopThread();
    Platform::Print("ClientGC destroyed\n");
}

void ClientGC::HandleIdle()
{
    PollRewardBridge();
    PollOperationMissionSelectionBridge();

#ifdef _WIN32
    if (g_revAcceptFullyAccepted.exchange(false, std::memory_order_acq_rel))
        SendMatchmakingConnectReserve();
#endif

    // SharedGC wakes every 250 ms. Keep polling the tiny local state file
    // twice per second even after the stock client stops the SEARCH phase.
    // MatchEnd results arrive after Accept, when m_matchmakingActive is false.
    if ((++m_matchmakingIdleTicks & 1u) == 0)
        PollMatchmakingBridge();
}

void ClientGC::HandleEvent(GCEvent type, uint64_t id, const std::vector<uint8_t> &buffer)
{
    switch (type)
    {
    case GCEvent::Message:
        HandleMessage(static_cast<uint32_t>(id), buffer.data(), static_cast<uint32_t>(buffer.size()));
        break;

    case GCEvent::NetMessage:
        HandleNetMessage(buffer.data(), static_cast<uint32_t>(buffer.size()));
        break;

    case GCEvent::SOCacheRequest:
        HandleSOCacheRequest();
        break;

    default:
        assert(false);
        break;
    }
}

void ClientGC::HandleMessage(uint32_t type, const void *data, uint32_t size)
{
    GCMessageRead messageRead{ type, data, size };
    if (!messageRead.IsValid())
    {
        assert(false);
        return;
    }

    if (messageRead.IsProtobuf())
    {
        switch (messageRead.TypeUnmasked())
        {
        case k_EMsgGCClientHello:
            OnClientHello(messageRead);
            break;

        case k_EMsgGCAdjustItemEquippedState:
            AdjustItemEquippedState(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_ClientPlayerDecalSign:
            ClientPlayerDecalSign(messageRead);
            break;

        case k_EMsgGCUseItemRequest:
            UseItemRequest(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_ClientRequestJoinServerData:
            ClientRequestJoinServerData(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_MatchmakingStart:
            MatchmakingStart(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_MatchmakingStop:
            MatchmakingStop(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_MatchmakingClient2ServerPing:
            MatchmakingPing(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_MatchmakingClient2GCHello:
            MatchmakingHello(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_ClientRequestNewMission:
            ClientRequestNewMission(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_ClientRedeemMissionReward:
            ClientRedeemMissionReward(messageRead);
            break;

        case k_EMsgGCSetItemPositions:
            SetItemPositions(messageRead);
            break;

        case k_EMsgGCApplySticker:
            ApplySticker(messageRead);
            break;

        case k_EMsgGCStoreGetUserData:
            StoreGetUserData(messageRead);
            break;

        case k_EMsgGCStorePurchaseInit:
            StorePurchaseInit(messageRead);
            break;

        case k_EMsgGCStorePurchaseFinalize:
            StorePurchaseFinalize(messageRead);
            break;

        default:
            Platform::Print("ClientGC::HandleMessage: unhandled protobuf message %s\n",
                MessageName(messageRead.TypeUnmasked()));
            break;
        }
    }
    else
    {
        switch (messageRead.TypeUnmasked())
        {
        case k_EMsgGCDelete:
            DeleteItem(messageRead);
            break;

        case k_EMsgGCUnlockCrate:
            UnlockCrate(messageRead);
            break;

        case k_EMsgGCNameItem:
            NameItem(messageRead);
            break;

        case k_EMsgGCNameBaseItem:
            NameBaseItem(messageRead);
            break;

        case k_EMsgGCRemoveItemName:
            RemoveItemName(messageRead);
            break;

        case k_EMsgGCCraft:
            // trade-up contracts (revival addition) --- stage 1: just log it
            Craft(messageRead, static_cast<const uint8_t *>(data), size);
            break;

        default:
            Platform::Print("ClientGC::HandleMessage: unhandled struct message %s\n",
                MessageName(messageRead.TypeUnmasked()));
            break;
        }
    }
}

void ClientGC::HandleNetMessage(const void *data, uint32_t size)
{
    // pass 0 as type so it gets parsed from the message
    GCMessageRead messageRead{ 0, data, size };
    if (!messageRead.IsValid())
    {
        assert(false);
        return;
    }

    if (messageRead.IsProtobuf())
    {
        switch (messageRead.TypeUnmasked())
        {
        case k_EMsgGC_IncrementKillCountAttribute:
            IncrementKillCountAttribute(messageRead);
            return;

        case k_EMsgGCCStrike15_v2_MatchEndRunRewardDrops:
            MatchEndRunRewardDrops(messageRead);
            return;
        }
    }

    Platform::Print("ClientGC::HandleNetMessage: unhandled protobuf message %s\n",
        MessageName(messageRead.TypeUnmasked()));
}

void ClientGC::MatchEndRunRewardDrops(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchEndRunRewardDrops message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgGCCStrike15_v2_MatchEndRunRewardDrops failed, ignoring\n");
        return;
    }

    uint64_t reservationId = 0;
    uint64_t matchId = 0;
    if (message.has_serverinfo())
    {
        const CMsgGCCStrike15_v2_MatchmakingServerReservationResponse &serverInfo =
            message.serverinfo();
        if (serverInfo.has_reservationid())
            reservationId = serverInfo.reservationid();
        if (serverInfo.has_reservation()
            && serverInfo.reservation().has_match_id())
        {
            matchId = serverInfo.reservation().match_id();
        }
    }

    bool revivalMissionPacket = false;
    uint32_t revivalMissionRounds = 0;
    bool revivalMissionWon = false;
    if (message.has_match_end_quest_data())
    {
        const std::string &marker =
            message.match_end_quest_data().binary_data();
        unsigned rounds = 0;
        unsigned won = 0;
        if (std::sscanf(
                marker.c_str(), "RVOPM1:%u:%u", &rounds, &won) == 2)
        {
            revivalMissionPacket = true;
            revivalMissionRounds = static_cast<uint32_t>(rounds);
            revivalMissionWon = won != 0;
        }
    }

    const bool serverAuthoritativeItems =
        revivalMissionPacket
        || (m_revivalAuthoritativeMatchId
            && ((matchId && matchId == m_revivalAuthoritativeMatchId)
                || (!matchId && reservationId == GameServerCookieId)));

    // Mission fallback packets have their own transaction id because they may
    // arrive before or after SRCDS' normal 9136. Never let ordinary match-end
    // dedupe suppress mission progress, and never apply one mission twice.
    if (revivalMissionPacket)
    {
        if (matchId
            && (matchId == m_lastOperationMissionMatchId
                || matchId == m_lastMissionProgressMatchId))
        {
            Platform::Print(
                "REVIVAL_REPEATABLE_MISSIONS_V5 duplicate mission packet "
                "ignored for match %llu\n",
                static_cast<unsigned long long>(matchId));
            return;
        }
    }
    else
    {
        // Direct-UDP revival uses the same GC-welcome cookie as reservation id
        // on every match, so reservationid alone is NOT a valid transaction key.
        if (matchId && matchId == m_lastRewardedMatchId)
        {
            Platform::Print(
                "progression: duplicate 9136 ignored for match %llu\n",
                matchId);
            return;
        }
        if (!matchId && reservationId && reservationId != GameServerCookieId
            && reservationId == m_lastRewardedReservation)
        {
            Platform::Print(
                "progression: duplicate 9136 ignored for reservation %llu\n",
                reservationId);
            return;
        }
    }

    if (!message.has_match_end_quest_data())
    {
        return;
    }

    CMsgSOMultipleObjects operationUpdate;
    bool operationChanged = false;
    bool processedPlayer = false;

    const CMsgGC_ServerQuestUpdateData &questData = message.match_end_quest_data();
    for (const PlayerQuestData &playerData : questData.player_quest_data())
    {
        if (playerData.has_quester_account_id()
            && playerData.quester_account_id() != AccountId())
        {
            continue;
        }

        processedPlayer = true;

        uint64_t baseXp64 = 0;
        for (const XpProgressData &xp : playerData.xp_progress_data())
        {
            baseXp64 += xp.xp_points();
        }
        if (baseXp64 > UINT32_MAX)
        {
            baseXp64 = UINT32_MAX;
        }
        const uint32_t baseXp = static_cast<uint32_t>(baseXp64);

        uint32_t levelsGained = 0;
        uint32_t awardedXp = 0;
        if (!serverAuthoritativeItems)
        {
            awardedXp =
                m_inventory.ApplyWeeklyProfileXp(baseXp, &levelsGained);

            if (awardedXp)
            {
                CMsgSOMultipleObjects profileUpdate;
                m_inventory.BuildProfilePersonaUpdate(profileUpdate);
                SendMessageToGame(true, k_ESOMsg_UpdateMultiple, profileUpdate);

                CMsgGCCStrike15_v2_MatchmakingGC2ClientHello profileHello;
                BuildMatchmakingHello(profileHello);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchmakingGC2ClientHello,
                    profileHello);
            }
        }

        if (!serverAuthoritativeItems)
        {
        // Legacy profile-rank reward: at most once per Wednesday reset and only
        // after actually crossing a 5000-XP profile-rank boundary.
        if (levelsGained)
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.CreateWeeklyLevelReward(create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        // Revival Competitive drop policy: two cases remain guaranteed, but
        // each roll uses the full schema-derived case pool with old cases rarer.
        // Skins and old event containers are separate chance-based bonuses.
        for (int guaranteedCase = 0; guaranteedCase < 2; ++guaranteedCase)
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.CreateRandomCaseMatchDrop(create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.CreateRareLegacyStickerCapsuleMatchDrop(
                50, create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.CreateRareLegacySouvenirPackageMatchDrop(
                200, create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        static const std::vector<std::string_view> StandardSkinCollections{
            "set_dust_2_2021",
            "set_cache"
        };
        static const std::vector<std::string_view> CobblestoneCollection{
            "set_cobblestone"
        };

        // Standard map skin: 1/3 per completed match, rather than always.
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.CreateRareCollectionBonusMatchDrop(
                StandardSkinCollections, 3, create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        // Separate 1/20 Cobblestone bonus roll; the collection's own rarity
        // weighting still makes Dragon Lore extremely rare.
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.CreateRareCollectionBonusMatchDrop(
                CobblestoneCollection, 20, create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        // Preserve legacy playtime accounting. It can still produce the normal
        // weekly timed case as an additional bonus beyond the guaranteed drops.
        if (playerData.has_time_played() && playerData.time_played())
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (m_inventory.AddMatchPlaytimeAndCreateCaseDrop(
                playerData.time_played(), create, drop))
            {
                SendMessageToGame(true, k_ESOMsg_Create, create);
                SendMessageToGame(false,
                    k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop);
            }
        }

        } // !serverAuthoritativeItems
        else
        {
            Platform::Print(
                "REVIVAL_SERVER_ITEM_AUTHORITY_V1 skipped client item reroll match=%llu\n",
                static_cast<unsigned long long>(
                    matchId ? matchId : m_revivalAuthoritativeMatchId));
        }

        // The server reports Competitive wins using the same Steam-user-stat
        // delta Valve used. A 15-round non-win in classic MR15 is a tie; all
        // other non-wins are treated as losses for the hidden revival rating.
        bool won = false;
        for (const CMsgCsgoSteamUserStatChange &stat : playerData.userstatchanges())
        {
            if (stat.ecsgosteamuserstat()
                    == k_ECsgoSteamUserStat_MatchWinsCompetitive
                && stat.delta() > 0)
            {
                won = true;
                break;
            }
        }
        uint32_t roundsWon = baseXp / 30;
        if (revivalMissionPacket)
        {
            roundsWon = revivalMissionRounds;
            won = revivalMissionWon;
        }

        const bool tied = !won && roundsWon == 15;
        if (!serverAuthoritativeItems
            && m_inventory.ApplyCompetitiveMatchResult(won, tied))
        {
            SendRankUpdate();
        }

        // Operation missions share the same real match-end path. The custom
        // RVOPM1 packet is only a Direct-UDP fallback carrying authoritative
        // map/round result; the Inventory still evaluates the original Riptide
        // quest graph and emits the normal SeasonalOperation/QuestProgress SOs.
        const bool operationEligible =
            !playerData.has_operation_points_eligible()
            || playerData.operation_points_eligible();

        if (operationEligible && revivalMissionPacket)
        {
            std::string missionMap;
            if (message.has_serverinfo() && message.serverinfo().has_map())
            {
                missionMap = message.serverinfo().map();
            }

            if (m_inventory.ApplySelectedOperationCompetitiveMission(
                    missionMap, roundsWon, won, operationUpdate))
            {
                operationChanged = true;
            }
        }
        else if (operationEligible)
        {
            uint32_t revivalSelectedQuest = 0;
            if (serverAuthoritativeItems
                && message.has_serverinfo()
                && message.serverinfo().has_map())
            {
                revivalSelectedQuest =
                    m_inventory.PreferredOperationMissionQuest(
                        message.serverinfo().map());
            }

            if (revivalSelectedQuest)
            {
                // The V4 fallback is authoritative for revival Competitive
                // missions. Ignore SRCDS' parallel native quest deltas for this
                // match so the same win cannot award stars twice.
                Platform::Print(
                    "REVIVAL_REPEATABLE_MISSIONS_V4 deferring native quest "
                    "deltas to fallback quest=%u map=%s\n",
                    revivalSelectedQuest, message.serverinfo().map().c_str());
            }
            else
            {
                for (const PlayerQuestData::QuestItemData &quest :
                    playerData.quest_item_data())
                {
                    if (!quest.has_quest_id() || quest.quest_id() > UINT32_MAX)
                    {
                        continue;
                    }

                    const int normal = quest.has_quest_normal_points_earned()
                        ? quest.quest_normal_points_earned() : 0;
                    const int bonus = quest.has_quest_bonus_points_earned()
                        ? quest.quest_bonus_points_earned() : 0;

                    if (m_inventory.ApplyOperationQuestProgress(
                        static_cast<uint32_t>(quest.quest_id()),
                        normal, bonus, operationUpdate))
                    {
                        operationChanged = true;
                    }
                }
            }
        }

        // gc_server already split 9136 per account, so there should only be one
        // relevant PlayerQuestData. Do not process accidental duplicates.
        break;
    }

    if (operationChanged)
    {
        SendMessageToGame(true, k_ESOMsg_UpdateMultiple, operationUpdate);
    }

    if (processedPlayer)
    {
        if (revivalMissionPacket)
        {
            if (matchId)
            {
                m_lastOperationMissionMatchId = matchId;
                m_lastMissionProgressMatchId = matchId;
            }
        }
        else
        {
            if (matchId)
                m_lastRewardedMatchId = matchId;
            if (reservationId)
                m_lastRewardedReservation = reservationId;
        }
    }

    if (revivalMissionPacket)
    {
        Platform::Print(
            "REVIVAL_REPEATABLE_MISSIONS_V5 completed mission packet "
            "match=%llu account=%u rounds=%u won=%u\n",
            static_cast<unsigned long long>(matchId), AccountId(),
            revivalMissionRounds, revivalMissionWon ? 1u : 0u);
    }
    else
    {
        Platform::Print(
            "progression: completed match-end processing "
            "match=%llu reservation=%llu account=%u\n",
            matchId, reservationId, AccountId());
    }
}


void ClientGC::HandleSOCacheRequest()
{
    CMsgSOCacheSubscribed message;
    m_inventory.BuildCacheSubscription(message, m_inventory.ProfileLevel(), true);

    GCMessageWrite messageWrite{ k_ESOMsg_CacheSubscribed, message };
    PostToHost(HostEvent::NetMessage, 0, messageWrite.Data(), messageWrite.Size());
}

void ClientGC::SendMessageToGame(bool sendToGameServer, uint32_t type,
    const google::protobuf::MessageLite &message, uint64_t jobId)
{
    GCMessageWrite messageWrite{ type, message, jobId };

    if (sendToGameServer)
    {
        PostToHost(HostEvent::NetMessage, 0, messageWrite.Data(), messageWrite.Size());
    }

    PostToHost(HostEvent::Message, messageWrite.TypeMasked(), messageWrite.Data(), messageWrite.Size());
}

constexpr uint32_t MakeAddress(uint32_t v1, uint32_t v2, uint32_t v3, uint32_t v4)
{
    return v4 | (v3 << 8) | (v2 << 16) | (v1 << 24);
}

static void BuildCSWelcome(CMsgCStrike15Welcome &message)
{
    // mikkotodo cleanup dox
    message.set_store_item_hash(136617352);
    message.set_timeplayedconsecutively(0);
    message.set_time_first_played(1329845773);
    message.set_last_time_played(1680260376);
    message.set_last_ip_address(MakeAddress(127, 0, 0, 1));
}

void ClientGC::BuildMatchmakingHello(CMsgGCCStrike15_v2_MatchmakingGC2ClientHello &message)
{
    message.set_account_id(AccountId());

    // this is the state of csgo matchmaking in 2024
    message.mutable_global_stats()->set_players_online(0);
    message.mutable_global_stats()->set_servers_online(0);
    message.mutable_global_stats()->set_players_searching(0);
    message.mutable_global_stats()->set_servers_available(0);
    message.mutable_global_stats()->set_ongoing_matches(0);
    message.mutable_global_stats()->set_search_time_avg(0);

    // don't write search_statistics

    message.mutable_global_stats()->set_main_post_url("");

    // bullshit
    message.mutable_global_stats()->set_required_appid_version(13857);
    message.mutable_global_stats()->set_pricesheet_version(1680057676); // mikkotodo revisit
    message.mutable_global_stats()->set_twitch_streams_version(2);
    message.mutable_global_stats()->set_active_tournament_eventid(20);
    message.mutable_global_stats()->set_active_survey_id(0);
    message.mutable_global_stats()->set_required_appid_version2(13862); // csgo s2

    message.set_vac_banned(GetConfig().VacBanned());
    message.mutable_commendation()->set_cmd_friendly(GetConfig().CommendedFriendly());
    message.mutable_commendation()->set_cmd_teaching(GetConfig().CommendedTeaching());
    message.mutable_commendation()->set_cmd_leader(GetConfig().CommendedLeader());
    message.set_player_level(m_inventory.ProfileLevel());
    message.set_player_cur_xp(m_inventory.ProfileXp());
}

void ClientGC::BuildClientWelcome(CMsgClientWelcome &message, const CMsgCStrike15Welcome &csWelcome,
    const CMsgGCCStrike15_v2_MatchmakingGC2ClientHello &matchmakingHello)
{
    // mikkotodo remove dox
    message.set_version(0); // this is accurate
    message.set_game_data(csWelcome.SerializeAsString());
    m_inventory.BuildCacheSubscription(*message.add_outofdate_subscribed_caches(), m_inventory.ProfileLevel(), false);
    message.mutable_location()->set_latitude(65.0133006f);
    message.mutable_location()->set_longitude(25.4646212f);
    message.mutable_location()->set_country("FI"); // finland
    message.set_game_data2(matchmakingHello.SerializeAsString());
    message.set_rtime32_gc_welcome_timestamp(static_cast<uint32_t>(time(nullptr)));
    message.set_currency(2); // euros
    message.set_txn_country_code("FI"); // finland
}

void ClientGC::SendRankUpdate()
{
    CMsgGCCStrike15_v2_ClientGCRankUpdate message;

    PlayerRankingInfo *rank = message.add_rankings();
    rank->set_account_id(AccountId());
    rank->set_rank_id(m_inventory.CompetitiveRank());
    rank->set_wins(m_inventory.CompetitiveWins());
    rank->set_rank_type_id(RankTypeCompetitive);

    rank = message.add_rankings();
    rank->set_account_id(AccountId());
    rank->set_rank_id(GetConfig().WingmanRank());
    rank->set_wins(GetConfig().WingmanWins());
    rank->set_rank_type_id(RankTypeWingman);

    rank = message.add_rankings();
    rank->set_account_id(AccountId());
    rank->set_rank_id(GetConfig().DangerZoneRank());
    rank->set_wins(GetConfig().DangerZoneWins());
    rank->set_rank_type_id(RankTypeDangerZone);

    SendMessageToGame(false, k_EMsgGCCStrike15_v2_ClientGCRankUpdate, message);
}

void ClientGC::OnClientHello(GCMessageRead &messageRead)
{
    Platform::Print("REVIVAL_MM_BRIDGE_CLEAN_V1 loaded\n");
    Platform::Print("REVIVAL_CLIENT_COOKIE_RESERVE_V3 active; REVIVAL_CLIENT_DIRECT_UDP_V1 active; REVIVAL_CLIENT_READY_FLOW_V1 active; REVIVAL_CLIENT_ACCEPT_WATCH_V1 active; REVIVAL_CLIENT_DIRECT_ACCEPT_ROUTE_V2 active; REVIVAL_CLIENT_REWARD_BRIDGE_V1 active; REVIVAL_GUARANTEED_MATCH_DROPS_V1 active; REVIVAL_CLIENT_COOKIE_RESERVE_V2 compatible\n");

    CMsgClientHello hello;
    if (!messageRead.ReadProtobuf(hello))
    {
        Platform::Print("Parsing CMsgClientHello failed, ignoring\n");
        return;
    }

    // we don't care about anything in this message, just reply
    CMsgCStrike15Welcome csWelcome;
    BuildCSWelcome(csWelcome);

    CMsgGCCStrike15_v2_MatchmakingGC2ClientHello mmHello;
    BuildMatchmakingHello(mmHello);

    CMsgClientWelcome clientWelcome;
    BuildClientWelcome(clientWelcome, csWelcome, mmHello);

    SendMessageToGame(false, k_EMsgGCClientWelcome, clientWelcome);

    // the real gc sends this a bit later when it has more info to put on it
    // however we have everything at our fingertips so send it right away
    // mikkotodo is this even needed? k_EMsgGCClientWelcome should have it all already
    SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientHello, mmHello);

    // send all ranks here as well, it's a bit back and forth with real gc
    SendRankUpdate();
}

void ClientGC::AdjustItemEquippedState(GCMessageRead &messageRead)
{
    CMsgAdjustItemEquippedState message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgAdjustItemEquippedState failed, ignoring\n");
        return;
    }

    CMsgSOMultipleObjects update;
    if (!m_inventory.EquipItem(message.item_id(), message.new_class(), message.new_slot(), update))
    {
        // no change
        assert(false);
        return;
    }

    // let the gameserver know, too
    SendMessageToGame(true, k_ESOMsg_UpdateMultiple, update);
}

void ClientGC::ClientPlayerDecalSign(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_ClientPlayerDecalSign message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgGCCStrike15_v2_ClientPlayerDecalSign failed, ignoring\n");
        return;
    }

    if (!Graffiti::SignMessage(*message.mutable_data()))
    {
        Platform::Print("Could not sign graffiti! it won't appear\n");
        return;
    }

    SendMessageToGame(false, k_EMsgGCCStrike15_v2_ClientPlayerDecalSign, message);
}

void ClientGC::UseItemRequest(GCMessageRead &messageRead)
{
    CMsgUseItem message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgUseItem failed, ignoring\n");
        return;
    }

    CMsgSOSingleObject destroy;
    CMsgSOMultipleObjects updateMultiple;
    CMsgGCItemCustomizationNotification notification;

    if (m_inventory.UseItem(message.item_id(), destroy, updateMultiple, notification))
    {
        SendMessageToGame(true, k_ESOMsg_Destroy, destroy);
        SendMessageToGame(true, k_ESOMsg_UpdateMultiple, updateMultiple);

        // Operation pass/star-pack uses do not need an item-customization popup.
        // Graffiti still sets request and keeps the original notification flow.
        if (notification.has_request())
        {
            SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
        }
    }
}

static void AddressString(uint32_t ip, uint32_t port, char *buffer, size_t bufferSize)
{
    snprintf(buffer, bufferSize,
        "%u.%u.%u.%u:%u",
        (ip >> 24) & 0xff,
        (ip >> 16) & 0xff,
        (ip >> 8) & 0xff,
        ip & 0xff,
        port);
}

void ClientGC::ClientRequestJoinServerData(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_ClientRequestJoinServerData request;
    if (!messageRead.ReadProtobuf(request))
    {
        Platform::Print("Parsing CMsgGCCStrike15_v2_ClientRequestJoinServerData failed, ignoring\n");
        return;
    }

    CMsgGCCStrike15_v2_ClientRequestJoinServerData response = request;

    if (m_lastMatchmakingReservation && !m_matchmakingServerAddress.empty())
    {
        CMsgGCCStrike15_v2_MatchmakingGC2ClientReserve *res = response.mutable_res();
        if (m_matchmakingServerId)
            res->set_serverid(m_matchmakingServerId);
        if (m_matchmakingDirectUdpIp)
            res->set_direct_udp_ip(m_matchmakingDirectUdpIp);
        res->set_direct_udp_port(m_matchmakingDirectUdpPort);
        res->set_reservationid(m_lastMatchmakingReservation);
        res->set_server_address(m_matchmakingServerAddress);
        if (!m_matchmakingMap.empty())
            res->set_map(m_matchmakingMap);

        Platform::Print(
            "matchmaking: 9164 returning active reserve %llu server=%s map=%s\n",
            m_lastMatchmakingReservation,
            m_matchmakingServerAddress.c_str(), m_matchmakingMap.c_str());
    }
    else
    {
        response.mutable_res()->set_serverid(request.version());
        response.mutable_res()->set_direct_udp_ip(request.server_ip());
        response.mutable_res()->set_direct_udp_port(request.server_port());
        response.mutable_res()->set_reservationid(GameServerCookieId);

        char addressString[32];
        AddressString(request.server_ip(), request.server_port(), addressString, sizeof(addressString));
        response.mutable_res()->set_server_address(addressString);
    }

    SendMessageToGame(false, k_EMsgGCCStrike15_v2_ClientRequestJoinServerData, response);
}

void ClientGC::PollOperationMissionSelectionBridge()
{
    for (const char *path : OperationMissionBridgePaths)
    {
        std::ifstream in(path);
        if (!in.is_open())
            continue;

        std::string line;
        std::string latest;
        while (std::getline(in, line))
        {
            if (line.find("REVIVAL_MISSION_SELECT_V1") != std::string::npos)
                latest = line;
        }
        in.close();

        // Consume first. If parsing fails, a stale malformed line cannot keep
        // reapplying forever on every SharedGC idle tick.
        std::remove(path);

        if (latest.empty())
            continue;

        const size_t marker = latest.find("REVIVAL_MISSION_SELECT_V1");
        unsigned season = 0;
        unsigned card = 0;
        unsigned quest = 0;
        if (std::sscanf(
                latest.c_str() + marker,
                "REVIVAL_MISSION_SELECT_V1 %u %u %u",
                &season, &card, &quest) != 3)
        {
            Platform::Print(
                "operation bridge: malformed selection line '%s'\n",
                latest.c_str());
            continue;
        }

        Platform::Print(
            "operation bridge: received Panorama selection season=%u card=%u quest=%u from %s\n",
            season, card, quest, path);

        CMsgSOMultipleObjects update;
        if (m_inventory.SetOperationMissionSelection(
                static_cast<uint32_t>(season),
                static_cast<uint32_t>(card),
                static_cast<uint32_t>(quest),
                update))
        {
            // Local client must receive both the SeasonalOperations state and
            // the modified Operation coin before matchmaking starts. Sending to
            // the server too is harmless and keeps an already-connected test
            // process coherent.
            SendMessageToGame(
                true, k_ESOMsg_UpdateMultiple, update);

            Platform::Print(
                "REVIVAL_OPERATION_SELECTION_BRIDGE_V2 applied season=%u card=%u quest=%u\n",
                season, card, quest);
        }
        else
        {
            Platform::Print(
                "REVIVAL_OPERATION_SELECTION_BRIDGE_V2 rejected season=%u card=%u quest=%u\n",
                season, card, quest);
        }

        // One click produces one selection. Do not let duplicate candidate
        // paths override the first successfully consumed event.
        return;
    }
}

void ClientGC::MatchmakingStart(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchmakingStart message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("matchmaking: failed to parse MatchmakingStart\n");
        return;
    }

    // The popup writes its local selection marker immediately before calling
    // LobbyAPI.StartMatchmaking. Consume it synchronously here as a final race
    // guard so mm_request.txt can never be created from the previous card.
    PollOperationMissionSelectionBridge();

    m_matchmakingGameType = message.has_game_type() ? message.game_type() : 8;
    m_matchmakingClientVersion = message.has_client_version() ? message.client_version() : 0;
    m_matchmakingActive = true;
    m_matchmakingIgnoreNextNonAbandonStop = true;
    m_lastMatchmakingReservation = 0;
    m_matchmakingIdleTicks = 0;
    m_matchmakingServerId = 0;
    m_matchmakingDirectUdpIp = 0;
    m_matchmakingDirectUdpPort = 0;
    m_matchmakingServerAddress.clear();
    m_matchmakingMap.clear();
    m_matchmakingFinalReserveSent = false;
    RevivalDisarmAcceptWatcher();

    std::ostringstream request;
    request << "action=start\n"
            << "steamid=" << m_steamId << "\n"
            << "account_id=" << AccountId() << "\n"
            << "game_type=" << m_matchmakingGameType << "\n"
            << "client_version=" << m_matchmakingClientVersion << "\n";

    const std::string operationMissionMap =
        m_inventory.PreferredOperationMissionMap();
    if (!operationMissionMap.empty())
    {
        request << "map=" << operationMissionMap << "\n";
        Platform::Print(
            "REVIVAL_REPEATABLE_MISSIONS_V1 targeting mission map %s\n",
            operationMissionMap.c_str());
    }

    if (!WriteMatchmakingBridgeFile(MatchmakingRequestPath, request.str()))
        Platform::Print("matchmaking: failed to write %s\n", MatchmakingRequestPath);

    CMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate update;
    update.set_matchmaking(1);
    update.add_waiting_account_id_sessions(AccountId());
    update.mutable_global_stats()->set_players_searching(1);
    update.mutable_global_stats()->set_servers_available(0);
    update.mutable_global_stats()->set_search_time_avg(5);
    SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate, update);

    Platform::Print("matchmaking: queued Competitive search through revival bridge (game_type=%u)\n",
        m_matchmakingGameType);
}

void ClientGC::MatchmakingStop(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchmakingStop message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("matchmaking: failed to parse MatchmakingStop\n");
        return;
    }

    const int abandon = message.has_abandon() ? message.abandon() : 0;

    // Legacy Panorama can emit one transient non-abandon stop immediately
    // after StartMatchmaking while it rebuilds the mmqueue session. Treating
    // that as a real cancel makes the queue disappear instantly.
    if (m_matchmakingActive
        && !m_lastMatchmakingReservation
        && abandon != 1
        && m_matchmakingIgnoreNextNonAbandonStop)
    {
        m_matchmakingIgnoreNextNonAbandonStop = false;
        Platform::Print(
            "REVIVAL_CLIENT_QUEUE_START_GUARD_V1 ignored transient startup stop abandon=%d\n",
            abandon);
        return;
    }

    // After 9107 the stock client can stop the SEARCH phase with abandon=0.
    // That must not cancel the already-reserved match. Only an explicit
    // abandon=1 (or a stop before any reservation exists) leaves the revival
    // coordinator queue/reservation.
    if (m_lastMatchmakingReservation && abandon != 1)
    {
        m_matchmakingActive = false;
        m_matchmakingIdleTicks = 0;
        Platform::Print(
            "matchmaking: search phase stopped after reserve; preserving reservation=%llu\n",
            m_lastMatchmakingReservation);
        return;
    }

    std::ostringstream request;
    request << "action=stop\n"
            << "steamid=" << m_steamId << "\n"
            << "account_id=" << AccountId() << "\n"
            << "abandon=" << abandon << "\n";
    WriteMatchmakingBridgeFile(MatchmakingRequestPath, request.str());

    m_matchmakingActive = false;
    m_matchmakingIgnoreNextNonAbandonStop = false;
    m_lastMatchmakingReservation = 0;
    m_matchmakingIdleTicks = 0;
    m_matchmakingServerId = 0;
    m_matchmakingDirectUdpIp = 0;
    m_matchmakingDirectUdpPort = 0;
    m_matchmakingServerAddress.clear();
    m_matchmakingMap.clear();
    m_matchmakingFinalReserveSent = false;
    RevivalDisarmAcceptWatcher();

    CMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate update;
    update.set_matchmaking(0);
    SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate, update);
    Platform::Print("matchmaking: search stopped abandon=%d\n", abandon);
}

void ClientGC::MatchmakingPing(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchmakingClient2ServerPing message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("matchmaking: failed to parse MatchmakingClient2ServerPing\n");
        return;
    }
    PollMatchmakingBridge();
}

void ClientGC::MatchmakingHello(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchmakingClient2GCHello hello;
    if (!messageRead.ReadProtobuf(hello))
        return;

    CMsgGCCStrike15_v2_MatchmakingGC2ClientHello response;
    BuildMatchmakingHello(response);
    SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientHello, response);
    if (m_matchmakingActive)
        PollMatchmakingBridge();
}


void ClientGC::SendMatchmakingConnectReserve()
{
    if (!m_lastMatchmakingReservation || m_matchmakingFinalReserveSent
        || m_matchmakingServerAddress.empty())
        return;

    CMsgGCCStrike15_v2_MatchmakingGC2ClientReserve reserve;
    reserve.set_serverid(m_matchmakingServerId ? m_matchmakingServerId : 1);
    if (m_matchmakingDirectUdpIp)
        reserve.set_direct_udp_ip(m_matchmakingDirectUdpIp);
    reserve.set_direct_udp_port(m_matchmakingDirectUdpPort);
    reserve.set_reservationid(m_lastMatchmakingReservation);
    if (!m_matchmakingMap.empty())
        reserve.set_map(m_matchmakingMap);
    reserve.set_server_address(m_matchmakingServerAddress);

    // Deliberately NO nested reservation here. The first 9107's Competitive
    // reservation created the stock stage-1 ready-up callback. Once the server
    // has answered stage 2 / awaiting 0, a second address+cookie 9107 switches
    // the retail client into its QueueConnect path instead of recreating the
    // Accept callback.
    SendMessageToGame(
        false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientReserve, reserve);
    m_matchmakingFinalReserveSent = true;

    Platform::Print(
        "matchmaking: ACCEPT COMPLETE; sent second 9107 for QueueConnect "
        "reservation=%llu server=%s map=%s\n",
        m_lastMatchmakingReservation, m_matchmakingServerAddress.c_str(),
        m_matchmakingMap.c_str());
}

void ClientGC::PollRewardBridge()
{
    std::ifstream in(MatchmakingRewardPath, std::ios::binary);
    if (!in.is_open())
        return;

    std::vector<uint8_t> payload(
        (std::istreambuf_iterator<char>(in)),
        std::istreambuf_iterator<char>());
    in.close();

    if (payload.empty() || payload.size() > 4u * 1024u * 1024u)
    {
        std::remove(MatchmakingRewardPath);
        Platform::Print(
            "REVIVAL_CLIENT_REWARD_BRIDGE_V1 discarded invalid reward payload (%zu bytes)\n",
            payload.size());
        return;
    }

    // Remove first so a crash/re-entry cannot apply the same local spool twice.
    std::remove(MatchmakingRewardPath);

    static const char BundleMagicV1[8] = { 'R','V','M','S','G','V','1','\0' };
    static const char BundleMagicV2[8] = { 'R','V','M','S','G','V','2','\0' };
    const bool bundleV1 = payload.size() >= 12
        && std::memcmp(payload.data(), BundleMagicV1, sizeof(BundleMagicV1)) == 0;
    const bool bundleV2 = payload.size() >= 76
        && std::memcmp(payload.data(), BundleMagicV2, sizeof(BundleMagicV2)) == 0;

    if (bundleV1 || bundleV2)
    {
        size_t offset = 8;
        uint32_t count = 0;

        if (bundleV2)
        {
            auto readU32 = [&payload, &offset](uint32_t &value) -> bool
            {
                if (offset + sizeof(value) > payload.size())
                    return false;
                std::memcpy(&value, payload.data() + offset, sizeof(value));
                offset += sizeof(value);
                return true;
            };
            auto readI32 = [&payload, &offset](int32_t &value) -> bool
            {
                if (offset + sizeof(value) > payload.size())
                    return false;
                std::memcpy(&value, payload.data() + offset, sizeof(value));
                offset += sizeof(value);
                return true;
            };

            uint64_t matchId = 0;
            if (offset + sizeof(matchId) > payload.size())
                return;
            std::memcpy(&matchId, payload.data() + offset, sizeof(matchId));
            offset += sizeof(matchId);

            uint32_t level = 0, xp = 0, profileWeek = 0, weeklyBaseXp = 0;
            uint32_t weeklyRewardClaimed = 0, casePlaytime = 0, caseDrops = 0;
            uint32_t nextCaseDrop = 0, rank = 0, wins = 0, matches = 0;
            uint32_t awardedXp = 0, levelsGained = 0;
            int32_t rating = 0;

            if (!readU32(level) || !readU32(xp)
                || !readU32(profileWeek) || !readU32(weeklyBaseXp)
                || !readU32(weeklyRewardClaimed)
                || !readU32(casePlaytime) || !readU32(caseDrops)
                || !readU32(nextCaseDrop) || !readU32(rank)
                || !readU32(wins) || !readI32(rating)
                || !readU32(matches) || !readU32(awardedXp)
                || !readU32(levelsGained) || !readU32(count))
            {
                Platform::Print("REVIVAL_PROGRESS_BUNDLE_V2 truncated profile header\n");
                return;
            }

            const uint32_t oldLevel = m_inventory.ProfileLevel();
            const uint32_t oldXp = m_inventory.ProfileXp();
            const uint32_t oldProfileWeek = m_inventory.ProfileWeek();
            const uint32_t oldWeeklyBaseXp = m_inventory.WeeklyBaseXp();
            const uint32_t oldRank =
                static_cast<uint32_t>(m_inventory.CompetitiveRank());

            if (!m_inventory.ImportRevivalProfile(
                    level, xp, profileWeek, weeklyBaseXp,
                    weeklyRewardClaimed != 0,
                    casePlaytime, caseDrops, nextCaseDrop,
                    static_cast<RankId>(rank), wins, rating, matches))
            {
                return;
            }

            if (matchId != m_lastUiDispatchedMatchId)
            {
                std::vector<uint8_t> rankUiMessage;
                std::vector<uint8_t> xpUiMessage;
                if (RevivalBuildEndMatchUiMessages(
                        AccountId(),
                        oldRank, rank, wins,
                        oldLevel, oldXp,
                        oldProfileWeek, oldWeeklyBaseXp,
                        profileWeek, weeklyBaseXp,
                        awardedXp,
                        rankUiMessage, xpUiMessage))
                {
                    PostToHost(
                        HostEvent::ClientUserMessage,
                        RevivalUserMsgServerRankUpdate,
                        rankUiMessage.data(),
                        static_cast<uint32_t>(rankUiMessage.size()));
                    PostToHost(
                        HostEvent::ClientUserMessage,
                        RevivalUserMsgXpUpdate,
                        xpUiMessage.data(),
                        static_cast<uint32_t>(xpUiMessage.size()));

                    std::vector<uint8_t> revealUiMessage;
                    revealUiMessage.push_back(0x08); // seconds_till_shutdown = 1
                    RevivalAppendVarint(revealUiMessage, 60);
                    PostToHost(
                        HostEvent::ClientUserMessage,
                        RevivalUserMsgServerRankRevealAll,
                        revealUiMessage.data(),
                        static_cast<uint32_t>(revealUiMessage.size()));

                    m_lastUiDispatchedMatchId = matchId;
                    Platform::Print(
                        "REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V3 late fallback queued stock 52+65+50\n");
                }
            }

            CMsgSOMultipleObjects profileUpdate;
            m_inventory.BuildProfilePersonaUpdate(profileUpdate);
            SendMessageToGame(true, k_ESOMsg_UpdateMultiple, profileUpdate);

            CMsgGCCStrike15_v2_MatchmakingGC2ClientHello profileHello;
            BuildMatchmakingHello(profileHello);
            SendMessageToGame(
                false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientHello,
                profileHello);
            SendRankUpdate();

            if (matchId)
            {
                m_lastRewardedMatchId = matchId;
                if (matchId == m_revivalAuthoritativeMatchId)
                    m_revivalAuthoritativeMatchId = 0;
            }

            Platform::Print(
                "REVIVAL_PROGRESS_BUNDLE_V2 applied match=%llu awarded_xp=%u levels=%u level=%u xp=%u rank=%u wins=%u\n",
                static_cast<unsigned long long>(matchId), awardedXp,
                levelsGained, level, xp, rank, wins);
        }
        else
        {
            std::memcpy(&count, payload.data() + offset, sizeof(count));
            offset += sizeof(count);
        }

        if (count > 64)
        {
            Platform::Print(
                "REVIVAL_NATIVE_DROP_BUNDLE_V1 invalid message count=%u\n", count);
            return;
        }

        uint32_t delivered = 0;
        for (uint32_t i = 0; i < count; ++i)
        {
            if (offset + 8 > payload.size())
                break;

            uint32_t type = 0;
            uint32_t size = 0;
            std::memcpy(&type, payload.data() + offset, sizeof(type));
            offset += sizeof(type);
            std::memcpy(&size, payload.data() + offset, sizeof(size));
            offset += sizeof(size);

            if (!size || size > 1024u * 1024u || offset + size > payload.size())
                break;

            const void *messageData = payload.data() + offset;

            GCMessageRead read{ type, messageData, size };
            if (read.IsValid() && read.IsProtobuf()
                && read.TypeUnmasked() == k_ESOMsg_Create)
            {
                CMsgSOSingleObject create;
                if (read.ReadProtobuf(create))
                    m_inventory.ImportServerCreatedItem(create);
            }

            PostToHost(HostEvent::Message, type, messageData, size);
            offset += size;
            ++delivered;
        }

        Platform::Print(
            "REVIVAL_NATIVE_DROP_BUNDLE_V1 delivered %u/%u exact server messages\n",
            delivered, count);
        return;
    }

    Platform::Print(
        "REVIVAL_CLIENT_REWARD_BRIDGE_V1 delivering server 9136 bridge (%zu bytes)\n",
        payload.size());
    HandleNetMessage(payload.data(), static_cast<uint32_t>(payload.size()));
}

void ClientGC::ProcessCompletedMatchBridge(
    const std::unordered_map<std::string, std::string> &state)
{
    const uint64_t matchId = BridgeU64(state, "last_match_id", 0);
    if (!matchId)
        return;

    auto reasonIt = state.find("result_reason");
    if (reasonIt == state.end() || reasonIt->second != "game_over")
        return;

    const uint32_t roundsWon = static_cast<uint32_t>(
        std::min<uint64_t>(BridgeU64(state, "result_rounds_won", 0), 30));
    const uint32_t timePlayed = static_cast<uint32_t>(
        std::min<uint64_t>(BridgeU64(state, "result_time_played", 0), UINT32_MAX));
    const bool won = BridgeU64(state, "result_won", 0) != 0;
    const bool tied = BridgeU64(state, "result_tied", 0) != 0;

    // Mission progression has its own exactly-once guard. The authoritative
    // server reward bundle can arrive before this coordinator state; tying
    // missions to m_lastRewardedMatchId made that ordering randomly skip stars.
    if (matchId != m_lastMissionProgressMatchId
        && matchId != m_lastOperationMissionMatchId)
    {
        auto mapIt = state.find("last_map");
        const std::string completedMap =
            (mapIt != state.end() && !mapIt->second.empty())
                ? mapIt->second
                : m_matchmakingMap;

        CMsgSOMultipleObjects operationUpdate;
        if (m_inventory.ApplySelectedOperationCompetitiveMission(
                completedMap, roundsWon, won, operationUpdate))
        {
            SendMessageToGame(true, k_ESOMsg_UpdateMultiple, operationUpdate);

            CMsgGCCStrike15_v2_MatchmakingGC2ClientHello operationHello;
            BuildMatchmakingHello(operationHello);
            SendMessageToGame(
                false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientHello,
                operationHello);

            Platform::Print(
                "REVIVAL_REPEATABLE_MISSIONS_V5 applied end-match Operation update map=%s match=%llu\n",
                completedMap.c_str(),
                static_cast<unsigned long long>(matchId));
        }

        m_lastMissionProgressMatchId = matchId;
        m_lastOperationMissionMatchId = matchId;
    }

    // If the server bundle already applied XP/rank/items, only the independent
    // mission step above was still needed.
    if (matchId == m_lastRewardedMatchId)
        return;

    Platform::Print(
        "REVIVAL_SYNTHETIC_MATCH_END_V1 match=%llu rounds=%u time=%u won=%u tied=%u\n",
        matchId, roundsWon, timePlayed, won ? 1u : 0u, tied ? 1u : 0u);

    // Snapshot the PRE-MATCH values before applying the synthetic result.
    // The stock XP/rank end-screen usermessages must describe old -> new state.
    const uint32_t uiOldLevel = m_inventory.ProfileLevel();
    const uint32_t uiOldXp = m_inventory.ProfileXp();
    const uint32_t uiOldProfileWeek = m_inventory.ProfileWeek();
    const uint32_t uiOldWeeklyBaseXp = m_inventory.WeeklyBaseXp();
    const uint32_t uiOldRank =
        static_cast<uint32_t>(m_inventory.CompetitiveRank());

    // Legacy Competitive XP is driven primarily by rounds won. Use the same
    // 30-XP-per-round base that the real 9136 handler derives.
    const uint32_t baseXp = roundsWon * 30u;
    uint32_t levelsGained = 0;
    const uint32_t awardedXp =
        m_inventory.ApplyWeeklyProfileXp(baseXp, &levelsGained);

    if (awardedXp)
    {
        CMsgSOMultipleObjects profileUpdate;
        m_inventory.BuildProfilePersonaUpdate(profileUpdate);
        SendMessageToGame(true, k_ESOMsg_UpdateMultiple, profileUpdate);

        CMsgGCCStrike15_v2_MatchmakingGC2ClientHello profileHello;
        BuildMatchmakingHello(profileHello);
        SendMessageToGame(false,
            k_EMsgGCCStrike15_v2_MatchmakingGC2ClientHello, profileHello);
    }

    // Item drops are generated exactly once by the dedicated server so the
    // inventory item IDs match the previews recorded in CCSGameRules and shown
    // by the native scoreboard reveal. This bridge remains responsible only
    // for profile XP / Competitive result state when the public DS omits 9136.

    if (m_inventory.ApplyCompetitiveMatchResult(won, tied))
        SendRankUpdate();

    // Populate the stock Panorama end-match buffers BEFORE EndOfMatch_Show.
    // Waiting for the later reward bundle was too late: the item-drop reveal
    // had already started and the XP/skillgroup panels stayed hidden.
    if (matchId != m_lastUiDispatchedMatchId)
    {
        std::vector<uint8_t> rankUiMessage;
        std::vector<uint8_t> xpUiMessage;
        if (RevivalBuildEndMatchUiMessages(
                AccountId(),
                uiOldRank,
                static_cast<uint32_t>(m_inventory.CompetitiveRank()),
                m_inventory.CompetitiveWins(),
                uiOldLevel, uiOldXp,
                uiOldProfileWeek, uiOldWeeklyBaseXp,
                m_inventory.ProfileWeek(), m_inventory.WeeklyBaseXp(),
                awardedXp,
                rankUiMessage, xpUiMessage))
        {
            PostToHost(
                HostEvent::ClientUserMessage,
                RevivalUserMsgServerRankUpdate,
                rankUiMessage.data(),
                static_cast<uint32_t>(rankUiMessage.size()));
            PostToHost(
                HostEvent::ClientUserMessage,
                RevivalUserMsgXpUpdate,
                xpUiMessage.data(),
                static_cast<uint32_t>(xpUiMessage.size()));

            std::vector<uint8_t> revealUiMessage;
            revealUiMessage.push_back(0x08); // seconds_till_shutdown = 1
            RevivalAppendVarint(revealUiMessage, 60);
            PostToHost(
                HostEvent::ClientUserMessage,
                RevivalUserMsgServerRankRevealAll,
                revealUiMessage.data(),
                static_cast<uint32_t>(revealUiMessage.size()));

            m_lastUiDispatchedMatchId = matchId;
            Platform::Print(
                "REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V3 early queued stock 52+65+50 "
                "match=%llu\n",
                static_cast<unsigned long long>(matchId));
        }
    }

    // Mark the backend-completed match as consumed before the next 500 ms poll.
    // A late genuine 9136 carrying the same match id will then be deduplicated.
    m_lastRewardedMatchId = matchId;

    Platform::Print(
        "REVIVAL_MATCH_RESULT_FALLBACK_V2 complete match=%llu xp=%u\n",
        matchId, awardedXp);
}


void ClientGC::PollMatchmakingBridge()
{
    const auto state = ReadMatchmakingBridgeFile(MatchmakingStatePath);
    if (state.empty())
        return;

    auto stateIt = state.find("state");
    const std::string phase = stateIt == state.end() ? std::string{} : stateIt->second;

    if (phase == "searching" || phase == "allocating")
    {
        m_matchmakingActive = true;
        CMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate update;
        update.set_matchmaking(1);

        std::vector<uint32_t> waiting = BridgeU32List(state, "waiting_account_ids");
        if (waiting.empty())
            waiting.push_back(AccountId());
        for (uint32_t accountId : waiting)
            update.add_waiting_account_id_sessions(accountId);

        update.mutable_global_stats()->set_players_searching(
            static_cast<uint32_t>(BridgeU64(state, "players_searching", waiting.size())));
        const bool serverOnline = BridgeU64(state, "server_online", 0) != 0;
        const bool serverAvailable = BridgeU64(
            state, "server_available", serverOnline ? 1 : 0) != 0;
        update.mutable_global_stats()->set_servers_online(serverOnline ? 1 : 0);
        update.mutable_global_stats()->set_servers_available(serverAvailable ? 1 : 0);
        update.mutable_global_stats()->set_search_time_avg(1);
        SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate, update);
        return;
    }

    if (phase == "reserved" || phase == "in_match")
    {
        const uint64_t reservationId = BridgeU64(state, "reservation_id", 0);
        const uint64_t matchId = BridgeU64(state, "match_id", reservationId);
        if (!reservationId || reservationId == m_lastMatchmakingReservation)
            return;

        auto addressIt = state.find("server_address");
        auto mapIt = state.find("map");
        const std::string serverAddress =
            addressIt == state.end() ? std::string{} : addressIt->second;
        const std::string mapName =
            mapIt == state.end() ? std::string{"de_dust2"} : mapIt->second;
        const uint32_t port = static_cast<uint32_t>(BridgeU64(state, "public_port", 27015));
        const uint32_t gameType = static_cast<uint32_t>(
            BridgeU64(state, "game_type", m_matchmakingGameType));

        if (serverAddress.empty())
            return;

        const uint64_t reportedServerId = BridgeU64(state, "server_id", 0);
        const uint32_t directUdpIp = static_cast<uint32_t>(
            BridgeU64(state, "direct_udp_ip", 0));

        // Match the retail-tested Accept flow: if the community server has no
        // real Steam gameserver identity, use the intentionally inert serverid
        // 1 and let direct_udp_ip/direct_udp_port own transport. Using the
        // reservation cookie as serverid can make the retail client attempt a
        // Steam-server route and never emit its 0x21 reserve check to Playit.
        const uint64_t serverId = reportedServerId ? reportedServerId : 1;
        const std::string numericServerAddress =
            directUdpIp ? RevivalNumericServerAddress(directUdpIp, port)
                        : serverAddress;

        CMsgGCCStrike15_v2_MatchmakingGC2ClientReserve reserve;
        reserve.set_serverid(serverId);
        if (directUdpIp)
            reserve.set_direct_udp_ip(directUdpIp);
        reserve.set_direct_udp_port(port);
        reserve.set_reservationid(reservationId);
        reserve.set_map(mapName);
        reserve.set_server_address(numericServerAddress);

        CMsgGCCStrike15_v2_MatchmakingGC2ServerReserve *details =
            reserve.mutable_reservation();
        std::vector<uint32_t> accountIds = BridgeU32List(state, "account_ids");
        if (accountIds.empty())
            accountIds.push_back(AccountId());
        for (uint32_t accountId : accountIds)
            details->add_account_ids(accountId);
        details->set_game_type(gameType);
        details->set_match_id(matchId);
        const uint32_t serverVersion = static_cast<uint32_t>(
            BridgeU64(state, "server_version", 0));
        if (serverVersion)
            details->set_server_version(serverVersion);

        // The stock in-game mission HUD does NOT read
        // SeasonalOperations.mission_id. GameStateAPI.GetActiveQuestID() is
        // backed by the Operation coin's native "quest id" attribute. Resolve
        // the selected card against the actual allocated map and publish that
        // item update before the client enters the match-ready/connect flow.
        const uint32_t operationQuestId =
            m_inventory.PreferredOperationMissionQuest(mapName);
        if (operationQuestId)
        {
            CMsgSOMultipleObjects questUpdate;
            if (m_inventory.SetOperationActiveQuest(
                    operationQuestId, questUpdate))
            {
                SendMessageToGame(
                    true, k_ESOMsg_UpdateMultiple, questUpdate);
                Platform::Print(
                    "REVIVAL_NATIVE_ACTIVE_QUEST_V1 map=%s quest=%u published before reserve\n",
                    mapName.c_str(), operationQuestId);
            }
        }
        else
        {
            Platform::Print(
                "REVIVAL_NATIVE_ACTIVE_QUEST_V1 map=%s no matching selected quest\n",
                mapName.c_str());
        }

        // 9107 itself is the transition into the stock match-ready flow.
        // Do NOT immediately follow it with 9104 matchmaking=0: that cancels
        // the UI/search state in the same tick and suppresses the green ACCEPT
        // panel before the client can create its game/mmqueue session.
        SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientReserve, reserve);

        m_lastMatchmakingReservation = reservationId;
        m_revivalAuthoritativeMatchId = matchId;
        m_matchmakingIgnoreNextNonAbandonStop = false;
        m_matchmakingServerId = serverId;
        m_matchmakingDirectUdpIp = directUdpIp;
        m_matchmakingDirectUdpPort = port;
        m_matchmakingServerAddress = numericServerAddress;
        m_matchmakingMap = mapName;
        m_matchmakingFinalReserveSent = false;
        RevivalArmAcceptWatcher(
            directUdpIp, static_cast<uint16_t>(port),
            static_cast<uint32_t>(accountIds.size()));
        Platform::Print(
            "matchmaking: MATCH FOUND reservation=%llu gameserver=%llu route=%s map=%s server=%s game_type=%u version=%u\n",
            reservationId, serverId, reportedServerId ? "steamid+direct" : "serverid-1+direct-udp",
            mapName.c_str(), numericServerAddress.c_str(), gameType, serverVersion);
        return;
    }

    if (phase == "idle")
    {
        ProcessCompletedMatchBridge(state);
        if (m_matchmakingActive)
        {
            CMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate update;
            update.set_matchmaking(0);
            SendMessageToGame(false, k_EMsgGCCStrike15_v2_MatchmakingGC2ClientUpdate, update);
        }
        m_matchmakingActive = false;
        m_lastMatchmakingReservation = 0;
        m_matchmakingIdleTicks = 0;
    }
}

void ClientGC::ClientRequestNewMission(GCMessageRead &messageRead)
{
    CMsgGCCstrike15_v2_ClientRequestNewMission message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print(
            "Parsing CMsgGCCstrike15_v2_ClientRequestNewMission failed, ignoring\n");
        return;
    }

    Platform::Print(
        "REVIVAL_NATIVE_MISSION_REQUEST_V2 has_mission=%d mission=%u "
        "has_campaign=%d campaign=%u\n",
        message.has_mission_id() ? 1 : 0,
        message.has_mission_id() ? message.mission_id() : 0,
        message.has_campaign_id() ? 1 : 0,
        message.has_campaign_id() ? message.campaign_id() : 0);

    if (!message.has_mission_id())
    {
        Platform::Print(
            "operation: ClientRequestNewMission missing mission id\n");
        return;
    }

    // Some Riptide-era/native client paths omit campaign_id or expose the
    // access index instead of the SeasonalOperations key. Normalize both.
    const uint32_t requestedCampaign = message.has_campaign_id()
        ? message.campaign_id()
        : GetConfig().OperationSeason();
    const uint32_t operationSeason = GetConfig().OperationSeason();

    if (requestedCampaign != operationSeason && requestedCampaign != 1)
    {
        Platform::Print(
            "operation: refused mission %u for campaign/access %u (active season %u)\n",
            message.mission_id(), requestedCampaign, operationSeason);
        return;
    }

    CMsgSOMultipleObjects update;
    if (m_inventory.SetOperationMissionCard(
            operationSeason, message.mission_id(), update))
    {
        Platform::Print(
            "REVIVAL_OPERATION_MISSION_ACTIVATION_V2 campaign/access=%u -> "
            "season=%u mission_field=%u\n",
            requestedCampaign, operationSeason, message.mission_id());

        SendMessageToGame(
            true, k_ESOMsg_UpdateMultiple, update);
    }
}


static ShopReward RiptideNativeReward(uint32_t redeemId)
{
    switch (redeemId)
    {
    case 3161140792u: return { 4795, 2 };   // crate_patch_pack03
    case 486239559u:  return { 4783, 1 };   // crate_sticker_pack_op_riptide_capsule
    case 3394577585u: return { 4779, 1 };   // crate_sticker_pack_riptide_surfshop
    case 766835476u:  return { 4790, 2 };   // crate_community_29
    case 4262277037u: return { 4788, 100 }; // Train Covert
    case 2179775250u: return { 4787, 20 };  // Train Classified
    case 1690286011u: return { 4786, 4 };   // Train Restricted
    case 3750698461u: return { 4785, 1 };   // Train Mil-Spec
    case 3820610303u: return { 4794, 4 };   // Mirage 2021
    case 205604202u:  return { 4793, 4 };   // Dust II 2021
    case 1519885759u: return { 4792, 4 };   // Vertigo 2021
    case 3278305639u: return { 4769, 25 };  // CT Master Agents
    case 1517267165u: return { 4770, 25 };  // T Master Agents
    case 3736209238u: return { 4768, 10 };  // Superior Agents
    case 1969402445u: return { 4767, 7 };   // Exceptional Agents
    case 2249793569u: return { 4766, 5 };   // Distinguished Agents
    default:          return { 0, 0 };
    }
}

void ClientGC::ClientRedeemMissionReward(GCMessageRead &messageRead)
{
    Platform::Print("operation shop: received native redeem request\n");
    CMsgGCCstrike15_v2_ClientRedeemMissionReward message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgGCCstrike15_v2_ClientRedeemMissionReward failed, ignoring\n");
        return;
    }

    if (!message.has_campaign_id() || !message.has_redeem_id())
    {
        Platform::Print("operation shop: native redeem missing campaign/redeem id\n");
        return;
    }

    // Riptide Panorama addresses the active operation through season_access=1,
    // while the SeasonalOperations SO/campaign itself is season 10. Depending
    // on the client build, the native redeem request can expose either value.
    if (message.campaign_id() != GetConfig().OperationSeason()
        && message.campaign_id() != 1)
    {
        Platform::Print("operation shop: refused campaign/access %u (active season %u)\n",
            message.campaign_id(), GetConfig().OperationSeason());
        return;
    }

    Platform::Print("operation shop: native redeem campaign/access=%u redeem=%u balance=%u expected_cost=%u\n",
        message.campaign_id(),
        message.redeem_id(),
        message.has_redeemable_balance() ? message.redeemable_balance() : 0,
        message.has_expected_cost() ? message.expected_cost() : 0);

    const ShopReward reward = RiptideNativeReward(message.redeem_id());
    if (!reward.defIndex || reward.cost <= 0)
    {
        Platform::Print("operation shop: refused unknown redeem id %u [native-map-v3]\n",
            message.redeem_id());
        return;
    }

    Platform::Print("operation shop: native-map-v3 resolved %u -> def %u cost %d\n",
        message.redeem_id(), reward.defIndex, reward.cost);

    // The Legacy client derives expected_cost from its bundled Operation schema.
    // The revival UI/shop table is server-owned, so never trust or require that
    // client hint to match. The configured reward cost below is authoritative.
    if (message.has_expected_cost()
        && message.expected_cost() != static_cast<uint32_t>(reward.cost))
    {
        Platform::Print("operation shop: client expected cost %u for redeem id %u; using server cost %d\n",
            message.expected_cost(), message.redeem_id(), reward.cost);
    }

    if (!m_inventory.CanSpendStars(reward.cost))
    {
        Platform::Print("operation shop: refused redeem id %u - not enough stars (need %d)\n",
            message.redeem_id(), reward.cost);
        return;
    }

    std::vector<CMsgSOSingleObject> created;
    const uint64_t itemId = m_inventory.PurchaseOperationReward(reward.defIndex, created);
    if (!itemId || created.empty())
    {
        Platform::Print("operation shop: native redeem failed for def %u\n", reward.defIndex);
        return;
    }

    CMsgSOMultipleObjects coinUpdate;
    if (!m_inventory.SpendStars(reward.cost, coinUpdate))
    {
        CMsgSOSingleObject rollback;
        m_inventory.RemoveItem(itemId, rollback);
        Platform::Print("operation shop: native redeem rolled back def %u - wallet changed\n",
            reward.defIndex);
        return;
    }

    for (CMsgSOSingleObject &newItem : created)
    {
        SendMessageToGame(true, k_ESOMsg_Create, newItem);
    }
    SendMessageToGame(true, k_ESOMsg_UpdateMultiple, coinUpdate);

    CMsgGCItemCustomizationNotification notification;
    notification.add_item_id(itemId);
    notification.set_request(k_EGCItemCustomizationNotification_ClientRedeemMissionReward);
    SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);

    Platform::Print("operation shop: redeemed id %u -> def %u item %llu for %d stars\n",
        message.redeem_id(), reward.defIndex, itemId, reward.cost);
}


void ClientGC::SetItemPositions(GCMessageRead &messageRead)
{
    CMsgSetItemPositions message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgSetItemPositions failed, ignoring\n");
        return;
    }

    std::vector<CMsgItemAcknowledged> acknowledgements;
    acknowledgements.reserve(message.item_positions_size());

    CMsgSOMultipleObjects update;
    if (m_inventory.SetItemPositions(message, acknowledgements, update))
    {
        for (const CMsgItemAcknowledged &acknowledgement : acknowledgements)
        {
            // send these to the server only
            GCMessageWrite messageWrite{ k_EMsgGCItemAcknowledged, acknowledgement };
            PostToHost(HostEvent::NetMessage, 0, messageWrite.Data(), messageWrite.Size());
        }

        SendMessageToGame(true, k_ESOMsg_UpdateMultiple, update);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::IncrementKillCountAttribute(GCMessageRead &messageRead)
{
    CMsgIncrementKillCountAttribute message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgIncrementKillCountAttribute failed, ignoring\n");
        return;
    }

    assert(message.event_type() == 0);

    CMsgSOSingleObject update;
    if (m_inventory.IncrementKillCountAttribute(message.item_id(), message.amount(), update))
    {
        SendMessageToGame(true, k_ESOMsg_Update, update);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::ApplySticker(GCMessageRead &messageRead)
{
    CMsgApplySticker message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgApplySticker failed, ignoring\n");
        return;
    }

    assert(!message.item_item_id() != !message.baseitem_defidx());

    CMsgSOSingleObject update, destroy;
    CMsgGCItemCustomizationNotification notification;

    if (!message.sticker_item_id())
    {
        // scrape
        if (m_inventory.ScrapeSticker(message, update, destroy, notification))
        {
            if (destroy.has_type_id())
            {
                // destroying a default item
                SendMessageToGame(true, k_ESOMsg_Destroy, destroy);
            }

            if (update.has_type_id())
            {
                // if the item got removed (handled above), nothing gets updated
                SendMessageToGame(true, k_ESOMsg_Update, update);
            }

            if (notification.has_request())
            {
                // might get a k_EGCItemCustomizationNotification_RemoveSticker
                SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
            }
        }
        else
        {
            assert(false);
        }
    }
    else if (m_inventory.ApplySticker(message, update, destroy, notification))
    {
        SendMessageToGame(true, k_ESOMsg_Destroy, destroy);
        SendMessageToGame(true, k_ESOMsg_Update, update);

        SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::StoreGetUserData(GCMessageRead &messageRead)
{
    CMsgStoreGetUserData message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgStoreGetUserData failed, ignoring\n");
        return;
    }

    KeyValue priceSheet{ "price_sheet" };
    if (!priceSheet.ParseFromFile("csgo_gc/price_sheet.txt"))
    {
        return;
    }

    std::string binaryString;
    binaryString.reserve(1 << 17);
    priceSheet.BinaryWriteToString(binaryString);

    // fuck you idiot
    CMsgStoreGetUserDataResponse response;
    response.set_result(1);
    response.set_price_sheet_version(1729); // what
    *response.mutable_price_sheet() = std::move(binaryString);

    SendMessageToGame(false, k_EMsgGCStoreGetUserDataResponse, response);
}

void ClientGC::StorePurchaseInit(GCMessageRead &messageRead)
{
    CMsgGCStorePurchaseInit message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgGCStorePurchaseInit failed, ignoring\n");
        return;
    }

    // value doesn't matter
    uint64_t transactionId = Random{}.Integer<uint64_t>();

    assert(!m_transactionId);
    m_transactionId = transactionId;
    m_transactionItemIds.reserve(message.line_items_size()); // rough approx

    // inventory update response
    std::vector<CMsgSOSingleObject> inventoryUpdate;

    // operation shop (revival): star deduction for the player's coin
    CMsgSOMultipleObjects coinUpdate;
    bool coinChanged = false;

    for (const auto &item : message.line_items())
    {
        for (uint32_t i = 0; i < item.quantity(); i++)
        {
            const int starCost = GetConfig().OperationShopCost(item.item_def_id());

            // Validate affordability before allocating a reward, but do not mutate
            // the wallet yet. The old order deducted first, so an invalid item def
            // could eat stars without granting anything.
            if (starCost > 0 && !m_inventory.CanSpendStars(starCost))
            {
                Platform::Print("operation shop: refused def %u - not enough stars (need %d)\n",
                    item.item_def_id(), starCost);
                continue;
            }

            const size_t updateCountBefore = inventoryUpdate.size();
            uint64_t itemId = starCost > 0
                ? m_inventory.PurchaseOperationReward(item.item_def_id(), inventoryUpdate)
                : m_inventory.PurchaseItem(item.item_def_id(), inventoryUpdate);
            if (!itemId)
            {
                Platform::Print("store: purchase failed for def %u\n", item.item_def_id());
                continue;
            }

            if (starCost > 0)
            {
                if (!m_inventory.SpendStars(starCost, coinUpdate))
                {
                    // This should be unreachable because the wallet was checked
                    // immediately above and this GC is single-owner/synchronous.
                    // Roll the reward back instead of ever granting it for free.
                    CMsgSOSingleObject rollback;
                    m_inventory.RemoveItem(itemId, rollback);
                    inventoryUpdate.resize(updateCountBefore);
                    Platform::Print("operation shop: rolled back def %u - wallet changed during purchase\n",
                        item.item_def_id());
                    continue;
                }
                coinChanged = true;
            }

            m_transactionItemIds.push_back(itemId);
        }
    }

    // operation shop (revival): push the coin's new star balance to the game
    if (coinChanged)
    {
        SendMessageToGame(true, k_ESOMsg_UpdateMultiple, coinUpdate);
    }

    char url[128]; // url doesn't matter, but it needs to be set
    snprintf(url, sizeof(url), "https://checkout.steampowered.com/checkout/approvetxn/%llu/?returnurl=steam", transactionId);

    CMsgGCStorePurchaseInitResponse response;
    response.set_result(1); // success
    response.set_txn_id(transactionId);
    response.set_url(url);
    response.mutable_item_ids()->Assign(m_transactionItemIds.begin(), m_transactionItemIds.end());

    SendMessageToGame(false, k_EMsgGCStorePurchaseInitResponse, response, messageRead.JobId());

    // FIXME: why would the server care???
    for (auto &newItem : inventoryUpdate)
    {
        SendMessageToGame(true, k_ESOMsg_Create, newItem);
    }

    // this will run the steam callback
    PostToHost(HostEvent::MicroTransactionResponse, 0, nullptr, 0);
}

void ClientGC::StorePurchaseFinalize(GCMessageRead &messageRead)
{
    CMsgGCStorePurchaseFinalize message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgGCStorePurchaseFinalize failed, ignoring\n");
        return;
    }

    assert(m_transactionId);

    CMsgGCStorePurchaseFinalizeResponse response;
    response.set_result(1); // success
    response.mutable_item_ids()->Assign(m_transactionItemIds.begin(), m_transactionItemIds.end());
    SendMessageToGame(false, k_EMsgGCStorePurchaseFinalizeResponse, response, messageRead.JobId());

    // done with this one
    m_transactionId = 0;
}

void ClientGC::DeleteItem(GCMessageRead &messageRead)
{
    // there is data after this, but i don't know what it is
    uint64_t itemId = messageRead.ReadUint64();
    if (!messageRead.IsValid())
    {
        Platform::Print("Parsing CMsgGCDelete failed, ignoring\n");
        return;
    }

    CMsgSOSingleObject destroyed;
    if (m_inventory.RemoveItem(itemId, destroyed))
    {
        // mikkotodo what does the server want to know
        SendMessageToGame(true, k_ESOMsg_Destroy, destroyed);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::UnlockCrate(GCMessageRead &messageRead)
{
    uint64_t keyId = messageRead.ReadUint64();
    uint64_t crateId = messageRead.ReadUint64();
    if (!messageRead.IsValid())
    {
        Platform::Print("Parsing CMsgGCUnlockCrate failed, ignoring\n");
        return;
    }

    // trade-up contracts (revival addition): if this is the configured Gold
    // Trade-Up crate, convert 5 Coverts to a gold instead of a normal roll.
    uint32_t goldCrate = GetConfig().GoldTradeUpCrate();
    if (goldCrate && m_inventory.ItemDefIndex(crateId) == goldCrate)
    {
        Platform::Print("GOLD TRADE-UP crate %llu\n", crateId);

        std::vector<CMsgSOSingleObject> destroyed;
        CMsgSOSingleObject newItem;
        CMsgGCItemCustomizationNotification notification;

        if (m_inventory.UnlockCrateGoldTradeUp(crateId, keyId, destroyed, newItem, notification))
        {
            for (CMsgSOSingleObject &destroy : destroyed)
            {
                SendMessageToGame(true, k_ESOMsg_Destroy, destroy);
            }

            SendMessageToGame(true, k_ESOMsg_Create, newItem);
            SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
        }

        return;
    }

    // "gold only" case (revival addition): if this is the configured gold-only
    // crate ("Kinkerm's Case"), always roll a random gold - no Coverts consumed.
    uint32_t goldOnlyCrate = GetConfig().GoldOnlyCrate();
    if (goldOnlyCrate && m_inventory.ItemDefIndex(crateId) == goldOnlyCrate)
    {
        Platform::Print("GOLD-ONLY case %llu with %llu\n", crateId, keyId);

        CMsgSOSingleObject destroyCrate, destroyKey, newItem;
        CMsgGCItemCustomizationNotification notification;

        if (m_inventory.UnlockGoldOnlyCase(crateId, keyId, destroyCrate, destroyKey, newItem, notification))
        {
            // only send destroys that were actually populated (crate/key are
            // consumed only when destroy_used_items is enabled)
            if (destroyCrate.has_type_id())
            {
                SendMessageToGame(true, k_ESOMsg_Destroy, destroyCrate);
            }

            if (destroyKey.has_type_id())
            {
                SendMessageToGame(true, k_ESOMsg_Destroy, destroyKey);
            }

            SendMessageToGame(true, k_ESOMsg_Create, newItem);
            SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
        }

        return;
    }

    Platform::Print("CASE OPENING %llu with %llu\n", crateId, keyId);

    CMsgSOSingleObject destroyCrate, destroyKey, newItem;
    CMsgGCItemCustomizationNotification notification;

    if (m_inventory.UnlockCrate(
            crateId,
            keyId,
            destroyCrate,
            destroyKey,
            newItem,
            notification))
    {
        // mikkotodo what does the server want to know
        SendMessageToGame(true, k_ESOMsg_Destroy, destroyCrate);
        SendMessageToGame(true, k_ESOMsg_Destroy, destroyKey);
        SendMessageToGame(true, k_ESOMsg_Create, newItem);

        SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::NameItem(GCMessageRead &messageRead)
{
    uint64_t nameTagId = messageRead.ReadUint64();
    uint64_t itemId = messageRead.ReadUint64();
    messageRead.ReadData(1); // skip the sentinel
    std::string_view name = messageRead.ReadString();

    if (!messageRead.IsValid())
    {
        Platform::Print("Parsing CMsgGCNameItem failed, ignoring\n");
        return;
    }

    CMsgSOSingleObject update, destroy;
    CMsgGCItemCustomizationNotification notification;
    if (m_inventory.NameItem(nameTagId, itemId, name, update, destroy, notification))
    {
        SendMessageToGame(true, k_ESOMsg_Update, update);
        SendMessageToGame(true, k_ESOMsg_Destroy, destroy);

        SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::NameBaseItem(GCMessageRead &messageRead)
{
    uint64_t nameTagId = messageRead.ReadUint64();
    uint32_t defIndex = messageRead.ReadUint32();
    messageRead.ReadData(1); // skip the sentinel
    std::string_view name = messageRead.ReadString();

    if (!messageRead.IsValid())
    {
        Platform::Print("Parsing CMsgGCNameBaseItem failed, ignoring\n");
        return;
    }

    CMsgSOSingleObject create, destroy;
    CMsgGCItemCustomizationNotification notification;
    if (m_inventory.NameBaseItem(nameTagId, defIndex, name, create, destroy, notification))
    {
        SendMessageToGame(true, k_ESOMsg_Create, create);
        SendMessageToGame(true, k_ESOMsg_Destroy, destroy);

        SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
    }
    else
    {
        assert(false);
    }
}

void ClientGC::RemoveItemName(GCMessageRead &messageRead)
{
    uint64_t itemId = messageRead.ReadUint64();
    if (!messageRead.IsValid())
    {
        Platform::Print("Parsing CMsgGCRemoveItemName failed, ignoring\n");
        return;
    }

    CMsgSOSingleObject update, destroy;
    CMsgGCItemCustomizationNotification notification;
    if (m_inventory.RemoveItemName(itemId, update, destroy, notification))
    {
        if (update.has_type_id())
        {
            SendMessageToGame(true, k_ESOMsg_Update, update);
        }

        if (destroy.has_type_id())
        {
            SendMessageToGame(true, k_ESOMsg_Destroy, destroy);
        }

        SendMessageToGame(false, k_EMsgGCItemCustomizationNotification, notification);
    }
    else
    {
        assert(false);
    }
}


// trade-up contracts (revival addition)
// ---------------------------------------------------------------------------
// Handles the craft / trade-up message (k_EMsgGCCraft = 1002), a NON-protobuf
// "struct" message. Confirmed wire format (after the 18-byte struct header that
// GCMessageRead's constructor already consumed):
//   uint16 recipe
//   uint16 itemCount
//   uint64 itemIds[itemCount]   (low 32 bits = account id, high 32 bits = item id)
//
// We hand the input ids to Inventory::TradeUp, then push the result to the client
// using the same SO-cache Create/Destroy path that case opening uses (the client
// struct-message header in GCMessageWrite is known-broken, so we deliberately do
// NOT send a k_EMsgGCCraftResponse). Set `log_output 1` in config.txt to see logs.
void ClientGC::Craft(GCMessageRead &messageRead, const uint8_t *rawData, uint32_t rawSize)
{
    (void)rawData;
    (void)rawSize;

    uint16_t recipe = messageRead.ReadUint16();
    uint16_t count = messageRead.ReadUint16();
    if (!messageRead.IsValid())
    {
        Platform::Print("Craft: failed to read recipe/count, ignoring\n");
        return;
    }

    Platform::Print("Craft: recipe=%u, %u input item(s)\n", recipe, count);

    // sanity clamp - a real contract is 10 (or 5 for the covert->gold recipe)
    if (count == 0 || count > 64)
    {
        Platform::Print("Craft: implausible item count %u, ignoring\n", count);
        return;
    }

    std::vector<uint64_t> itemIds;
    itemIds.reserve(count);

    for (uint32_t i = 0; i < count; i++)
    {
        uint64_t itemId = messageRead.ReadUint64();
        if (!messageRead.IsValid())
        {
            Platform::Print("Craft: ran out of data at item %u/%u, ignoring\n", i, count);
            return;
        }

        itemIds.push_back(itemId);
    }

    std::vector<CMsgSOSingleObject> destroyed;
    CMsgSOSingleObject newItem;
    uint64_t newItemId = 0;

    if (!m_inventory.TradeUp(itemIds, destroyed, newItem, newItemId))
    {
        // TradeUp already logged the reason; nothing was consumed or created
        Platform::Print("Craft: trade-up rejected, inventory unchanged\n");
        return;
    }

    // consume the inputs, then deliver the crafted item (same order as UnlockCrate)
    for (CMsgSOSingleObject &destroy : destroyed)
    {
        SendMessageToGame(true, k_ESOMsg_Destroy, destroy);
    }

    SendMessageToGame(true, k_ESOMsg_Create, newItem);

    Platform::Print("Craft: trade-up complete (%zu consumed, 1 created)\n", destroyed.size());
}
