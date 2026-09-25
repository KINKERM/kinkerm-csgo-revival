#include "stdafx.h"
#include "gc_server.h"
#include "steam_hook.h"
#include "gc_const.h"
#include "gc_const_csgo.h"
#include "graffiti.h"
#include "inventory.h"

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <unordered_map>

#ifdef _WIN32
#include <direct.h>
#else
#include <sys/stat.h>
#endif

// yuck!! needed for CSteamID (construct full id from account id)
#include "steam/steamclientpublic.h"

ServerGC::ServerGC()
{
    // also called from ClientGC's constructor
    Graffiti::Initialize();

    StartThread();

    Platform::Print("ServerGC spawned\n");
    Platform::Print("REVIVAL_SERVER_REWARD_BRIDGE_V1 active; REVIVAL_REWARD_SPOOL_QUEUE_V1 active; REVIVAL_SERVER_LOCAL_SOCACHE_V1 active; REVIVAL_SERVER_ACCEPT_ROSTER_V1 active; REVIVAL_SERVER_RESERVATION_RETRY_V4 active; REVIVAL_SERVER_RESERVATION_RETRY_V3 compatible; REVIVAL_SERVER_RESERVATION_RETRY_V2 compatible\n");
}

ServerGC::~ServerGC()
{
    StopThread();
    Platform::Print("ServerGC destroyed\n");
}

void ServerGC::HandleEvent(GCEvent type, uint64_t id, const std::vector<uint8_t> &buffer)
{
    switch (type)
    {
    case GCEvent::Message:
        HandleMessage(static_cast<uint32_t>(id), buffer.data(), static_cast<uint32_t>(buffer.size()));
        break;

    case GCEvent::NetMessage:
        HandleNetMessage(id, buffer.data(), static_cast<uint32_t>(buffer.size()));
        break;

    case GCEvent::ClientSOCacheUnsubscribe:
        HandleClientSOCacheUnsubscribe(id);
        break;

    case GCEvent::ClientLocalInventoryRequest:
        HandleClientLocalInventoryRequest(id);
        break;

    default:
        assert(false);
        break;
    }
}

void ServerGC::HandleIdle()
{
    // End-match reveal is timing-sensitive: the stock scoreboard waits for the
    // server gamerules drop list during intermission. Poll this tiny local trigger
    // every GC idle tick rather than on the slower reservation refresh cadence.
    ProcessRevivalMatchEndTrigger();

    // Some Legacy dedicated-server builds never emit k_EMsgGCServerHello after
    // the injected GC comes up. If we wait for that hello, 9105 is never queued
    // and the agent waits forever for 9106. Proactively establish the local GC
    // welcome once, then retry 9105 every ~2 seconds until Source answers.
    ++m_reservationIdleTicks;
    if ((m_reservationIdleTicks & 7u) != 0)
        return;

    if (!m_sentWelcome)
    {
        SendServerWelcome();
        Platform::Print("matchmaking server: proactive GCServerWelcome queued\n");
    }

    SendMatchmakingReservation();

    // 9107 requires the actual game-server SteamID. Steam may assign it a
    // moment after the reservation response, so backfill it once available.
    const uint64_t serverId = RevivalGameServerSteamId();
    if (serverId)
    {
        std::ifstream in("csgo_gc/server_reservation_response.txt", std::ios::binary);
        std::string existing((std::istreambuf_iterator<char>(in)),
            std::istreambuf_iterator<char>());
        if (!existing.empty() && existing.find("server_id=") == std::string::npos)
        {
            std::ofstream out("csgo_gc/server_reservation_response.txt",
                std::ios::binary | std::ios::app);
            if (out.is_open())
            {
                out << "server_id=" << serverId << "\n";
                out.flush();
                Platform::Print(
                    "matchmaking server: published gameserver SteamID %llu\n",
                    serverId);
            }
        }
    }
}

