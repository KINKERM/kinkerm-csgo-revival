#pragma once

#include "config.h"
#include "gc_shared.h"
#include "inventory.h"

class ClientGC final : public SharedGC
{
public:
    ClientGC(uint64_t steamId);
    ~ClientGC();

private:
    void HandleEvent(GCEvent type, uint64_t id, const std::vector<uint8_t> &buffer) override;

    // event handlers
    void HandleMessage(uint32_t type, const void *data, uint32_t size);
    void HandleNetMessage(const void *data, uint32_t size);
    void HandleSOCacheRequest();

    // send to the local game and the game server we're connected to (if we're connected)
    void SendMessageToGame(bool sendToGameServer, uint32_t type,
        const google::protobuf::MessageLite &message, uint64_t jobId = JobIdInvalid);

    void OnClientHello(GCMessageRead &messageRead);
    void AdjustItemEquippedState(GCMessageRead &messageRead);
    void ClientPlayerDecalSign(GCMessageRead &messageRead);
    void UseItemRequest(GCMessageRead &messageRead);
    void ClientRequestJoinServerData(GCMessageRead &messageRead);
    void MatchmakingStart(GCMessageRead &messageRead);
    void MatchmakingStop(GCMessageRead &messageRead);
    void MatchmakingPing(GCMessageRead &messageRead);
    void MatchmakingHello(GCMessageRead &messageRead);
    void PollMatchmakingBridge();
    void ClientRequestNewMission(GCMessageRead &messageRead);
    void ClientRedeemMissionReward(GCMessageRead &messageRead);
    void SetItemPositions(GCMessageRead &messageRead);
    void IncrementKillCountAttribute(GCMessageRead &messageRead);
    void MatchEndRunRewardDrops(GCMessageRead &messageRead);
    void ApplySticker(GCMessageRead &messageRead);
    void StoreGetUserData(GCMessageRead &messageRead);
    void StorePurchaseInit(GCMessageRead &messageRead);
    void StorePurchaseFinalize(GCMessageRead &messageRead);

    void DeleteItem(GCMessageRead &messageRead);
    void UnlockCrate(GCMessageRead &messageRead);
    void NameItem(GCMessageRead &messageRead);
    void NameBaseItem(GCMessageRead &messageRead);
    void RemoveItemName(GCMessageRead &messageRead);

    // trade-up contracts (revival addition)
    // stage 1: log the raw craft message so we can confirm its wire format
    void Craft(GCMessageRead &messageRead, const uint8_t *rawData, uint32_t rawSize);

    void BuildMatchmakingHello(CMsgGCCStrike15_v2_MatchmakingGC2ClientHello &message);
    void BuildClientWelcome(CMsgClientWelcome &message, const CMsgCStrike15Welcome &csWelcome,
        const CMsgGCCStrike15_v2_MatchmakingGC2ClientHello &matchmakingHello);
    void SendRankUpdate();

    uint32_t AccountId() const { return m_steamId & 0xffffffff; }

    const uint64_t m_steamId;

    Inventory m_inventory;

    // microtransactions, we only have one going at a time
    uint64_t m_transactionId{};
    std::vector<uint64_t> m_transactionItemIds;

    bool m_matchmakingActive{};
    uint32_t m_matchmakingGameType{ 8 };
    uint32_t m_matchmakingClientVersion{};
    uint64_t m_lastMatchmakingReservation{};
    uint64_t m_lastRewardedReservation{};
};
