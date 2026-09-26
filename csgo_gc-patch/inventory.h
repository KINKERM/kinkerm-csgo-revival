#pragma once

#include "gc_const_csgo.h"
#include "item_schema.h"
#include "random.h"

class KeyValue;

using ItemMap = std::unordered_map<uint64_t, CSOEconItem>;

struct OperationQuestProgressState
{
    uint32_t progress{};
    uint32_t bonusPoints{};
};

class Inventory
{
public:
    Inventory(uint64_t steamId, std::string filePath = "csgo_gc/inventory.txt");
    ~Inventory();

    void BuildCacheSubscription(CMsgSOCacheSubscribed &message, int level, bool server);

    bool EquipItem(uint64_t itemId, uint32_t classId, uint32_t slotId, CMsgSOMultipleObjects &update);

    bool RemoveItem(uint64_t itemId, CMsgSOSingleObject &destroy);

    bool UseItem(uint64_t itemId,
        CMsgSOSingleObject &destroy,
        CMsgSOMultipleObjects &updateMultiple,
        CMsgGCItemCustomizationNotification &notification);

    bool UnlockCrate(uint64_t crateId,
        uint64_t keyId,
        CMsgSOSingleObject &destroyCrate,
        CMsgSOSingleObject &destroyKey,
        CMsgSOSingleObject &newItem,
        CMsgGCItemCustomizationNotification &notification);

    // trade-up contracts (revival addition)
    // consumes the given input skins and produces one skin of the next rarity,
    // with the output float derived from the inputs (normalized average). the
    // created item is written to newItem and the consumed inputs to destroyed.
    // returns false (changing nothing) if the inputs aren't a valid trade-up.
    bool TradeUp(const std::vector<uint64_t> &itemIds,
        std::vector<CMsgSOSingleObject> &destroyed,
        CMsgSOSingleObject &newItem,
        uint64_t &newItemId);

    // trade-up contracts (revival addition) --- "Gold Trade-Up crate"
    // opening the configured crate runs 5 Covert -> gold on the player's own
    // Coverts (picked automatically) and reveals the gold via the unbox flow.
    bool UnlockCrateGoldTradeUp(uint64_t crateId,
        uint64_t keyId,
        std::vector<CMsgSOSingleObject> &destroyed,
        CMsgSOSingleObject &newItem,
        CMsgGCItemCustomizationNotification &notification);

    // "gold only" case (revival addition) --- "Kinkerm's Case"
    // opening the configured crate always rolls a uniform-random gold
    // (knife/glove) from any collection. Nothing is consumed except the crate
    // (and key) itself - no Coverts needed. Admin-grant it like any case.
    bool UnlockGoldOnlyCase(uint64_t crateId,
        uint64_t keyId,
        CMsgSOSingleObject &destroyCrate,
        CMsgSOSingleObject &destroyKey,
        CMsgSOSingleObject &newItem,
        CMsgGCItemCustomizationNotification &notification);

    // def index of an item id, or 0 if it doesn't exist (used to detect the crate)
    uint32_t ItemDefIndex(uint64_t itemId) const;

    bool SetItemPositions(
        const CMsgSetItemPositions &message,
        std::vector<CMsgItemAcknowledged> &acknowledgements,
        CMsgSOMultipleObjects &update);

    bool ApplySticker(const CMsgApplySticker &message,
        CMsgSOSingleObject &update,
        CMsgSOSingleObject &destroy,
        CMsgGCItemCustomizationNotification &notification);

    bool ScrapeSticker(const CMsgApplySticker &message,
        CMsgSOSingleObject &update,
        CMsgSOSingleObject &destroy,
        CMsgGCItemCustomizationNotification &notification);

    bool IncrementKillCountAttribute(uint64_t itemId, uint32_t amount, CMsgSOSingleObject &update);

    bool NameItem(uint64_t nameTagId,
        uint64_t itemId,
        std::string_view name,
        CMsgSOSingleObject &update,
        CMsgSOSingleObject &destroy,
        CMsgGCItemCustomizationNotification &notification);

    bool NameBaseItem(uint64_t nameTagId,
        uint32_t defIndex,
        std::string_view name,
        CMsgSOSingleObject &create,
        CMsgSOSingleObject &destroy,
        CMsgGCItemCustomizationNotification &notification);

    bool RemoveItemName(uint64_t itemId,
        CMsgSOSingleObject &update,
        CMsgSOSingleObject &destroy,
        CMsgGCItemCustomizationNotification &notification);

    // returns the item id and adds the item to the provided CMsgSOMultipleObjects
    // on failure returns 0 and does nothing
    uint64_t PurchaseItem(uint32_t defIndex, std::vector<CMsgSOSingleObject> &update);