void ServerGC::HandleMessage(uint32_t type, const void *data, uint32_t size)
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
        case k_EMsgGCServerHello:
            if (!m_sentWelcome)
                SendServerWelcome();
            SendMatchmakingReservation();
            break;

        case k_EMsgGCCStrike15_v2_MatchmakingServerReservationResponse:
            MatchmakingReservationResponse(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_Server2GCClientValidate:
            // server doesn't want a response so ignore
            break;

        case k_EMsgGC_IncrementKillCountAttribute:
            IncrementKillCountAttribute(messageRead);
            break;

        case k_EMsgGCCStrike15_v2_MatchEndRunRewardDrops:
            MatchEndRunRewardDrops(messageRead);
            break;

        default:
            Platform::Print("ServerGC::HandleMessage: unhandled protobuf message %s)\n",
                MessageName(messageRead.TypeUnmasked()));
            break;
        }
    }
}

void ServerGC::HandleClientSOCacheUnsubscribe(uint64_t steamId)
{
    Platform::Print("HandleClientSOCacheUnsubscribe: %llu\n", steamId);

    CMsgSOCacheUnsubscribed message;
    message.mutable_owner_soid()->set_type(SoIdTypeSteamId);
    message.mutable_owner_soid()->set_id(steamId);

    GCMessageWrite write{ k_ESOMsg_CacheUnsubscribed, message };
    PostToHost(HostEvent::Message, write.TypeMasked(), write.Data(), write.Size());
}

void ServerGC::HandleClientLocalInventoryRequest(uint64_t steamId)
{
    // This event is posted directly from ISteamGameServer::BeginAuthSession.
    // Publish an authoritative local presence marker before touching inventory
    // data so the Python agent can never time out a player Source already
    // authenticated, even if log tailing/RCON status formatting changes.
    const uint32_t accountId = static_cast<uint32_t>(steamId & 0xFFFFFFFFull);
    const std::string authDir = "csgo_gc/server_auth";
#ifdef _WIN32
    _mkdir(authDir.c_str());
#else
    mkdir(authDir.c_str(), 0755);
#endif
    const std::string authPath =
        authDir + "/" + std::to_string(accountId) + ".txt";
    {
        std::ofstream auth(authPath, std::ios::binary | std::ios::trunc);
        if (auth.is_open())
        {
            auth << steamId << "\n";
            auth.flush();
            Platform::Print(
                "REVIVAL_SERVER_PLAYER_AUTH_V1 account=%u steamid=%llu\n",
                accountId, static_cast<unsigned long long>(steamId));
        }
    }

    const std::string path =
        "csgo_gc/server_players/" + std::to_string(steamId) + ".txt";

    std::ifstream probe(path, std::ios::binary);
    if (!probe.is_open())
    {
        Platform::Print(
            "REVIVAL_SERVER_LOCAL_SOCACHE_V1 missing inventory for %llu at %s\n",
            steamId, path.c_str());
        return;
    }
    probe.close();

    Inventory inventory{ steamId, path };
    CMsgSOCacheSubscribed message;
    inventory.BuildCacheSubscription(message, inventory.ProfileLevel(), true);

    GCMessageWrite write{ k_ESOMsg_CacheSubscribed, message };
    PostToHost(HostEvent::Message, write.TypeMasked(), write.Data(), write.Size());

    Platform::Print(
        "REVIVAL_SERVER_LOCAL_SOCACHE_V1 injected equipped SOCache for %llu from %s\n",
        steamId, path.c_str());
}

template<typename T>
static bool ValidateMessageOwnerSOID(GCMessageRead &messageRead, uint64_t steamId, std::optional<GCMessageWrite> &)
{
    T message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("ValidateMessageOwnerSOID %llu: parsing failed\n", steamId);
        return false;
    }

    if (message.owner_soid().type() != SoIdTypeSteamId
        || message.owner_soid().id() != steamId)
    {
        Platform::Print("ValidateMessageOwnerSOID %llu: steam id mismatch (message has %llu)\n",
            steamId, message.owner_soid().id());
        return false;
    }

    return true;
}

// FIXME: made up
constexpr int MaxServerSOCacheItems = 64;

static bool RemoveUnequippedItems(CMsgSOCacheSubscribed &message, int &itemCount)
{
    bool modified = false;

    for (auto it = message.mutable_objects()->begin(); it != message.mutable_objects()->end(); it++)
    {
        if (it->type_id() != SOTypeItem)
        {
            continue;
        }

        for (auto obj = it->mutable_object_data()->begin(); obj != it->mutable_object_data()->end(); )
        {
            CSOEconItem item;
            if (!item.ParseFromString(*obj) || !item.equipped_state_size())
            {
                obj = it->mutable_object_data()->erase(obj);
                modified = true;
            }
            else
            {
                obj++;
                itemCount++;
            }
        }
    }

    return modified;
}

