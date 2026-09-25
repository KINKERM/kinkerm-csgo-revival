#include "stdafx.h"
#include "gc_server.h"
#include "gc_const.h"
#include "gc_const_csgo.h"
#include "graffiti.h"

#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>

// yuck!! needed for CSteamID (construct full id from account id)
#include "steam/steamclientpublic.h"

ServerGC::ServerGC()
{
    // also called from ClientGC's constructor
    Graffiti::Initialize();

    StartThread();

    Platform::Print("ServerGC spawned\n");
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

    default:
        assert(false);
        break;
    }
}

void ServerGC::HandleIdle()
{
    // SharedGC idles at ~250 ms. Re-read the tiny local reservation request
    // twice per second so later humans can be appended to the SAME live match.
    // SendMatchmakingReservation itself suppresses unchanged requests.
    if ((++m_reservationIdleTicks & 1u) == 0)
        SendMatchmakingReservation();
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
constexpr const char *ServerReservationResponsePath = "csgo_gc/server_reservation_response.txt";

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
} // namespace

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

    // The initial 9105 creates the native reservation. If the laptop agent
    // later expands account_ids for a drop-in human, resend 9105 with the same
    // match so Source refreshes sv_mmqueue_reservation's allowed account list.
    // Do not spam the game server when the file has not changed.
    const std::string accountText =
        accounts != kv.end() ? accounts->second : std::string{};
    const std::string signature =
        std::to_string(matchId) + "|" +
        std::to_string(ReservationNumber(kv, "game_type", 8)) + "|" +
        std::to_string(ReservationNumber(kv, "server_version", 0)) + "|" +
        accountText;
    if (m_sentReservation && signature == m_lastReservationSignature)
        return;

    if (accounts != kv.end())
    {
        std::istringstream stream(accounts->second);
        std::string part;
        while (std::getline(stream, part, ','))
        {
            char *end = nullptr;
            const unsigned long value = std::strtoul(part.c_str(), &end, 10);
            if (end && *end == '\0' && value && value <= UINT32_MAX)
                reserve.add_account_ids(static_cast<uint32_t>(value));
        }
    }

    if (!reserve.account_ids_size())
    {
        Platform::Print("matchmaking server: reservation %llu has no accounts; refusing 9105\n",
            matchId);
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
        Platform::Print(
            "matchmaking server: native 9106 missing match/reservation id (match=%llu reservation=%llu)\n",
            matchId, reservationId);
        return;
    }

    std::ofstream out(ServerReservationResponsePath, std::ios::binary | std::ios::trunc);
    if (out.is_open())
    {
        out << "match_id=" << matchId << "\n";
        out << "reservation_id=" << reservationId << "\n";
        if (response.has_map())
            out << "map=" << response.map() << "\n";
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

        PostToHost(HostEvent::NetMessage,
            playerId.ConvertToUint64(),
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