    // Operation rewards differ from ordinary store purchases: Valve's collection
    // tokens, dossiers and sticker/patch packs are direct loot-list wrappers.
    // Resolve those wrappers to the actual random reward before it reaches inventory.
    uint64_t PurchaseOperationReward(uint32_t defIndex, std::vector<CMsgSOSingleObject> &update);

    // Native end-of-match rewards. Creates the inventory SO plus the 9137
    // preview payload used by CS:GO's end-match item reveal.
    bool CreateMatchDrop(uint32_t defIndex,
        bool resolveDirectLoot,
        UnacknowledgedType unacknowledgedType,
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool CreateRandomCaseMatchDrop(
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool CreateRareLegacyStickerCapsuleMatchDrop(
        uint32_t oneIn,
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool CreateRareLegacySouvenirPackageMatchDrop(
        uint32_t oneIn,
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool CreateWeeklyLevelReward(
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool AddMatchPlaytimeAndCreateCaseDrop(uint32_t seconds,
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool CreateRandomCollectionMatchDrop(
        const std::vector<std::string_view> &collectionNames,
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);
    bool CreateRareCollectionBonusMatchDrop(
        const std::vector<std::string_view> &collectionNames,
        uint32_t oneIn,
        CMsgSOSingleObject &create,
        CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification);

    // Import an exact SO Create produced by the dedicated server. This keeps
    // the local persistent inventory identical to the item shown by the native
    // server-side end-match reveal.
    bool ImportServerCreatedItem(const CMsgSOSingleObject &create);

    // operation shop (revival): query/spend stars from the player's Operation coin.
    // CanSpendStars never mutates inventory. SpendStars persists and emits the
    // modified coin so Panorama live-refreshes the displayed balance.
    bool CanSpendStars(int cost) const;
    bool SpendStars(int cost, CMsgSOMultipleObjects &update);

    // Apply match-end quest progress from the game server. Mission-earned stars
    // increase both the spendable wallet and the non-spendable Operation tier
    // progress, while respecting each Riptide mission card's weekly star cap.
    bool ApplyOperationQuestProgress(uint32_t questId,
        int normalPointsEarned,
        int bonusPointsEarned,
        CMsgSOMultipleObjects &update);

    bool SetOperationMissionCard(uint32_t season,
        uint32_t missionCardId,
        CMsgSOMultipleObjects &update);

    // Persistent CS:GO profile progression. One profile rank is 5000 XP and
    // rank 40 is the pre-service-medal cap used by the legacy client.
    uint32_t ProfileLevel() const { return m_profileLevel; }
    uint32_t ProfileXp() const { return m_profileXp; }
    RankId CompetitiveRank() const { return m_competitiveRank; }
    uint32_t CompetitiveWins() const { return m_competitiveWins; }
    uint32_t ProfileWeek() const { return m_profileWeek; }
    uint32_t WeeklyBaseXp() const { return m_weeklyBaseXp; }
    bool WeeklyLevelRewardClaimed() const { return m_weeklyLevelRewardClaimed; }
    uint32_t CasePlaytimeSeconds() const { return m_casePlaytimeSeconds; }
    uint32_t CaseDropsThisWeek() const { return m_caseDropsThisWeek; }
    uint32_t NextCaseDropSeconds() const { return m_nextCaseDropSeconds; }
    int32_t CompetitiveRating() const { return m_competitiveRating; }
    uint32_t CompetitiveMatches() const { return m_competitiveMatches; }
    bool AddProfileXp(uint32_t amount, uint32_t *levelsGained = nullptr);
    uint32_t ApplyWeeklyProfileXp(uint32_t baseXp, uint32_t *levelsGained = nullptr);
    bool ApplyCompetitiveMatchResult(bool won, bool tied);
    bool ImportRevivalProfile(
        uint32_t level, uint32_t xp,
        uint32_t profileWeek, uint32_t weeklyBaseXp,
        bool weeklyLevelRewardClaimed,
        uint32_t casePlaytimeSeconds, uint32_t caseDropsThisWeek,
        uint32_t nextCaseDropSeconds,
        RankId competitiveRank, uint32_t competitiveWins,
        int32_t competitiveRating, uint32_t competitiveMatches);
    void BuildProfilePersonaUpdate(CMsgSOMultipleObjects &update);

private:
    uint32_t AccountId() const;
    uint32_t CurrentProfileWeek() const;
    void RefreshProfileWeek();

    bool IsOperationCoinDef(uint32_t defIndex) const;
    uint32_t OperationStars(const CSOEconItem &item) const;
    const CSOEconItem *FindOperationCoin(uint32_t minStars) const;
    CSOEconItem *FindOperationCoin(uint32_t minStars);
    uint32_t OperationCoinDefForEarnedStars() const;
    uint32_t OperationMissionCardRawStars(const OperationMissionCard &card) const;
    void AddOperationSeasonalState(CMsgSOMultipleObjects &update);
    void AddOperationQuestState(uint32_t questId, CMsgSOMultipleObjects &update);

    // trade-up contracts (revival addition): finds 5 Covert skins from the same
    // collection and same StatTrak state; returns their ids in `out`, or false
    bool SelectCovertsForTradeUp(std::vector<uint64_t> &out) const;

    // allocates an empty item, sets id and account_id fields
    // pass zero as highItemId to generate a new one
    CSOEconItem &AllocateItem(uint32_t highItemId);

    // create a new item of a specific type
    CSOEconItem &CreateItem(const CSOEconItem &copyFrom);
    CSOEconItem &CreateItem(uint32_t defIndex, ItemOrigin origin, UnacknowledgedType unacknowledgedType);

    void ReadFromFile();
    void ReadItem(const KeyValue &itemKey, CSOEconItem &item) const;

    void WriteToFile() const;
    void WriteItem(KeyValue &itemKey, const CSOEconItem &item) const;

    // helper, only called via EquipItem
    bool UnequipItem(uint64_t itemId, CMsgSOMultipleObjects &update);
    void UnequipItem(uint32_t classId, uint32_t slotId, CMsgSOMultipleObjects &update);

    void DestroyItem(ItemMap::iterator iterator, CMsgSOSingleObject &message);

    // move this to the item schema maybe?
    void ItemToPreviewDataBlock(const CSOEconItem &item, CEconItemPreviewDataBlock &block);

    // helpers for serializing items to CMsgSOMultipleObjects and CMsgSOSingleObject
    void AddToMultipleObjects(CMsgSOMultipleObjects &message, SOTypeId type, const google::protobuf::MessageLite &object);
    void ToSingleObject(CMsgSOSingleObject &message, SOTypeId type, const google::protobuf::MessageLite &object);

    // helpers for above..
    void AddToMultipleObjects(CMsgSOMultipleObjects &message, const CSOEconItem &object)
    {
        AddToMultipleObjects(message, SOTypeItem, object);
    }

    void ToSingleObject(CMsgSOSingleObject &message, const CSOEconItem &object)
    {
        ToSingleObject(message, SOTypeItem, object);
    }

    void AddToMultipleObjects(CMsgSOMultipleObjects &message, const CSOEconDefaultEquippedDefinitionInstanceClient &object)
    {
        AddToMultipleObjects(message, SOTypeDefaultEquippedDefinitionInstanceClient, object);
    }

    void ToSingleObject(CMsgSOSingleObject &message, const CSOEconDefaultEquippedDefinitionInstanceClient &object)
    {
        ToSingleObject(message, SOTypeDefaultEquippedDefinitionInstanceClient, object);
    }

    const uint64_t m_steamId;
    std::string m_filePath;
    ItemSchema m_itemSchema;
    Random m_random;
    uint32_t m_lastHighItemId{};
    ItemMap m_items;
    std::vector<CSOEconDefaultEquippedDefinitionInstanceClient> m_defaultEquips;

    uint32_t m_profileLevel{ 1 };
    uint32_t m_profileXp{};
    RankId m_competitiveRank{ RankNone };
    uint32_t m_competitiveWins{};

    // Riptide-era weekly progression/drop state.
    uint32_t m_profileWeek{};
    uint32_t m_weeklyBaseXp{};
    bool m_weeklyLevelRewardClaimed{};
    uint32_t m_casePlaytimeSeconds{};
    uint32_t m_caseDropsThisWeek{};
    uint32_t m_nextCaseDropSeconds{};

    // Valve's exact hidden skill-group formula is not public. Keep a persistent
    // internal rating while exposing the real 0..18 legacy rank ids.
    int32_t m_competitiveRating{ 1200 };
    uint32_t m_competitiveMatches{};

    // Persistent Operation Riptide progress. Spendable stars remain on the coin
    // item attribute; earnedStars is deliberately separate because purchased
    // star packs must not advance the operation coin/tier track.
    uint32_t m_operationEarnedStars{};
    uint32_t m_operationMissionsCompleted{};
    uint32_t m_operationMissionId{};
    uint32_t m_operationSeasonPassTime{};
    std::unordered_map<uint32_t, OperationQuestProgressState> m_operationQuestProgress;
};