template<>
bool ValidateMessageOwnerSOID<CMsgSOCacheSubscribed>(GCMessageRead &messageRead, uint64_t steamId, std::optional<GCMessageWrite> &sanitized)
{
    CMsgSOCacheSubscribed message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("ValidateMessageOwnerSOID %llu: parsing failed\n", steamId);
        return false;
    }

    if (message.owner_soid().type() != SoIdTypeSteamId
        || message.owner_soid().id() != steamId)
    {
        Platform::Print("ValidateMessageOwnerSOID %llu: steam id mismatch (message has %llu)\n",
            steamId, message.owner_soid().id());
        return false;
    }

    size_t oldSize = message.ByteSizeLong();

    int itemCount = 0;
    bool modified = RemoveUnequippedItems(message, itemCount);

    if (itemCount > MaxServerSOCacheItems)
    {
        Platform::Print("Client %llu socache has %d items (max allowed %d), ignoring\n", itemCount, MaxServerSOCacheItems);
        return false;
    }

    if (modified)
    {
        Platform::Print("SOCache from %llu had to be cleaned up (%zu -> %zu bytes)\n", steamId, oldSize, message.ByteSizeLong());
        sanitized.emplace(k_ESOMsg_CacheSubscribed, message);
    }

    return true;
}

void ServerGC::HandleNetMessage(uint64_t steamId, const void *data, uint32_t size)
{
    Platform::Print("HandleNetMessage: %llu, %u bytes\n", steamId, size);

    GCMessageRead validate{ 0, data, size };
    if (!validate.IsValid())
    {
        assert(false);
        return;
    }

    if (!validate.IsProtobuf())
    {
        // all the allowed messages are protobuf based
        Platform::Print("ServerGC: ignoring non protobuf message %u from %llu\n",
            validate.TypeUnmasked(), steamId);
        return;
    }

    // validate the type and contents
    bool isValid = false;
    std::optional<GCMessageWrite> sanitized;

    switch (validate.TypeUnmasked())
    {
    case k_ESOMsg_Create:
    case k_ESOMsg_Update:
    case k_ESOMsg_Destroy:
        isValid = ValidateMessageOwnerSOID<CMsgSOSingleObject>(validate, steamId, sanitized);
        break;

    case k_ESOMsg_CacheSubscribed:
        isValid = ValidateMessageOwnerSOID<CMsgSOCacheSubscribed>(validate, steamId, sanitized);
        break;

    case k_ESOMsg_UpdateMultiple:
        isValid = ValidateMessageOwnerSOID<CMsgSOMultipleObjects>(validate, steamId, sanitized);
        break;

    case k_EMsgGCItemAcknowledged:
        isValid = true;
        break;
    }

    if (!isValid)
    {
        Platform::Print("ServerGC: ignoring net message %u from %llu\n",
            validate.TypeUnmasked(), steamId);
        return;
    }

    if (!m_sentWelcome)
    {
        // FIXME: ideally we'd sent this on steam logon, instead of on demand...
        Platform::Print("Sending server welcome due to net message\n");
        SendServerWelcome();
    }

    if (sanitized.has_value())
    {
        // pass the sanitized message
        PostToHost(HostEvent::Message, sanitized->TypeMasked(), sanitized->Data(), sanitized->Size());
    }
    else
    {
        // otherwise the old message was fine
        PostToHost(HostEvent::Message, validate.TypeMasked(), data, size);
    }
}

void ServerGC::SendServerWelcome()
{
    // we don't care about anything in this message, just reply

    CMsgCStrike15Welcome csWelcome;
    csWelcome.set_gscookieid(GameServerCookieId);

    CMsgClientWelcome welcome;
    welcome.set_version(0);
    welcome.set_game_data(csWelcome.SerializeAsString());
    welcome.set_rtime32_gc_welcome_timestamp(static_cast<uint32_t>(time(nullptr)));

    GCMessageWrite write{ k_EMsgGCServerWelcome, welcome };
    PostToHost(HostEvent::Message, write.TypeMasked(), write.Data(), write.Size());

    m_sentWelcome = true;
}

