#pragma once

#include "gc_const_csgo.h"
#include "item_schema.h"
#include "random.h"

class KeyValue;

using ItemMap = std::unordered_map<uint64_t, CSOEconItem>;

class Inventory
{
public:
    Inventory(uint64_t steamId);
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

    // operation shop (revival): query/spend stars from the player's Operation coin.
    // CanSpendStars never mutates inventory. SpendStars persists and emits the
    // modified coin so Panorama live-refreshes the displayed balance.
    bool CanSpendStars(int cost) const;
    bool SpendStars(int cost, CMsgSOMultipleObjects &update);

private:
    uint32_t AccountId() const;

    bool IsOperationCoinDef(uint32_t defIndex) const;
    uint32_t OperationStars(const CSOEconItem &item) const;
    const CSOEconItem *FindOperationCoin(uint32_t minStars) const;
    CSOEconItem *FindOperationCoin(uint32_t minStars);

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
    ItemSchema m_itemSchema;
    Random m_random;
    uint32_t m_lastHighItemId{};
    ItemMap m_items;
    std::vector<CSOEconDefaultEquippedDefinitionInstanceClient> m_defaultEquips;
};
