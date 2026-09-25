#pragma once

#include "gc_shared.h"
#include <string>

class ServerGC final : public SharedGC
{
public:
    ServerGC();
    ~ServerGC();

private:
    void HandleEvent(GCEvent type, uint64_t id, const std::vector<uint8_t> &buffer) override;
    void HandleIdle() override;

    // event handlers
    void HandleMessage(uint32_t type, const void *data, uint32_t size);
    void HandleNetMessage(uint64_t steamId, const void *data, uint32_t size);
    void HandleClientSOCacheUnsubscribe(uint64_t steamId);

    void SendServerWelcome();
    void SendMatchmakingReservation();
    void MatchmakingReservationResponse(GCMessageRead &messageRead);
    void IncrementKillCountAttribute(GCMessageRead &messageRead);
    void MatchEndRunRewardDrops(GCMessageRead &messageRead);

    bool m_sentWelcome{};
    bool m_sentReservation{};
    uint32_t m_reservationIdleTicks{};
    uint32_t m_queueReservationRefreshTicks{};
    std::string m_lastReservationSignature;
    std::string m_lastQueueReservationPayload;
};