namespace
{
constexpr uint32_t RevivalMsgMatchmakingGC2ServerReserve = 9105;
constexpr const char *ServerReservationPath = "csgo_gc/server_reservation.txt";

std::unordered_map<std::string, std::string> ReadServerReservationFile()
{
    std::unordered_map<std::string, std::string> out;
    std::ifstream in(ServerReservationPath, std::ios::binary);
    std::string line;
    while (std::getline(in, line))
    {
        const size_t eq = line.find('=');
        if (eq != std::string::npos)
            out[line.substr(0, eq)] = line.substr(eq + 1);
    }
    return out;
}

uint64_t ReservationNumber(
    const std::unordered_map<std::string, std::string> &kv,
    const char *key, uint64_t fallback = 0)
{
    auto it = kv.find(key);
    if (it == kv.end())
        return fallback;
    char *end = nullptr;
    const unsigned long long value = std::strtoull(it->second.c_str(), &end, 10);
    return end && *end == '\0' ? static_cast<uint64_t>(value) : fallback;
}

std::string NextRewardSpoolPath(uint64_t steamId, const char *kind)
{
    static uint64_t sequence = 0;
    char path[256];
    snprintf(
        path, sizeof(path),
        "csgo_gc/server_rewards/%llu_%010llu_%020llu_%s.bin",
        static_cast<unsigned long long>(steamId),
        static_cast<unsigned long long>(time(nullptr)),
        static_cast<unsigned long long>(++sequence),
        kind ? kind : "packet");
    return path;
}

bool WriteRewardSpool(
    uint64_t steamId, const char *kind, const void *data, size_t size,
    std::string *writtenPath = nullptr)
{
    if (!data || !size)
        return false;

    const std::string path = NextRewardSpoolPath(steamId, kind);
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out.is_open())
        return false;

    out.write(
        reinterpret_cast<const char *>(data),
        static_cast<std::streamsize>(size));
    out.flush();
    if (!out.good())
        return false;

    if (writtenPath)
        *writtenPath = path;
    Platform::Print(
        "REVIVAL_REWARD_SPOOL_QUEUE_V1 queued %s for %llu (%zu bytes) path=%s\n",
        kind ? kind : "packet",
        static_cast<unsigned long long>(steamId), size, path.c_str());
    return true;
}

std::string BuildQueuedReservationPayload(
    uint64_t cookie, uint64_t matchId,
    const CMsgGCCStrike15_v2_MatchmakingGC2ServerReserve &reserve)
{
    char header[96];
    snprintf(header, sizeof(header), "Q%llx,%llx,1:",
        static_cast<unsigned long long>(cookie),
        static_cast<unsigned long long>(matchId ? matchId : cookie));

    std::string payload = header;
    char token[24];
    for (int i = 0; i < reserve.account_ids_size(); ++i)
    {
        // ReserveServerForQueuedGame parses the roster as hexadecimal AccountIDs.
        snprintf(token, sizeof(token), "[%x]", reserve.account_ids(i));
        payload += token;
    }
    return payload;
}
} // namespace

void ServerGC::ProcessRevivalMatchEndTrigger()
{
    constexpr const char *TriggerPath = "csgo_gc/server_match_end_trigger.txt";
    std::ifstream trigger(TriggerPath, std::ios::binary);
    if (!trigger.is_open())
        return;

    std::unordered_map<std::string, std::string> end;
    std::string line;
    while (std::getline(trigger, line))
    {
        const size_t eq = line.find('=');
        if (eq != std::string::npos)
            end[line.substr(0, eq)] = line.substr(eq + 1);
    }
    trigger.close();

    const uint64_t matchId = ReservationNumber(end, "match_id");
    if (!matchId || matchId == m_lastSyntheticDropMatchId)
    {
        std::remove(TriggerPath);
        return;
    }

    const auto reservation = ReadServerReservationFile();
    if (ReservationNumber(reservation, "match_id") != matchId)
    {
        // Ignore a stale trigger from a previous srcds allocation.
        std::remove(TriggerPath);
        return;
    }

    auto accountIt = reservation.find("account_ids");
    if (accountIt == reservation.end() || accountIt->second.empty())
        return;

    const uint32_t timePlayed = static_cast<uint32_t>(
        std::min<uint64_t>(ReservationNumber(end, "time_played"), UINT32_MAX));

    std::vector<uint32_t> accountIds;
    std::stringstream accountStream(accountIt->second);
    std::string part;
    while (std::getline(accountStream, part, ','))
    {
        char *parseEnd = nullptr;
        const unsigned long value = std::strtoul(part.c_str(), &parseEnd, 10);
        if (parseEnd && *parseEnd == '\0' && value && value <= UINT32_MAX)
            accountIds.push_back(static_cast<uint32_t>(value));
    }

    bool processedAny = false;
    for (uint32_t accountId : accountIds)
    {
        CSteamID playerId{ accountId, k_EUniversePublic, k_EAccountTypeIndividual };
        const uint64_t steamId = playerId.ConvertToUint64();
        const std::string inventoryPath =
            "csgo_gc/server_players/" + std::to_string(steamId) + ".txt";

        std::ifstream probe(inventoryPath, std::ios::binary);
        if (!probe.is_open())
        {
            Platform::Print(
                "REVIVAL_NATIVE_DROP_REVEAL_V1 missing cached inventory for %llu\n",
                static_cast<unsigned long long>(steamId));
            continue;
        }
        probe.close();

        Inventory inventory{ steamId, inventoryPath };

        struct BridgeMessage
        {
            uint32_t type{};
            std::vector<uint8_t> bytes;
        };
        std::vector<BridgeMessage> bridgeMessages;

        auto queueMessage = [&bridgeMessages](const GCMessageWrite &write)
        {
            BridgeMessage out;
            out.type = write.TypeMasked();
            const auto *begin =
                static_cast<const uint8_t *>(write.Data());
            out.bytes.assign(begin, begin + write.Size());
            bridgeMessages.emplace_back(std::move(out));
        };

        auto publishDrop = [this, &queueMessage](
            CMsgSOSingleObject &create,
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &drop)
        {
            if (!drop.has_iteminfo())
                return;

            const std::string preview = drop.iteminfo().SerializeAsString();
            PostToHost(
                HostEvent::RecordPlayerItemDrop, drop.iteminfo().accountid(),
                preview.data(), static_cast<uint32_t>(preview.size()));

            GCMessageWrite createWrite{ k_ESOMsg_Create, create };
            GCMessageWrite dropWrite{
                k_EMsgGCCStrike15_v2_MatchEndRewardDropsNotification, drop };
            queueMessage(createWrite);
            queueMessage(dropWrite);
        };

        for (int i = 0; i < 2; ++i)
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (inventory.CreateRandomCaseMatchDrop(create, drop))
                publishDrop(create, drop);
        }

        static const std::vector<std::string_view> Dust2021{
            "set_dust_2_2021"
        };
        static const std::vector<std::string_view> DustLegacy{
            "set_dust_2"
        };
        static const std::vector<std::string_view> Cache{
            "set_cache"
        };
        static const std::vector<std::string_view> Cobblestone{
            "set_cobblestone"
        };

        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (inventory.CreateRandomCollectionMatchDrop(Dust2021, create, drop)
                || inventory.CreateRandomCollectionMatchDrop(DustLegacy, create, drop))
            {
                publishDrop(create, drop);
            }
        }

        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (inventory.CreateRandomCollectionMatchDrop(Cache, create, drop))
                publishDrop(create, drop);
        }

        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (inventory.CreateRareCollectionBonusMatchDrop(
                Cobblestone, 20, create, drop))
            {
                publishDrop(create, drop);
            }
        }

        if (timePlayed)
        {
            CMsgSOSingleObject create;
            CMsgGCCStrike15_v2_MatchEndRewardDropsNotification drop;
            if (inventory.AddMatchPlaytimeAndCreateCaseDrop(
                timePlayed, create, drop))
            {
                publishDrop(create, drop);
            }
        }

        if (bridgeMessages.empty())
        {
            Platform::Print(
                "REVIVAL_NATIVE_DROP_REVEAL_V1 no drops generated for account=%u\n",
                accountId);
            continue;
        }

        std::vector<uint8_t> bundle;
        const char magic[8] = { 'R','V','M','S','G','V','1','\0' };
        bundle.insert(bundle.end(), magic, magic + sizeof(magic));
        const uint32_t count = static_cast<uint32_t>(bridgeMessages.size());
        const auto *countBytes = reinterpret_cast<const uint8_t *>(&count);
        bundle.insert(bundle.end(), countBytes, countBytes + sizeof(count));
        for (const BridgeMessage &message : bridgeMessages)
        {
            const uint32_t size = static_cast<uint32_t>(message.bytes.size());
            const auto *typeBytes = reinterpret_cast<const uint8_t *>(&message.type);
            const auto *sizeBytes = reinterpret_cast<const uint8_t *>(&size);
            bundle.insert(bundle.end(), typeBytes, typeBytes + sizeof(message.type));
            bundle.insert(bundle.end(), sizeBytes, sizeBytes + sizeof(size));
            bundle.insert(bundle.end(), message.bytes.begin(), message.bytes.end());
        }

        const bool spooled = WriteRewardSpool(
            steamId, "dropbundle", bundle.data(), bundle.size());
        Platform::Print(
            "REVIVAL_NATIVE_DROP_REVEAL_V1 queued %u client messages for account=%u match=%llu spooled=%d\n",
            static_cast<unsigned>(bridgeMessages.size()), accountId,
            static_cast<unsigned long long>(matchId), spooled ? 1 : 0);
        processedAny = true;
    }

    if (processedAny)
    {
        m_lastSyntheticDropMatchId = matchId;
        std::remove(TriggerPath);
        Platform::Print(
            "REVIVAL_NATIVE_DROP_REVEAL_V1 processed match=%llu players=%u\n",
            static_cast<unsigned long long>(matchId),
            static_cast<unsigned>(accountIds.size()));
    }
}

void ServerGC::SendMatchmakingReservation()
{
    const auto kv = ReadServerReservationFile();
    const uint64_t matchId = ReservationNumber(kv, "match_id");
    if (!matchId)
    {
        Platform::Print("matchmaking server: no local reservation file; normal standalone srcds mode\n");
        return;
    }

    CMsgGCCStrike15_v2_MatchmakingGC2ServerReserve reserve;
    reserve.set_match_id(matchId);
    reserve.set_game_type(static_cast<uint32_t>(ReservationNumber(kv, "game_type", 8)));
    reserve.set_server_version(
        static_cast<uint32_t>(ReservationNumber(kv, "server_version", 0)));

    auto accounts = kv.find("account_ids");
    const std::string accountText =
        accounts != kv.end() ? accounts->second : std::string{};

    if (accounts != kv.end())
    {
        std::istringstream stream(accounts->second);
        std::string part;
        while (std::getline(stream, part, ','))
        {
            char *parseEnd = nullptr;
            const unsigned long value = std::strtoul(part.c_str(), &parseEnd, 10);
            if (parseEnd && *parseEnd == '\0' && value && value <= UINT32_MAX)
                reserve.add_account_ids(static_cast<uint32_t>(value));
        }
    }

    if (!reserve.account_ids_size())
    {
        Platform::Print("matchmaking server: reservation %llu has no accounts; refusing reservation\n",
            matchId);
        return;
    }

    // The 9105/9106 exchange alone does NOT arm the public Legacy engine's
    // queued-reservation state. The stock client enters "Confirming match" and
    // sends A2S_RESERVE_CHECK (0x21); only ReserveServerForQueuedGame with a Q
    // roster makes server.dll answer 0x25 with the real ready-up stages.
    //
    // This HostEvent is drained from SteamGameServer_RunCallbacks on the main
    // thread. Refresh every ~8 seconds (HandleIdle calls this about every 2s)
    // so sv_mmqueue_reservation_timeout cannot expire while the Accept UI is up.
    const std::string queuePayload =
        BuildQueuedReservationPayload(GameServerCookieId, matchId, reserve);
    const bool queueChanged = queuePayload != m_lastQueueReservationPayload;
    if (queueChanged || ++m_queueReservationRefreshTicks >= 4)
    {
        PostToHost(HostEvent::ReserveServerForQueuedGame, matchId,
            queuePayload.data(), static_cast<uint32_t>(queuePayload.size()));
        m_lastQueueReservationPayload = queuePayload;
        m_queueReservationRefreshTicks = 0;
        Platform::Print(
            queueChanged
                ? "matchmaking server: queued engine Q reservation match=%llu roster=%d payload=%s\n"
                : "matchmaking server: refreshing engine Q reservation match=%llu roster=%d payload=%s\n",
            matchId, reserve.account_ids_size(), queuePayload.c_str());
    }

    // The native GC request is still useful for Source's normal bookkeeping,
    // account filtering and match-end messages. Resend if the membership or
    // request metadata changed; otherwise stop retrying after a valid 9106.
    const std::string signature =
        std::to_string(matchId) + "|" +
        std::to_string(ReservationNumber(kv, "game_type", 8)) + "|" +
        std::to_string(ReservationNumber(kv, "server_version", 0)) + "|" +
        accountText;

    if (m_sentReservation && signature == m_lastReservationSignature)
    {
        std::ifstream response("csgo_gc/server_reservation_response.txt", std::ios::binary);
        uint64_t responseMatch = 0;
        uint64_t responseReservation = 0;
        std::string line;
        while (std::getline(response, line))
        {
            const size_t eq = line.find('=');
            if (eq == std::string::npos)
                continue;
            const std::string key = line.substr(0, eq);
            const std::string value = line.substr(eq + 1);
            char *parseEnd = nullptr;
            const unsigned long long parsed = std::strtoull(value.c_str(), &parseEnd, 10);
            if (!parseEnd || *parseEnd != '\0')
                continue;
            if (key == "match_id")
                responseMatch = static_cast<uint64_t>(parsed);
            else if (key == "reservation_id")
                responseReservation = static_cast<uint64_t>(parsed);
        }

        if (responseMatch == matchId && responseReservation)
            return;
    }

    GCMessageWrite write{ RevivalMsgMatchmakingGC2ServerReserve, reserve };
    PostToHost(HostEvent::Message, write.TypeMasked(), write.Data(), write.Size());
    const bool refresh = m_sentReservation;
    m_sentReservation = true;
    m_lastReservationSignature = signature;

    Platform::Print(
        refresh
            ? "matchmaking server: refreshed native 9105 match=%llu accounts=%d game_type=%u version=%u\n"
            : "matchmaking server: sent native 9105 match=%llu accounts=%d game_type=%u version=%u\n",
        matchId, reserve.account_ids_size(), reserve.game_type(), reserve.server_version());
}

void ServerGC::MatchmakingReservationResponse(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchmakingServerReservationResponse response;
    if (!messageRead.ReadProtobuf(response))
    {
        Platform::Print("matchmaking server: failed to parse native 9106 response\n");
        return;
    }

    const uint64_t matchId =
        response.has_reservation() && response.reservation().has_match_id()
            ? response.reservation().match_id() : 0;
    const uint64_t reservationId =
        response.has_reservationid() ? response.reservationid() : 0;

    if (!matchId || !reservationId)
    {
        // Public/community Legacy DS builds can answer our valid 9105 with an
        // otherwise empty 9106. Source already received GameServerCookieId in
        // GCServerWelcome, so persist that exact engine cookie as the local
        // reservation response instead of waiting for the Python agent to infer
        // readiness later from Match_Start. This keeps one authoritative cookie
        // end-to-end: server welcome -> coordinator -> client 9107.
        const auto current = ReadServerReservationFile();
        const uint64_t requestedMatchId = ReservationNumber(current, "match_id");
        if (requestedMatchId)
        {
            std::ofstream out("csgo_gc/server_reservation_response.txt",
                std::ios::binary | std::ios::trunc);
            if (out.is_open())
            {
                out << "match_id=" << requestedMatchId << "\n";
                out << "reservation_id=" << GameServerCookieId << "\n";
                out << "account_ids=";
                auto accounts = current.find("account_ids");
                if (accounts != current.end())
                    out << accounts->second;
                out << "\n";
                const uint64_t serverId = RevivalGameServerSteamId();
                if (serverId)
                    out << "server_id=" << serverId << "\n";
                out.flush();
            }

            Platform::Print(
                "matchmaking server: native 9106 empty; using GC welcome cookie fallback match=%llu reservation=%llu\n",
                requestedMatchId, GameServerCookieId);
            return;
        }

        Platform::Print(
            "matchmaking server: native 9106 missing match/reservation id (match=%llu reservation=%llu) and no local request\n",
            matchId, reservationId);
        return;
    }

    std::ofstream out("csgo_gc/server_reservation_response.txt", std::ios::binary | std::ios::trunc);
    if (out.is_open())
    {
        out << "match_id=" << matchId << "\n";
        out << "reservation_id=" << reservationId << "\n";
        if (response.has_map())
            out << "map=" << response.map() << "\n";

        // Persist the exact account list acknowledged by the game server.
        // This is used by the HTTP coordinator as a barrier before a late
        // drop-in client receives 9107, preventing a race where the client
        // connects before sv_mmqueue_reservation contains its account id.
        out << "account_ids=";
        bool wroteAccount = false;
        if (response.has_reservation())
        {
            for (int i = 0; i < response.reservation().account_ids_size(); ++i)
            {
                if (wroteAccount)
                    out << ",";
                out << response.reservation().account_ids(i);
                wroteAccount = true;
            }
        }
        if (!wroteAccount)
        {
            const auto current = ReadServerReservationFile();
            auto accounts = current.find("account_ids");
            if (accounts != current.end())
                out << accounts->second;
        }
        out << "\n";
        const uint64_t serverId = RevivalGameServerSteamId();
        if (serverId)
            out << "server_id=" << serverId << "\n";
        out.flush();
    }

    Platform::Print(
        "matchmaking server: native 9106 accepted match=%llu reservation=%llu map=%s\n",
        matchId, reservationId, response.has_map() ? response.map().c_str() : "");
}

void ServerGC::MatchEndRunRewardDrops(GCMessageRead &messageRead)
{
    CMsgGCCStrike15_v2_MatchEndRunRewardDrops message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgGCCStrike15_v2_MatchEndRunRewardDrops failed, ignoring\n");
        return;
    }

    if (!message.has_match_end_quest_data())
    {
        return;
    }

    // The dedicated game server reports all players in one message. The local
    // revival has one ClientGC per Steam user, so split the payload and forward
    // only that player's quest data over the existing private GC network.
    for (const PlayerQuestData &playerData : message.match_end_quest_data().player_quest_data())
    {
        if (!playerData.has_quester_account_id() || !playerData.quester_account_id())
        {
            continue;
        }

        CMsgGCCStrike15_v2_MatchEndRunRewardDrops perPlayer = message;
        CMsgGC_ServerQuestUpdateData *questData = perPlayer.mutable_match_end_quest_data();
        questData->clear_player_quest_data();
        questData->add_player_quest_data()->CopyFrom(playerData);

        GCMessageWrite messageWrite{ k_EMsgGCCStrike15_v2_MatchEndRunRewardDrops, perPlayer };
        CSteamID playerId{
            playerData.quester_account_id(),
            k_EUniversePublic,
            k_EAccountTypeIndividual
        };

        const uint64_t playerSteamId = playerId.ConvertToUint64();

        // Keep the original Steam-P2P path for servers that have a real
        // gameserver identity, but also spool the exact 9136 packet locally.
        // Direct-UDP revival servers do not have a usable gameserver SteamID,
        // so the laptop agent relays this file through the backend/launcher.
        {
            const bool spooled = WriteRewardSpool(
                playerSteamId, "9136", messageWrite.Data(), messageWrite.Size());
            Platform::Print(
                spooled
                    ? "REVIVAL_SERVER_REWARD_BRIDGE_V1 spooled 9136 for %llu (%u bytes)\n"
                    : "REVIVAL_SERVER_REWARD_BRIDGE_V1 failed to spool 9136 for %llu (%u bytes)\n",
                static_cast<unsigned long long>(playerSteamId),
                messageWrite.Size());
        }

        PostToHost(HostEvent::NetMessage,
            playerSteamId,
            messageWrite.Data(),
            messageWrite.Size());

        Platform::Print("operation: forwarded match-end quest data to account %u\n",
            playerData.quester_account_id());
    }
}

void ServerGC::IncrementKillCountAttribute(GCMessageRead &messageRead)
{
    CMsgIncrementKillCountAttribute message;
    if (!messageRead.ReadProtobuf(message))
    {
        Platform::Print("Parsing CMsgIncrementKillCountAttribute failed, ignoring\n");
        return;
    }

    // just forward it to the killer
    GCMessageWrite messageWrite{ k_EMsgGC_IncrementKillCountAttribute, message };
    CSteamID killerId{ message.killer_account_id(), k_EUniversePublic, k_EAccountTypeIndividual };
    PostToHost(HostEvent::NetMessage, killerId.ConvertToUint64(), messageWrite.Data(), messageWrite.Size());
}
