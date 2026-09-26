#include "stdafx.h"
#include "inventory.h"
#include "case_opening.h"
#include "config.h"
#include "gc_const.h"
#include "keyvalue.h"
#include "random.h"

#include <algorithm>

constexpr const char *InventoryFilePath = "csgo_gc/inventory.txt";

// mikkotodo actual versioning
constexpr uint64_t InventoryVersion = 7523377975160828514;

// mix the account id into item ids to avoid collisions in multiplayer games
inline uint64_t ComposeItemId(uint32_t accountId, uint32_t highItemId)
{
    uint64_t low = accountId;
    uint64_t high = highItemId;
    return low | (high << 32);
}

inline uint32_t HighItemId(uint64_t itemId)
{
    return (itemId >> 32);
}

// helper, see ItemIdDefaultItemMask for more information
inline bool IsDefaultItemId(uint64_t itemId, uint32_t &defIndex, uint32_t &paintKitIndex)
{
    if ((itemId & ItemIdDefaultItemMask) == ItemIdDefaultItemMask)
    {
        defIndex = itemId & 0xffff;
        paintKitIndex = (itemId >> 16) & 0xffff;
        return true;
    }

    return false;
}

Inventory::Inventory(uint64_t steamId, std::string filePath)
    : m_steamId{ steamId }
    , m_filePath{ filePath }
{
    m_profileLevel = static_cast<uint32_t>(std::max(GetConfig().Level(), 1));
    m_profileXp = static_cast<uint32_t>(std::max(GetConfig().Xp(), 0));
    m_competitiveRank = GetConfig().CompetitiveRank();
    m_competitiveWins = static_cast<uint32_t>(std::max(GetConfig().CompetitiveWins(), 0));
    if (m_competitiveRank != RankNone)
    {
        m_competitiveRating = 600 + (static_cast<int32_t>(m_competitiveRank) - 1) * 100;
    }
    ReadFromFile();
}

Inventory::~Inventory()
{
    WriteToFile();
}

void Inventory::AddToMultipleObjects(CMsgSOMultipleObjects &message, SOTypeId type, const google::protobuf::MessageLite &object)
{
    if (!message.has_version())
    {
        assert(!message.has_owner_soid());
        message.set_version(InventoryVersion);
        message.mutable_owner_soid()->set_type(SoIdTypeSteamId);
        message.mutable_owner_soid()->set_id(m_steamId);
    }
    else
    {
        assert(message.has_owner_soid());
    }

    CMsgSOMultipleObjects_SingleObject *single = message.add_objects_modified();
    single->set_type_id(type);
    single->set_object_data(object.SerializeAsString());
}

void Inventory::ToSingleObject(CMsgSOSingleObject &message, SOTypeId type, const google::protobuf::MessageLite &object)
{
    assert(!message.has_owner_soid());
    assert(!message.has_version());
    assert(!message.has_type_id());
    assert(!message.has_object_data());

    message.set_version(InventoryVersion);
    message.mutable_owner_soid()->set_type(SoIdTypeSteamId);
    message.mutable_owner_soid()->set_id(m_steamId);

    message.set_type_id(type);
    message.set_object_data(object.SerializeAsString());
}

uint32_t Inventory::AccountId() const
{
    return m_steamId & 0xffffffff;
}

uint32_t Inventory::CurrentProfileWeek() const
{
    // Legacy CS:GO's XP/rank-up reward reset was mid-week. Shift the Unix
    // Thursday epoch by one day so integer weeks roll over Wednesday 00:00 UTC.
    const uint64_t now = static_cast<uint64_t>(time(nullptr));
    return static_cast<uint32_t>((now + 86400ull) / 604800ull);
}

void Inventory::RefreshProfileWeek()
{
    const uint32_t week = CurrentProfileWeek();
    if (m_profileWeek != week)
    {
        m_profileWeek = week;
        m_weeklyBaseXp = 0;
        m_weeklyLevelRewardClaimed = false;
        m_casePlaytimeSeconds = 0;
        m_caseDropsThisWeek = 0;
        m_nextCaseDropSeconds = m_random.Integer<uint32_t>(2u * 3600u, 4u * 3600u);
        WriteToFile();
        Platform::Print("progression: weekly state reset (week %u, first case target %us)\n",
            week, m_nextCaseDropSeconds);
    }
    else if (!m_nextCaseDropSeconds)
    {
        m_nextCaseDropSeconds = m_random.Integer<uint32_t>(2u * 3600u, 4u * 3600u);
    }
}

bool Inventory::IsOperationCoinDef(uint32_t defIndex) const
{
    for (uint32_t coinDef : GetConfig().OperationCoinDefs())
    {
        if (coinDef == defIndex)
        {
            return true;
        }
    }

    return false;
}

uint32_t Inventory::OperationStars(const CSOEconItem &item) const
{
    const uint32_t starAttr = GetConfig().OperationStarAttribute();
    for (const CSOEconItemAttribute &attribute : item.attribute())
    {
        if (attribute.def_index() == starAttr)
        {
            return m_itemSchema.AttributeUint32(&attribute);
        }
    }

    return 0;
}

const CSOEconItem *Inventory::FindOperationCoin(uint32_t minStars) const
{
    const CSOEconItem *best = nullptr;
    uint32_t bestStars = 0;

    for (const auto &pair : m_items)
    {
        const CSOEconItem &item = pair.second;
        if (!IsOperationCoinDef(item.def_index()))
        {
            continue;
        }

        const uint32_t stars = OperationStars(item);
        if (stars < minStars)
        {
            continue;
        }

        // Keep one canonical wallet even if a broken/old inventory contains
        // multiple Operation coins: always use the coin with the most stars.
        if (!best || stars > bestStars)
        {
            best = &item;
            bestStars = stars;
        }
    }

    return best;
}

CSOEconItem *Inventory::FindOperationCoin(uint32_t minStars)
{
    return const_cast<CSOEconItem *>(
        static_cast<const Inventory *>(this)->FindOperationCoin(minStars));
}

uint32_t Inventory::OperationCoinDefForEarnedStars() const
{
    const std::vector<uint32_t> &defs = GetConfig().OperationCoinDefs();
    if (defs.empty())
    {
        return 0;
    }

    size_t index = 0;
    if (m_operationEarnedStars >= 100)
        index = 3;
    else if (m_operationEarnedStars >= 66)
        index = 2;
    else if (m_operationEarnedStars >= 33)
        index = 1;

    if (index >= defs.size())
    {
        index = defs.size() - 1;
    }
    return defs[index];
}

uint32_t Inventory::OperationMissionCardRawStars(const OperationMissionCard &card) const
{
    uint64_t total = 0;

    for (uint32_t questId : card.questIds)
    {
        const QuestDefinition *quest = m_itemSchema.GetQuestDefinition(questId);
        if (!quest)
        {
            continue;
        }

        auto stateIt = m_operationQuestProgress.find(questId);
        uint32_t progress = (stateIt == m_operationQuestProgress.end()) ? 0 : stateIt->second.progress;

        for (uint32_t threshold : quest->thresholds)
        {
            if (progress >= threshold)
            {
                total += quest->operationalPoints;
            }
        }
    }

    return total > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(total);
}

void Inventory::AddOperationSeasonalState(CMsgSOMultipleObjects &update)
{
    CSOAccountSeasonalOperation operation;
    operation.set_season_value(GetConfig().OperationSeason());
    operation.set_tier_unlocked(m_operationEarnedStars);
    operation.set_premium_tiers(FindOperationCoin(0) ? 1 : 0);
    operation.set_mission_id(m_operationMissionId);
    operation.set_missions_completed(m_operationMissionsCompleted);

    const CSOEconItem *coin = FindOperationCoin(0);
    operation.set_redeemable_balance(coin ? OperationStars(*coin) : 0);
    operation.set_season_pass_time(
        coin ? (m_operationSeasonPassTime ? m_operationSeasonPassTime : 1) : 0);

    AddToMultipleObjects(update, static_cast<SOTypeId>(41), operation);
}

void Inventory::AddOperationQuestState(uint32_t questId, CMsgSOMultipleObjects &update)
{
    const QuestDefinition *quest = m_itemSchema.GetQuestDefinition(questId);
    if (!quest)
    {
        return;
    }

    auto stateIt = m_operationQuestProgress.find(questId);
    OperationQuestProgressState state;
    if (stateIt != m_operationQuestProgress.end())
    {
        state = stateIt->second;
    }

    CSOQuestProgress progress;
    progress.set_questid(questId);
    progress.set_points_remaining(state.progress >= quest->Goal() ? 0 : quest->Goal() - state.progress);
    progress.set_bonus_points(state.bonusPoints);
    AddToMultipleObjects(update, static_cast<SOTypeId>(46), progress);
}

CSOEconItem &Inventory::AllocateItem(uint32_t highItemId)
{
    // Players fuck up their inventory files constantly and end up with item id collisions...
    // This doesn't return until the item id is unique for this session, try with the provided
    // item id first, if it's invalid or already in use increment it

    if (!highItemId)
    {
        m_lastHighItemId++;
        highItemId = m_lastHighItemId;
    }

    for (;; highItemId++)
    {
        uint64_t itemId = ComposeItemId(AccountId(), highItemId);
        if ((itemId & ItemIdDefaultItemMask) == ItemIdDefaultItemMask)
        {
            // would be interpreted as a default item (it's not)
            assert(false);
            // shit error handling
            continue;
        }

        auto [it, inserted] = m_items.try_emplace(itemId);
        if (!inserted)
        {
            // item id collision
            assert(false);
            continue;
        }

        if (highItemId > m_lastHighItemId)
        {
            m_lastHighItemId = highItemId;
        }

        // ok
        CSOEconItem &item = it->second;

        item.set_id(itemId);
        item.set_account_id(AccountId());

        return item;
    }
}

CSOEconItem &Inventory::CreateItem(const CSOEconItem &copyFrom)
{
    CSOEconItem &item = AllocateItem(0);

    // shitty but what can you do
    uint64_t itemId = item.id();
    uint32_t accountId = item.account_id();

    item = copyFrom;

    item.set_id(itemId);
    item.set_account_id(accountId);

    return item;
}

CSOEconItem &Inventory::CreateItem(uint32_t defIndex, ItemOrigin origin, UnacknowledgedType unacknowledgedType)
{
    CSOEconItem &item = AllocateItem(0);
    m_itemSchema.CreateItem(defIndex, origin, unacknowledgedType, item);
    return item;
}

void Inventory::ReadFromFile()
{
    KeyValue inventoryKey{ "inventory" };
    if (!inventoryKey.ParseFromFile(m_filePath.c_str()))
    {
        return;
    }

    const KeyValue *itemsKey = inventoryKey.GetSubkey("items");
    if (itemsKey)
    {
        m_items.reserve(itemsKey->SubkeyCount());

        for (const KeyValue &itemKey : *itemsKey)
        {
            uint32_t highItemId = FromString<uint32_t>(itemKey.Name());
            CSOEconItem &item = AllocateItem(highItemId);
            ReadItem(itemKey, item);
        }
    }

    const KeyValue *defaultEquipsKey = inventoryKey.GetSubkey("default_equips");
    if (defaultEquipsKey)
    {
        m_defaultEquips.reserve(defaultEquipsKey->SubkeyCount());

        for (const KeyValue &defaultEquipKey : *defaultEquipsKey)
        {
            CSOEconDefaultEquippedDefinitionInstanceClient &defaultEquip = m_defaultEquips.emplace_back();
            defaultEquip.set_account_id(AccountId());
            defaultEquip.set_item_definition(FromString<uint32_t>(defaultEquipKey.Name()));
            defaultEquip.set_class_id(defaultEquipKey.GetNumber<uint32_t>("class_id"));
            defaultEquip.set_slot_id(defaultEquipKey.GetNumber<uint32_t>("slot_id"));
        }
    }

    const KeyValue *profileKey = inventoryKey.GetSubkey("revival_profile");
    if (profileKey)
    {
        m_profileLevel = std::clamp(profileKey->GetNumber<uint32_t>("level", m_profileLevel), 1u, 40u);
        m_profileXp = profileKey->GetNumber<uint32_t>("xp", m_profileXp);
        if (m_profileXp >= 5000)
            m_profileXp %= 5000;
        const uint32_t rank = profileKey->GetNumber<uint32_t>(
            "competitive_rank", static_cast<uint32_t>(m_competitiveRank));
        m_competitiveRank = static_cast<RankId>(std::min<uint32_t>(rank, RankGlobalElite));
        m_competitiveWins = profileKey->GetNumber<uint32_t>("competitive_wins", m_competitiveWins);
        m_profileWeek = profileKey->GetNumber<uint32_t>("profile_week", 0);
        m_weeklyBaseXp = profileKey->GetNumber<uint32_t>("weekly_base_xp", 0);
        m_weeklyLevelRewardClaimed =
            profileKey->GetNumber<uint32_t>("weekly_level_reward_claimed", 0) != 0;
        m_casePlaytimeSeconds = profileKey->GetNumber<uint32_t>("case_playtime_seconds", 0);
        m_caseDropsThisWeek = profileKey->GetNumber<uint32_t>("case_drops_this_week", 0);
        m_nextCaseDropSeconds = profileKey->GetNumber<uint32_t>("next_case_drop_seconds", 0);
        m_competitiveRating = std::clamp(
            profileKey->GetNumber<int32_t>("competitive_rating", m_competitiveRating),
            600, 2300);
        m_competitiveMatches =
            profileKey->GetNumber<uint32_t>("competitive_matches", m_competitiveMatches);
    }

    const KeyValue *operationKey = inventoryKey.GetSubkey("operation_riptide");
    if (operationKey)
    {
        m_operationEarnedStars = operationKey->GetNumber<uint32_t>("earned_stars", 0);
        m_operationMissionsCompleted = operationKey->GetNumber<uint32_t>("missions_completed", 0);
        m_operationMissionId = operationKey->GetNumber<uint32_t>("mission_id", 0);
        m_operationSeasonPassTime = operationKey->GetNumber<uint32_t>("season_pass_time", 0);

        const KeyValue *questsKey = operationKey->GetSubkey("quests");
        if (questsKey)
        {
            for (const KeyValue &questKey : *questsKey)
            {
                uint32_t questId = FromString<uint32_t>(questKey.Name());
                if (!questId || !m_itemSchema.GetQuestDefinition(questId))
                {
                    continue;
                }

                OperationQuestProgressState state;
                state.progress = questKey.GetNumber<uint32_t>("progress", 0);
                state.bonusPoints = questKey.GetNumber<uint32_t>("bonus_points", 0);
                state.repeatableRounds = questKey.GetNumber<uint32_t>(
                    "repeatable_rounds", 0);
                m_operationQuestProgress[questId] = state;
            }
        }
    }
    RefreshProfileWeek();
}

void Inventory::ReadItem(const KeyValue &itemKey, CSOEconItem &item) const
{
    // id and account_id were set by CreateItem
    item.set_inventory(itemKey.GetNumber<uint32_t>("inventory"));
    item.set_def_index(itemKey.GetNumber<uint32_t>("def_index"));
    //item.set_quantity(itemKey.GetNumber<uint32_t>("quantity"));
    item.set_quantity(1);
    item.set_level(itemKey.GetNumber<uint32_t>("level"));
    item.set_quality(itemKey.GetNumber<uint32_t>("quality"));
    item.set_flags(itemKey.GetNumber<uint32_t>("flags"));
    item.set_origin(itemKey.GetNumber<uint32_t>("origin"));

    std::string_view name = itemKey.GetString("custom_name");
    if (name.size())
    {
        item.set_custom_name(std::string{ name });
    }

    //std::string_view desc = itemKey.GetString("custom_desc");
    //if (desc.size())
    //{
    //    item.set_custom_desc(std::string{ desc });
    //}

    item.set_in_use(itemKey.GetNumber<int>("in_use"));
    //item.set_style(itemKey.GetNumber<uint32_t>("style"));
    //item.set_original_id(itemKey.GetNumber<uint64_t>("original_id"));
    item.set_rarity(itemKey.GetNumber<uint32_t>("rarity"));

    const KeyValue *attributesKey = itemKey.GetSubkey("attributes");
    if (attributesKey)
    {
        for (const KeyValue &attributeKey : *attributesKey)
        {
            CSOEconItemAttribute *attribute = item.add_attribute();

            uint32_t defIndex = FromString<uint32_t>(attributeKey.Name());
            attribute->set_def_index(defIndex);
            m_itemSchema.SetAttributeString(attribute, attributeKey.String());
        }
    }

    const KeyValue *equippedStateKey = itemKey.GetSubkey("equipped_state");
    if (equippedStateKey)
    {
        for (const KeyValue &equippedKey : *equippedStateKey)
        {
            CSOEconItemEquipped *equipped = item.add_equipped_state();
            equipped->set_new_class(FromString<uint32_t>(equippedKey.Name()));
            equipped->set_new_slot(FromString<uint32_t>(equippedKey.String()));
        }
    }
}

void Inventory::WriteToFile() const
{
    KeyValue inventoryKey{ "inventory" };

    {
        KeyValue &itemsKey = inventoryKey.AddSubkey("items");

        for (const auto &pair : m_items)
        {
            const CSOEconItem &item = pair.second;
            KeyValue &itemKey = itemsKey.AddSubkey(std::to_string(HighItemId(item.id())));
            WriteItem(itemKey, item);
        }
    }

    {
        KeyValue &defaultEquipsKey = inventoryKey.AddSubkey("default_equips");

        for (const CSOEconDefaultEquippedDefinitionInstanceClient &defaultEquip : m_defaultEquips)
        {
            KeyValue &defaultEquipKey = defaultEquipsKey.AddSubkey(std::to_string(defaultEquip.item_definition()));
            defaultEquipKey.AddNumber("class_id", defaultEquip.class_id());
            defaultEquipKey.AddNumber("slot_id", defaultEquip.slot_id());
        }
    }

    {
        KeyValue &profileKey = inventoryKey.AddSubkey("revival_profile");
        profileKey.AddNumber("level", m_profileLevel);
        profileKey.AddNumber("xp", m_profileXp);
        profileKey.AddNumber("competitive_rank", static_cast<uint32_t>(m_competitiveRank));
        profileKey.AddNumber("competitive_wins", m_competitiveWins);
        profileKey.AddNumber("profile_week", m_profileWeek);
        profileKey.AddNumber("weekly_base_xp", m_weeklyBaseXp);
        profileKey.AddNumber("weekly_level_reward_claimed", m_weeklyLevelRewardClaimed ? 1 : 0);
        profileKey.AddNumber("case_playtime_seconds", m_casePlaytimeSeconds);
        profileKey.AddNumber("case_drops_this_week", m_caseDropsThisWeek);
        profileKey.AddNumber("next_case_drop_seconds", m_nextCaseDropSeconds);
        profileKey.AddNumber("competitive_rating", m_competitiveRating);
        profileKey.AddNumber("competitive_matches", m_competitiveMatches);
    }

    {
        KeyValue &operationKey = inventoryKey.AddSubkey("operation_riptide");
        operationKey.AddNumber("season", GetConfig().OperationSeason());
        operationKey.AddNumber("earned_stars", m_operationEarnedStars);
        operationKey.AddNumber("missions_completed", m_operationMissionsCompleted);
        operationKey.AddNumber("mission_id", m_operationMissionId);
        operationKey.AddNumber("season_pass_time", m_operationSeasonPassTime);

        KeyValue &questsKey = operationKey.AddSubkey("quests");
        for (const auto &pair : m_operationQuestProgress)
        {
            KeyValue &questKey = questsKey.AddSubkey(std::to_string(pair.first));
            questKey.AddNumber("progress", pair.second.progress);
            questKey.AddNumber("bonus_points", pair.second.bonusPoints);
            questKey.AddNumber("repeatable_rounds", pair.second.repeatableRounds);
        }
    }

    inventoryKey.WriteToFile(m_filePath.c_str());
}

void Inventory::WriteItem(KeyValue &itemKey, const CSOEconItem &item) const
{
    itemKey.AddNumber("inventory", item.inventory());
    itemKey.AddNumber("def_index", item.def_index());
    //itemKey.AddNumber("quantity", item.quantity());
    itemKey.AddNumber("level", item.level());
    itemKey.AddNumber("quality", item.quality());
    itemKey.AddNumber("flags", item.flags());
    itemKey.AddNumber("origin", item.origin());

    itemKey.AddString("custom_name", item.custom_name());
    //itemKey.AddString("custom_desc", item.custom_desc());

    itemKey.AddNumber("in_use", item.in_use());
    //itemKey.AddNumber("style", item.style());
    //itemKey.AddNumber("original_id", item.original_id());
    itemKey.AddNumber("rarity", item.rarity());

    KeyValue &attributesKey = itemKey.AddSubkey("attributes");
    for (const CSOEconItemAttribute &attribute : item.attribute())
    {
        std::string name = std::to_string(attribute.def_index());
        std::string value = m_itemSchema.AttributeString(&attribute);
        attributesKey.AddString(name, value);
    }

    KeyValue &equippedStateKey = itemKey.AddSubkey("equipped_state");
    for (const CSOEconItemEquipped &equip : item.equipped_state())
    {
        equippedStateKey.AddNumber(std::to_string(equip.new_class()), equip.new_slot());
    }
}

void Inventory::BuildCacheSubscription(CMsgSOCacheSubscribed &message, int level, bool server)
{
    message.set_version(InventoryVersion);
    message.mutable_owner_soid()->set_type(SoIdTypeSteamId);
    message.mutable_owner_soid()->set_id(m_steamId);

    {
        CMsgSOCacheSubscribed_SubscribedType *object = message.add_objects();
        object->set_type_id(SOTypeItem);

        for (const auto &pair : m_items)
        {
            if (server && !pair.second.equipped_state_size())
            {
                continue;
            }

            object->add_object_data(pair.second.SerializeAsString());
        }
    }

    {
        CSOPersonaDataPublic personaData;
        personaData.set_player_level(m_profileLevel);
        personaData.set_elevated_state(true);

        CMsgSOCacheSubscribed_SubscribedType *object = message.add_objects();
        object->set_type_id(SOTypePersonaDataPublic);
        object->add_object_data(personaData.SerializeAsString());
    }

    if (!server)
    {
        CSOEconGameAccountClient accountClient;
        accountClient.set_additional_backpack_slots(0);
        accountClient.set_bonus_xp_timestamp_refresh(static_cast<uint32_t>(time(nullptr)));
        accountClient.set_bonus_xp_usedflags(16); // caught cheater lobbies, overwatch bonus etc
        accountClient.set_elevated_state(ElevatedStatePrime);
        accountClient.set_elevated_timestamp(ElevatedStatePrime); // is this actually 5????

        CMsgSOCacheSubscribed_SubscribedType *object = message.add_objects();
        object->set_type_id(SOTypeGameAccountClient);
        object->add_object_data(accountClient.SerializeAsString());
    }

    // Operation state is useful to both Panorama and the connected game server.
    // The server needs the selected mission card to evaluate the quest
    // expressions and produce MatchEndRunRewardDrops progress.
    {
        CSOAccountSeasonalOperation operation;
        operation.set_season_value(GetConfig().OperationSeason());
        operation.set_tier_unlocked(m_operationEarnedStars);
        operation.set_premium_tiers(FindOperationCoin(0) ? 1 : 0);
        operation.set_mission_id(m_operationMissionId);
        operation.set_missions_completed(m_operationMissionsCompleted);

        const CSOEconItem *coin = FindOperationCoin(0);
        operation.set_redeemable_balance(coin ? OperationStars(*coin) : 0);
        operation.set_season_pass_time(
            coin ? (m_operationSeasonPassTime ? m_operationSeasonPassTime : 1) : 0);

        CMsgSOCacheSubscribed_SubscribedType *operationObject = message.add_objects();
        operationObject->set_type_id(41); // CSOAccountSeasonalOperation
        operationObject->add_object_data(operation.SerializeAsString());

        if (!m_operationQuestProgress.empty())
        {
            CMsgSOCacheSubscribed_SubscribedType *questObjects = message.add_objects();
            questObjects->set_type_id(46); // CSOQuestProgress

            for (const auto &pair : m_operationQuestProgress)
            {
                const QuestDefinition *quest = m_itemSchema.GetQuestDefinition(pair.first);
                if (!quest)
                {
                    continue;
                }

                CSOQuestProgress progress;
                progress.set_questid(pair.first);
                progress.set_points_remaining(
                    pair.second.progress >= quest->Goal() ? 0 : quest->Goal() - pair.second.progress);
                progress.set_bonus_points(pair.second.bonusPoints);
                questObjects->add_object_data(progress.SerializeAsString());
            }
        }
    }

    {
        CMsgSOCacheSubscribed_SubscribedType *object = message.add_objects();
        object->set_type_id(SOTypeDefaultEquippedDefinitionInstanceClient);

        for (const CSOEconDefaultEquippedDefinitionInstanceClient &defaultEquip : m_defaultEquips)
        {
            object->add_object_data(defaultEquip.SerializeAsString());
        }
    }
}

// mikkotodo move
constexpr uint32_t SlotUneqip = 0xffff;
constexpr uint64_t ItemIdInvalid = 0;

// yes this function is inefficent!!! but i think that makes it more clear
// also i think this is the way valve gc does it???? can't remember
bool Inventory::EquipItem(uint64_t itemId, uint32_t classId, uint32_t slotId, CMsgSOMultipleObjects &update)
{
    if (slotId == SlotUneqip)
    {
        // unequipping a specific item from all slots
        const bool changed = UnequipItem(itemId, update);
        if (changed)
            WriteToFile();
        return changed;
    }

    // mikkotodo cleanup, old junk
    assert(itemId);
    assert(itemId != UINT64_MAX); // probably an old csgo thing

    if (itemId == ItemIdInvalid)
    {
        // unequip from this slot, itemid not provided so nothing gets equipped
        UnequipItem(classId, slotId, update);
        WriteToFile();
        return true;
    }

    uint32_t defIndex, paintKitIndex;
    if (IsDefaultItemId(itemId, defIndex, paintKitIndex))
    {
        // if an item is equipped in this slot, unequip it first
        UnequipItem(classId, slotId, update);

        Platform::Print("EquipItem def %u class %d slot %d\n", defIndex, classId, slotId);

        CSOEconDefaultEquippedDefinitionInstanceClient &defaultEquip = m_defaultEquips.emplace_back();
        defaultEquip.set_account_id(AccountId());
        defaultEquip.set_item_definition(defIndex);
        defaultEquip.set_class_id(classId);
        defaultEquip.set_slot_id(slotId);

        AddToMultipleObjects(update, defaultEquip);
        WriteToFile();

        return true;
    }
    else
    {
        auto it = m_items.find(itemId);
        if (it == m_items.end())
        {
            Platform::Print("EquipItem: no such item %llu!!!!\n", itemId);
            return false; // didn't modify anything
        }

        // if an item is equipped in this slot, unequip it first
        UnequipItem(classId, slotId, update);

        Platform::Print("EquipItem %llu class %d slot %d\n", itemId, classId,
            slotId);

        CSOEconItem &item = it->second;

        CSOEconItemEquipped *equippedState = item.add_equipped_state();
        equippedState->set_new_class(classId);
        equippedState->set_new_slot(slotId);

        AddToMultipleObjects(update, item);
        WriteToFile();

        return true;
    }
}

bool Inventory::RemoveItem(uint64_t itemId, CMsgSOSingleObject &response)
{
    auto it = m_items.find(itemId);
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    DestroyItem(it, response);
    return true;
}

bool Inventory::UseItem(uint64_t itemId,
    CMsgSOSingleObject &destroy,
    CMsgSOMultipleObjects &updateMultiple,
    CMsgGCItemCustomizationNotification &notification)
{
    auto it = m_items.find(itemId);
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    const uint32_t defIndex = it->second.def_index();

    // Operation Riptide pass activation. The original Panorama flow uses
    // InventoryAPI.UseTool(pass, ''), which lands here. The stock csgo_gc only
    // supported sealed graffiti, so the pass could never become an Operation
    // coin and users needed admin.py grant-coin. Make the real UI flow work:
    // consume the pass and create the configured bronze Operation coin wallet.
    if (defIndex == GetConfig().OperationPassDef())
    {
        if (FindOperationCoin(0))
        {
            Platform::Print("operation: pass %llu not consumed - player already owns a coin\n", itemId);
            return false;
        }

        CSOEconItem &coin = AllocateItem(0);
        const uint64_t coinId = coin.id();
        if (!m_itemSchema.CreateItem(GetConfig().OperationActivationCoinDef(),
            ItemOriginPurchased, UnacknowledgedPurchased, coin))
        {
            m_items.erase(coinId);
            Platform::Print("operation: could not create activation coin def %u\n",
                GetConfig().OperationActivationCoinDef());
            return false;
        }

        CSOEconItemAttribute *starAttribute = coin.add_attribute();
        starAttribute->set_def_index(GetConfig().OperationStarAttribute());
        if (!m_itemSchema.SetAttributeUint32(starAttribute, 0))
        {
            m_items.erase(coinId);
            Platform::Print("operation: could not initialize star attribute %u\n",
                GetConfig().OperationStarAttribute());
            return false;
        }

        // AllocateItem can rehash m_items, so re-find the pass before erasing it.
        it = m_items.find(itemId);
        if (it == m_items.end())
        {
            m_items.erase(coinId);
            return false;
        }

        AddToMultipleObjects(updateMultiple, coin);
        DestroyItem(it, destroy);
        m_operationSeasonPassTime = static_cast<uint32_t>(time(nullptr));
        AddOperationSeasonalState(updateMultiple);
        WriteToFile();

        Platform::Print("operation: activated pass def %u -> coin def %u (0 stars)\n",
            defIndex, GetConfig().OperationActivationCoinDef());
        return true;
    }

    // Operation star packs (1 / 10 / 100 by default). The Panorama star-store
    // already buys these inventory items and then calls InventoryAPI.UseTool on
    // each one; applying them here completes that original client workflow.
    const uint32_t starPackValue = GetConfig().OperationStarPackValue(defIndex);
    if (starPackValue > 0)
    {
        CSOEconItem *coin = FindOperationCoin(0);
        if (!coin)
        {
            Platform::Print("operation: cannot apply star pack def %u - no Operation coin\n", defIndex);
            return false;
        }

        const uint32_t oldStars = OperationStars(*coin);
        uint64_t sum = static_cast<uint64_t>(oldStars) + starPackValue;
        const uint32_t newStars = sum > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(sum);

        CSOEconItemAttribute *starAttribute = nullptr;
        for (int i = 0; i < coin->attribute_size(); i++)
        {
            if (coin->mutable_attribute(i)->def_index() == GetConfig().OperationStarAttribute())
            {
                starAttribute = coin->mutable_attribute(i);
                break;
            }
        }
        if (!starAttribute)
        {
            starAttribute = coin->add_attribute();
            starAttribute->set_def_index(GetConfig().OperationStarAttribute());
        }

        if (!m_itemSchema.SetAttributeUint32(starAttribute, newStars))
        {
            return false;
        }

        AddToMultipleObjects(updateMultiple, *coin);
        DestroyItem(it, destroy);
        AddOperationSeasonalState(updateMultiple);
        WriteToFile();

        Platform::Print("operation: applied star pack def %u (+%u), balance %u -> %u\n",
            defIndex, starPackValue, oldStars, newStars);
        return true;
    }

    // Existing csgo_gc behaviour: unseal a graffiti item.
    if (defIndex != ItemSchema::ItemSpray)
    {
        assert(false);
        return false;
    }

    // Copy before AllocateItem can rehash the item map.
    const CSOEconItem sealed = it->second;
    CSOEconItem &unsealed = CreateItem(sealed);
    unsealed.set_def_index(ItemSchema::ItemSprayPaint);

    // remove the sealed spray from our inventory
    it = m_items.find(itemId);
    if (it == m_items.end())
    {
        return false;
    }
    DestroyItem(it, destroy);

    // equip the new spray, this will also unequip the old one if we had one
    EquipItem(unsealed.id(), 0, ItemSchema::LoadoutSlotGraffiti, updateMultiple);

    // remove this to have unlimited sprays
    CSOEconItemAttribute *attribute = unsealed.add_attribute();
    attribute->set_def_index(ItemSchema::AttributeSpraysRemaining);
    m_itemSchema.SetAttributeUint32(attribute, 50);

    // set notification
    notification.add_item_id(unsealed.id());
    notification.set_request(k_EGCItemCustomizationNotification_GraffitiUnseal);

    return true;
}

bool Inventory::UnlockCrate(uint64_t crateId,
    uint64_t keyId,
    CMsgSOSingleObject &destroyCrate,
    CMsgSOSingleObject &destroyKey,
    CMsgSOSingleObject &newItem,
    CMsgGCItemCustomizationNotification &notification)
{
    auto crate = m_items.find(crateId);
    if (crate == m_items.end())
    {
        assert(false);
        return false;
    }

    // CASE OPENING
    CaseOpening caseOpening{ m_itemSchema, m_random };

    CSOEconItem temp;
    if (!caseOpening.SelectItemFromCrate(crate->second, temp))
    {
        assert(false);
        return false;
    }

    CSOEconItem &item = CreateItem(temp);

    ToSingleObject(newItem, item);

    // set notification
    notification.add_item_id(item.id());
    notification.set_request(k_EGCItemCustomizationNotification_UnlockCrate);

    // remove the crate
    if (GetConfig().DestroyUsedItems())
    {
        DestroyItem(crate, destroyCrate);

        // remove the key if one was used (yes, we don't validate keys...)
        auto key = m_items.find(keyId);
        if (key != m_items.end())
        {
            DestroyItem(key, destroyKey);
        }
    }

    return true;
}

// trade-up contracts (revival addition)
// ---------------------------------------------------------------------------
// Consumes the input skins and produces one skin of the next rarity up. For
// Covert inputs it runs the CS2 "5 Covert -> 1 gold (knife/glove)" recipe,
// pulling the output from the case's gold pool instead. Mirrors CS:GO trade-up
// contracts:
//   - all inputs must be painted weapon skins in a known collection (item_set)
//   - all inputs must share the same rarity and the same StatTrak state
//   - the output collection is chosen from the inputs' collections, weighted by
//     how many inputs came from each collection
//   - the output skin is a uniform-random next-rarity skin from that collection
//   - the output float is the normalized average of the input floats mapped into
//     the output paint kit's wear range
//   - StatTrak in -> StatTrak out
// Item create/destroy uses the same SO-cache path as case opening (UnlockCrate),
// which is the proven way to make items appear/disappear in the client.
bool Inventory::TradeUp(const std::vector<uint64_t> &itemIds,
    std::vector<CMsgSOSingleObject> &destroyed,
    CMsgSOSingleObject &newItem,
    uint64_t &newItemId)
{
    newItemId = 0;

    if (itemIds.size() < 2)
    {
        Platform::Print("tradeup: need at least 2 input items, got %zu\n", itemIds.size());
        return false;
    }

    struct Input
    {
        uint32_t defIndex;
        uint32_t paintKit;
        float wear;
        const Collection *collection;
        const CollectionItem *collItem;
    };

    std::vector<Input> inputs;
    inputs.reserve(itemIds.size());

    uint32_t commonRarity = 0;
    bool statTrak = false;

    for (size_t index = 0; index < itemIds.size(); index++)
    {
        uint64_t id = itemIds[index];

        auto it = m_items.find(id);
        if (it == m_items.end())
        {
            Platform::Print("tradeup: input item %llu not found, aborting\n", id);
            return false;
        }

        const CSOEconItem &item = it->second;

        uint32_t paintKit = 0;
        bool hasWear = false;
        float wear = 0.0f;
        bool itemStatTrak = false;

        for (const CSOEconItemAttribute &attribute : item.attribute())
        {
            switch (attribute.def_index())
            {
            case ItemSchema::AttributeTexturePrefab:
                paintKit = m_itemSchema.AttributeUint32(&attribute);
                break;
            case ItemSchema::AttributeTextureWear:
                wear = m_itemSchema.AttributeFloat(&attribute);
                hasWear = true;
                break;
            case ItemSchema::AttributeKillEater:
                itemStatTrak = true;
                break;
            }
        }

        if (!paintKit || !hasWear)
        {
            Platform::Print("tradeup: input %llu is not a painted skin (paintkit=%u hasWear=%d)\n",
                id, paintKit, hasWear ? 1 : 0);
            return false;
        }

        const CollectionItem *collItem = nullptr;
        const Collection *collection = m_itemSchema.FindCollectionForItem(item.def_index(), paintKit, &collItem);
        if (!collection || !collItem)
        {
            Platform::Print("tradeup: input %llu (def %u paintkit %u) is not in any collection\n",
                id, item.def_index(), paintKit);
            return false;
        }

        if (index == 0)
        {
            commonRarity = collItem->rarity;
            statTrak = itemStatTrak;
        }
        else
        {
            if (collItem->rarity != commonRarity)
            {
                Platform::Print("tradeup: mixed rarities (%u vs %u), aborting\n", collItem->rarity, commonRarity);
                return false;
            }
            if (itemStatTrak != statTrak)
            {
                Platform::Print("tradeup: mixed StatTrak state, aborting\n");
                return false;
            }
        }

        inputs.push_back({ item.def_index(), paintKit, wear, collection, collItem });
    }

    // ===== 5 Covert -> 1 gold (knife/glove), the CS2 recipe =====
    // Covert is the top normal grade, so instead of "next rarity" the output is a
    // random knife/glove from the case pool of one of the input collections.
    if (commonRarity >= ItemSchema::RarityAncient)
    {
        // gather each input's case gold pool, weighted by how many inputs used it
        std::vector<std::pair<const LootList *, int>> pools;
        for (const Input &in : inputs)
        {
            const LootList *pool = m_itemSchema.FindUnusualPoolForItem(in.defIndex, in.paintKit);
            if (!pool)
            {
                Platform::Print("tradeup: Covert input def %u paintkit %u has no known gold pool, aborting\n",
                    in.defIndex, in.paintKit);
                return false;
            }

            bool merged = false;
            for (auto &entry : pools)
            {
                if (entry.first == pool)
                {
                    entry.second++;
                    merged = true;
                    break;
                }
            }
            if (!merged)
            {
                pools.push_back({ pool, 1 });
            }
        }

        int totalWeight = 0;
        for (const auto &entry : pools)
        {
            totalWeight += entry.second;
        }

        // pick the gold pool weighted by input count
        int roll = m_random.Integer<int>(1, totalWeight);
        const LootList *chosenPool = pools.front().first;
        int accum = 0;
        for (const auto &entry : pools)
        {
            accum += entry.second;
            if (roll <= accum)
            {
                chosenPool = entry.first;
                break;
            }
        }

        // uniform-random gold from that pool
        std::vector<const LootListItem *> golds;
        for (const LootListItem &gold : chosenPool->items)
        {
            if (gold.type == LootListItemPaintable && gold.itemInfo && gold.paintKitInfo)
            {
                golds.push_back(&gold);
            }
        }

        if (golds.empty())
        {
            Platform::Print("tradeup: chosen gold pool has no usable items, aborting\n");
            return false;
        }

        const LootListItem *chosen = golds[m_random.Integer<size_t>(0, golds.size() - 1)];

        // output float = normalized average of the inputs, mapped to the gold range
        float sumNormalized = 0.0f;
        for (const Input &in : inputs)
        {
            float lo = in.collItem->paintKitInfo->m_minFloat;
            float hi = in.collItem->paintKitInfo->m_maxFloat;
            float normalized = (hi > lo) ? (in.wear - lo) / (hi - lo) : 0.0f;
            if (normalized < 0.0f) normalized = 0.0f;
            if (normalized > 1.0f) normalized = 1.0f;
            sumNormalized += normalized;
        }
        float avgNormalized = sumNormalized / static_cast<float>(inputs.size());
        float outLo = chosen->paintKitInfo->m_minFloat;
        float outHi = chosen->paintKitInfo->m_maxFloat;
        float outputFloat = avgNormalized * (outHi - outLo) + outLo;

        // StatTrak carries over only to items that can have it - gloves and some
        // newer knives (unusual quality, def >= 1000) can't, matching the case
        // opening logic in ShouldMakeStatTrak.
        bool goldStatTrak = statTrak && (chosen->itemInfo->m_defIndex < 1000);

        // build the gold with the same path case opening uses, then override the
        // wear with our computed float (create BEFORE destroying inputs)
        CSOEconItem temp;
        if (!m_itemSchema.CreateItemFromLootListItem(m_random, *chosen, goldStatTrak,
                ItemOriginCrate, UnacknowledgedCrafted, temp))
        {
            Platform::Print("tradeup: failed to create gold item, aborting\n");
            return false;
        }

        for (int i = 0; i < temp.attribute_size(); i++)
        {
            CSOEconItemAttribute *attribute = temp.mutable_attribute(i);
            if (attribute->def_index() == ItemSchema::AttributeTextureWear)
            {
                m_itemSchema.SetAttributeFloat(attribute, outputFloat);
                break;
            }
        }

        CSOEconItem &output = CreateItem(temp);

        Platform::Print("tradeup: %zu Covert -> GOLD def %u paintkit %u float %.4f%s\n",
            inputs.size(), chosen->itemInfo->m_defIndex, chosen->paintKitInfo->m_defIndex,
            outputFloat, goldStatTrak ? " StatTrak" : "");

        newItemId = output.id();
        ToSingleObject(newItem, output);

        destroyed.reserve(itemIds.size());
        for (uint64_t id : itemIds)
        {
            auto it = m_items.find(id);
            if (it == m_items.end())
            {
                continue;
            }

            CMsgSOSingleObject destroy;
            DestroyItem(it, destroy);
            destroyed.push_back(std::move(destroy));
        }

        return true;
    }

    uint32_t outputRarity = commonRarity + 1;

    // tally distinct collections, weighted by how many inputs came from each,
    // keeping only those that actually have a skin of the output rarity
    std::vector<std::pair<const Collection *, int>> weighted;
    for (const Input &in : inputs)
    {
        bool merged = false;
        for (auto &entry : weighted)
        {
            if (entry.first == in.collection)
            {
                entry.second++;
                merged = true;
                break;
            }
        }
        if (!merged)
        {
            weighted.push_back({ in.collection, 1 });
        }
    }

    int totalWeight = 0;
    std::vector<std::pair<const Collection *, int>> eligible;
    for (const auto &entry : weighted)
    {
        bool hasOutput = false;
        for (const CollectionItem &ci : entry.first->items)
        {
            if (ci.rarity == outputRarity)
            {
                hasOutput = true;
                break;
            }
        }
        if (hasOutput)
        {
            eligible.push_back(entry);
            totalWeight += entry.second;
        }
    }

    if (eligible.empty() || totalWeight <= 0)
    {
        Platform::Print("tradeup: no input collection has a skin of the next rarity (%u)\n", outputRarity);
        return false;
    }

    // pick the output collection weighted by input count
    int roll = m_random.Integer<int>(1, totalWeight);
    const Collection *chosenCollection = eligible.front().first;
    int accum = 0;
    for (const auto &entry : eligible)
    {
        accum += entry.second;
        if (roll <= accum)
        {
            chosenCollection = entry.first;
            break;
        }
    }

    // uniform-random next-rarity skin from that collection
    std::vector<const CollectionItem *> pool;
    for (const CollectionItem &ci : chosenCollection->items)
    {
        if (ci.rarity == outputRarity)
        {
            pool.push_back(&ci);
        }
    }

    const CollectionItem *chosen = pool[m_random.Integer<size_t>(0, pool.size() - 1)];

    // output float = normalized average of inputs mapped into the output range
    float sumNormalized = 0.0f;
    for (const Input &in : inputs)
    {
        float lo = in.collItem->paintKitInfo->m_minFloat;
        float hi = in.collItem->paintKitInfo->m_maxFloat;
        float normalized = (hi > lo) ? (in.wear - lo) / (hi - lo) : 0.0f;
        if (normalized < 0.0f) normalized = 0.0f;
        if (normalized > 1.0f) normalized = 1.0f;
        sumNormalized += normalized;
    }

    float avgNormalized = sumNormalized / static_cast<float>(inputs.size());
    float outLo = chosen->paintKitInfo->m_minFloat;
    float outHi = chosen->paintKitInfo->m_maxFloat;
    float outputFloat = avgNormalized * (outHi - outLo) + outLo;

    // build the output item. NOTE: create BEFORE destroying inputs; do not touch
    // the input iterators afterwards (CreateItem may rehash m_items). We re-find
    // inputs by id for destruction below.
    CSOEconItem &output = CreateItem(chosen->itemDefIndex, ItemOriginCrate, UnacknowledgedCrafted);
    output.set_rarity(outputRarity);
    output.set_quality(statTrak ? ItemSchema::QualityStrange : ItemSchema::QualityUnique);

    {
        CSOEconItemAttribute *attribute = output.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeTexturePrefab);
        m_itemSchema.SetAttributeUint32(attribute, chosen->paintKitDefIndex);
    }
    {
        CSOEconItemAttribute *attribute = output.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeTextureSeed);
        m_itemSchema.SetAttributeUint32(attribute, m_random.Integer<uint32_t>(0, 1000));
    }
    {
        CSOEconItemAttribute *attribute = output.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeTextureWear);
        m_itemSchema.SetAttributeFloat(attribute, outputFloat);
    }

    if (statTrak)
    {
        CSOEconItemAttribute *attribute = output.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeKillEater);
        m_itemSchema.SetAttributeUint32(attribute, 0);

        attribute = output.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeKillEaterScoreType);
        m_itemSchema.SetAttributeUint32(attribute, 0);
    }

    Platform::Print("tradeup: %zu inputs (rarity %u%s) from collection '%s' -> def %u paintkit %u rarity %u float %.4f\n",
        inputs.size(), commonRarity, statTrak ? " StatTrak" : "",
        chosenCollection->name.c_str(), chosen->itemDefIndex, chosen->paintKitDefIndex,
        outputRarity, outputFloat);

    newItemId = output.id();
    ToSingleObject(newItem, output);

    // consume the inputs (re-find by id; iterators from the loop above may be stale)
    destroyed.reserve(itemIds.size());
    for (uint64_t id : itemIds)
    {
        auto it = m_items.find(id);
        if (it == m_items.end())
        {
            continue;
        }

        CMsgSOSingleObject destroy;
        DestroyItem(it, destroy);
        destroyed.push_back(std::move(destroy));
    }

    return true;
}

// trade-up contracts (revival addition)
uint32_t Inventory::ItemDefIndex(uint64_t itemId) const
{
    auto it = m_items.find(itemId);
    return (it != m_items.end()) ? it->second.def_index() : 0;
}

// find 5 Covert skins that share the same StatTrak state. Collections may be
// MIXED - the gold pool is then weighted by how many inputs came from each
// collection (e.g. 3 from Weapon Case 1 + 2 from another -> 3:2 odds). StatTrak
// and non-StatTrak can't be combined (a contract is all one or the other).
bool Inventory::SelectCovertsForTradeUp(std::vector<uint64_t> &out) const
{
    std::vector<uint64_t> normal;
    std::vector<uint64_t> statTrak;

    for (const auto &pair : m_items)
    {
        const CSOEconItem &item = pair.second;

        uint32_t paintKit = 0;
        bool hasWear = false;
        bool isStatTrak = false;

        for (const CSOEconItemAttribute &attribute : item.attribute())
        {
            switch (attribute.def_index())
            {
            case ItemSchema::AttributeTexturePrefab:
                paintKit = m_itemSchema.AttributeUint32(&attribute);
                break;
            case ItemSchema::AttributeTextureWear:
                hasWear = true;
                break;
            case ItemSchema::AttributeKillEater:
                isStatTrak = true;
                break;
            }
        }

        if (!paintKit || !hasWear)
        {
            continue;
        }

        const CollectionItem *collItem = nullptr;
        const Collection *collection = m_itemSchema.FindCollectionForItem(item.def_index(), paintKit, &collItem);
        if (!collection || !collItem || collItem->rarity != ItemSchema::RarityAncient)
        {
            continue;
        }

        // it's a Covert skin - split only by StatTrak state, collection can vary
        (isStatTrak ? statTrak : normal).push_back(item.id());
    }

    // prefer a full non-StatTrak contract, then StatTrak
    if (normal.size() >= 5)
    {
        out.assign(normal.begin(), normal.begin() + 5);
        return true;
    }
    if (statTrak.size() >= 5)
    {
        out.assign(statTrak.begin(), statTrak.begin() + 5);
        return true;
    }

    return false;
}

// "Gold Trade-Up crate": opening the configured crate converts 5 of the player's
// Covert skins into a gold, revealed through the normal unbox flow.
bool Inventory::UnlockCrateGoldTradeUp(uint64_t crateId,
    uint64_t keyId,
    std::vector<CMsgSOSingleObject> &destroyed,
    CMsgSOSingleObject &newItem,
    CMsgGCItemCustomizationNotification &notification)
{
    // make sure the crate exists (don't hold the iterator - TradeUp rehashes m_items)
    if (m_items.find(crateId) == m_items.end())
    {
        assert(false);
        return false;
    }

    std::vector<uint64_t> coverts;
    if (!SelectCovertsForTradeUp(coverts))
    {
        Platform::Print("gold trade-up: need 5 Covert skins from the same collection (same StatTrak state) in your inventory\n");
        return false;
    }

    uint64_t goldId = 0;
    if (!TradeUp(coverts, destroyed, newItem, goldId))
    {
        Platform::Print("gold trade-up: conversion failed\n");
        return false;
    }

    // reveal the gold through the unbox animation
    notification.add_item_id(goldId);
    notification.set_request(k_EGCItemCustomizationNotification_UnlockCrate);

    // NOTE: the Gold Trade-Up crate and its key are intentionally NOT consumed -
    // it's a permanent, reusable trade-up tool, so a player can keep doing
    // 5 Covert -> gold without needing it re-granted. Only the 5 Coverts (consumed
    // by TradeUp above) and the produced gold change hands here.
    (void)keyId;

    return true;
}

// "gold only" case (revival addition) --- "Kinkerm's Case"
// ---------------------------------------------------------------------------
// Opening the configured crate always yields a uniform-random gold (knife/glove)
// from any collection. Unlike a normal case it consumes nothing but the crate
// (and key) itself - no Coverts needed. Uses the same SO-cache unbox flow as
// UnlockCrate so the client shows the standard reveal notification.
bool Inventory::UnlockGoldOnlyCase(uint64_t crateId,
    uint64_t keyId,
    CMsgSOSingleObject &destroyCrate,
    CMsgSOSingleObject &destroyKey,
    CMsgSOSingleObject &newItem,
    CMsgGCItemCustomizationNotification &notification)
{
    // make sure the crate exists (don't hold the iterator across CreateItem,
    // which rehashes m_items)
    if (m_items.find(crateId) == m_items.end())
    {
        assert(false);
        return false;
    }

    // pick a random gold from any collection
    const LootListItem *gold = m_itemSchema.PickRandomGold(m_random);
    if (!gold)
    {
        Platform::Print("gold-only case: no gold items found in the schema\n");
        return false;
    }

    // StatTrak: 1/10 chance, but only on items that can carry it (gloves and some
    // newer knives are def >= 1000 and can't), matching case opening.
    bool statTrak = (gold->itemInfo->m_defIndex < 1000)
        && (m_random.Integer(1, 10) == 1);

    CSOEconItem temp;
    if (!m_itemSchema.CreateItemFromLootListItem(m_random, *gold, statTrak,
            ItemOriginCrate, UnacknowledgedFoundInCrate, temp))
    {
        Platform::Print("gold-only case: failed to create gold item\n");
        return false;
    }

    // CreateItem rehashes m_items - any iterators obtained before are invalidated
    CSOEconItem &item = CreateItem(temp);

    Platform::Print("gold-only case: rolled GOLD def %u paintkit %u%s\n",
        gold->itemInfo->m_defIndex, gold->paintKitInfo->m_defIndex,
        statTrak ? " StatTrak" : "");

    ToSingleObject(newItem, item);

    // reveal through the unbox animation
    notification.add_item_id(item.id());
    notification.set_request(k_EGCItemCustomizationNotification_UnlockCrate);

    // consume the crate (and key, if one was used) - re-find AFTER CreateItem
    if (GetConfig().DestroyUsedItems())
    {
        auto crate = m_items.find(crateId);
        if (crate != m_items.end())
        {
            DestroyItem(crate, destroyCrate);
        }

        auto key = m_items.find(keyId);
        if (key != m_items.end())
        {
            DestroyItem(key, destroyKey);
        }
    }

    return true;
}

// mikkotodo constant enum
static int ItemWearLevel(float wearFloat)
{
    if (wearFloat < 0.07f)
    {
        // factory new
        return 0;
    }

    if (wearFloat < 0.15f)
    {
        // minimal wear
        return 1;
    }

    if (wearFloat < 0.37f)
    {
        // field tested
        return 2;
    }

    if (wearFloat < 0.45f)
    {
        // well worn
        return 3;
    }

    // battle scarred
    return 4;
}

void Inventory::ItemToPreviewDataBlock(const CSOEconItem &item, CEconItemPreviewDataBlock &block)
{
    block.set_accountid(item.account_id());
    block.set_itemid(item.id());
    block.set_defindex(item.def_index());
    block.set_rarity(item.rarity());
    block.set_quality(item.quality());
    block.set_customname(item.custom_name());
    block.set_inventory(item.inventory());
    block.set_origin(item.origin());

    // not stored in CSOEconItem?
    //block.set_entindex(item.entindex());
    //block.set_dropreason(item.dropreason());

    std::array<CEconItemPreviewDataBlock_Sticker, MaxStickers> stickers;

    for (const CSOEconItemAttribute &attribute : item.attribute())
    {
        uint32_t defIndex = attribute.def_index();
        switch (defIndex)
        {
        case ItemSchema::AttributeTexturePrefab:
            block.set_paintindex(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeTextureSeed:
            block.set_paintseed(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeTextureWear:
        {
            int wearLevel = ItemWearLevel(m_itemSchema.AttributeFloat(&attribute));
            block.set_paintwear(wearLevel);
            break;
        }

        case ItemSchema::AttributeKillEater:
            block.set_killeatervalue(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeKillEaterScoreType:
            block.set_killeaterscoretype(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeMusicId:
            block.set_musicindex(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeQuestId:
            block.set_questid(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeSprayTintId:
            stickers[0].set_tint_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerId0:
            stickers[0].set_sticker_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerWear0:
            stickers[0].set_wear(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerScale0:
            stickers[0].set_scale(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerRotation0:
            stickers[0].set_rotation(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerId1:
            stickers[1].set_sticker_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerWear1:
            stickers[1].set_wear(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerScale1:
            stickers[1].set_scale(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerRotation1:
            stickers[1].set_rotation(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerId2:
            stickers[2].set_sticker_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerWear2:
            stickers[2].set_wear(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerScale2:
            stickers[2].set_scale(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerRotation2:
            stickers[2].set_rotation(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerId3:
            stickers[3].set_sticker_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerWear3:
            stickers[3].set_wear(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerScale3:
            stickers[3].set_scale(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerRotation3:
            stickers[3].set_rotation(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerId4:
            stickers[4].set_sticker_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerWear4:
            stickers[4].set_wear(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerScale4:
            stickers[4].set_scale(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerRotation4:
            stickers[4].set_rotation(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerId5:
            stickers[5].set_sticker_id(m_itemSchema.AttributeUint32(&attribute));
            break;

        case ItemSchema::AttributeStickerWear5:
            stickers[5].set_wear(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerScale5:
            stickers[5].set_scale(m_itemSchema.AttributeFloat(&attribute));
            break;

        case ItemSchema::AttributeStickerRotation5:
            stickers[5].set_rotation(m_itemSchema.AttributeFloat(&attribute));
            break;
        }
    }

    for (size_t i = 0; i < stickers.size(); i++)
    {
        const CEconItemPreviewDataBlock_Sticker &source = stickers[i];
        if (!source.has_sticker_id())
        {
            continue;
        }

        CEconItemPreviewDataBlock_Sticker *sticker = block.add_stickers();
        *sticker = source;
        sticker->set_slot(i);
    }
}

bool Inventory::SetItemPositions(
    const CMsgSetItemPositions &message,
    std::vector<CMsgItemAcknowledged> &acknowledgements,
    CMsgSOMultipleObjects &update)
{
    for (const CMsgSetItemPositions_ItemPosition &position : message.item_positions())
    {
        auto it = m_items.find(position.item_id());
        if (it == m_items.end())
        {
            assert(false);
            return false;
        }

        CSOEconItem &item = it->second;

        Platform::Print("SetItemPositions: %llu --> %u\n", position.item_id(), position.position());

        CMsgItemAcknowledged &acknowledgement = acknowledgements.emplace_back();
        ItemToPreviewDataBlock(item, *acknowledgement.mutable_iteminfo());

        item.set_inventory(position.position());

        AddToMultipleObjects(update, item);
    }

    return true;
}

bool Inventory::ApplySticker(const CMsgApplySticker &message,
    CMsgSOSingleObject &update,
    CMsgSOSingleObject &destroy,
    CMsgGCItemCustomizationNotification &notification)
{
    assert(message.has_sticker_item_id());
    assert(message.has_sticker_slot());
    assert(!message.has_sticker_wear());

    auto sticker = m_items.find(message.sticker_item_id());
    if (sticker == m_items.end())
    {
        assert(false);
        return false;
    }

    CSOEconItem *item = nullptr;

    if (message.baseitem_defidx())
    {
        item = &CreateItem(message.baseitem_defidx(), ItemOriginBaseItem, UnacknowledgedInvalid);
    }
    else
    {
        auto it = m_items.find(message.item_item_id());
        if (it == m_items.end())
        {
            assert(false);
            return false;
        }

        item = &it->second;
    }

    assert(item);

    // get the sticker kit def index
    uint32_t stickerKit = 0;

    for (const CSOEconItemAttribute &attribute : sticker->second.attribute())
    {
        if (attribute.def_index() == ItemSchema::AttributeStickerId0)
        {
            stickerKit = m_itemSchema.AttributeUint32(&attribute);
            break;
        }
    }

    if (!stickerKit)
    {
        assert(false);
        return false;
    }

    // mikkotodo lookup table instead of this crap...
    uint32_t attributeStickerId = ItemSchema::AttributeStickerId0 + (message.sticker_slot() * 4);
    uint32_t attributeStickerWear = ItemSchema::AttributeStickerWear0 + (message.sticker_slot() * 4);

    // add the sticker id attribute
    CSOEconItemAttribute *attribute = item->add_attribute();
    attribute->set_def_index(attributeStickerId);
    m_itemSchema.SetAttributeUint32(attribute, stickerKit);

    // add the sticker wear attribute if this is not a patch (mikkotodo revisit...)
    if (sticker->second.def_index() != ItemSchema::ItemPatch)
    {
        attribute = item->add_attribute();
        attribute->set_def_index(attributeStickerWear);
        m_itemSchema.SetAttributeFloat(attribute, 0);
    }

    ToSingleObject(update, *item);

    // remove the sticker
    if (GetConfig().DestroyUsedItems())
    {
        DestroyItem(sticker, destroy);
    }

    // notification, if any
    notification.add_item_id(item->id());
    notification.set_request(k_EGCItemCustomizationNotification_ApplySticker);

    return true;
}

static void RemoveStickerAttributes(CSOEconItem &item, uint32_t slot)
{
    // mikkotodo lookup table instead of this crap...
    // mikkotodo rest of attribs???
    uint32_t attributeStickerId = ItemSchema::AttributeStickerId0 + (slot * 4);
    uint32_t attributeStickerWear = ItemSchema::AttributeStickerWear0 + (slot * 4);

    for (auto attrib = item.mutable_attribute()->begin(); attrib != item.mutable_attribute()->end();)
    {
        if (attrib->def_index() == attributeStickerId
            || attrib->def_index() == attributeStickerWear)
        {
            attrib = item.mutable_attribute()->erase(attrib);
        }
        else
        {
            attrib++;
        }
    }
}

bool Inventory::ScrapeSticker(const CMsgApplySticker &message,
    CMsgSOSingleObject &update,
    CMsgSOSingleObject &destroy,
    CMsgGCItemCustomizationNotification &notification)
{
    auto it = m_items.find(message.item_item_id());
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    CSOEconItem &item = it->second;

    // mikkotodo lookup table instead of this crap...
    uint32_t attributeStickerWear = ItemSchema::AttributeStickerWear0 + (message.sticker_slot() * 4);

    CSOEconItemAttribute *wearAttribute = nullptr;
    for (int i = 0; i < item.attribute_size(); i++)
    {
        if (item.mutable_attribute(i)->def_index() == attributeStickerWear)
        {
            wearAttribute = item.mutable_attribute(i);
            break;
        }
    }

    float wearLevel = 0.0f;

    if (wearAttribute)
    {
        // mikkotodo randomize
        float wearIncrement = 1.0f / 9;
        wearLevel = m_itemSchema.AttributeFloat(wearAttribute) + wearIncrement;
    }

    // if the wear attribute is not present, remove it outright (patches)
    if (!wearAttribute || wearLevel > 1.0f)
    {
        // so long, and thanks for all the fish

        // mikkotodo fix... should this be deduced from the item???
        uint32_t request = k_EGCItemCustomizationNotification_RemoveSticker;
        if (!wearAttribute)
        {
            request = k_EGCItemCustomizationNotification_RemovePatch;
        }

        if (item.rarity() == ItemSchema::RarityDefault)
        {
            // sticker removal notification with a fake item id
            notification.add_item_id(item.def_index() | ItemIdDefaultItemMask);
            notification.set_request(request);

            // this was a default weapon clone with a sticker so destroy the entire item
            DestroyItem(it, destroy);
        }
        else
        {
            // sticker removal notification
            notification.add_item_id(item.id());
            notification.set_request(request);

            // remove the sticker
            RemoveStickerAttributes(item, message.sticker_slot());

            ToSingleObject(update, item);
        }
    }
    else
    {
        // just update the wear
        m_itemSchema.SetAttributeFloat(wearAttribute, wearLevel);

        ToSingleObject(update, item);
    }

    return true;
}

bool Inventory::IncrementKillCountAttribute(uint64_t itemId, uint32_t amount, CMsgSOSingleObject &update)
{
    auto it = m_items.find(itemId);
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    CSOEconItem &item = it->second;
    bool incremented = false;

    for (int i = 0; i < item.attribute_size(); i++)
    {
        CSOEconItemAttribute *attribute = item.mutable_attribute(i);
        if (attribute->def_index() == ItemSchema::AttributeKillEater)
        {
            int value = m_itemSchema.AttributeUint32(attribute) + amount;
            m_itemSchema.SetAttributeUint32(attribute, value);
            incremented = true;
            break;
        }
    }

    if (incremented)
    {
        ToSingleObject(update, item);
        return true;
    }

    assert(false);
    return false;
}

bool Inventory::NameItem(uint64_t nameTagId,
    uint64_t itemId,
    std::string_view name,
    CMsgSOSingleObject &update,
    CMsgSOSingleObject &destroy,
    CMsgGCItemCustomizationNotification &notification)
{
    auto it = m_items.find(itemId);
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    it->second.mutable_custom_name()->assign(name);

    ToSingleObject(update, it->second);

    if (GetConfig().DestroyUsedItems())
    {
        auto tag = m_items.find(nameTagId);
        if (tag == m_items.end())
        {
            assert(false);
            return false;
        }

        DestroyItem(tag, destroy);
    }

    notification.add_item_id(it->second.id());
    notification.set_request(k_EGCItemCustomizationNotification_NameItem);

    return true;
}

bool Inventory::NameBaseItem(uint64_t nameTagId,
    uint32_t defIndex,
    std::string_view name,
    CMsgSOSingleObject &create,
    CMsgSOSingleObject &destroy,
    CMsgGCItemCustomizationNotification &notification)
{
    CSOEconItem &item = CreateItem(defIndex, ItemOriginBaseItem, UnacknowledgedInvalid);

    item.mutable_custom_name()->assign(name);

    ToSingleObject(create, item);

    if (GetConfig().DestroyUsedItems())
    {
        auto tag = m_items.find(nameTagId);
        if (tag == m_items.end())
        {
            assert(false);
            return false;
        }

        DestroyItem(tag, destroy);
    }

    notification.add_item_id(item.id()); // mikkotodo def index???
    notification.set_request(k_EGCItemCustomizationNotification_NameBaseItem);

    return true;
}

bool Inventory::RemoveItemName(uint64_t itemId,
    CMsgSOSingleObject &update,
    CMsgSOSingleObject &destroy,
    CMsgGCItemCustomizationNotification &notification)
{
    auto it = m_items.find(itemId);
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    if (it->second.rarity() == ItemSchema::RarityDefault)
    {
        notification.add_item_id(it->second.def_index() | ItemIdDefaultItemMask);
        notification.set_request(k_EGCItemCustomizationNotification_RemoveItemName);

        DestroyItem(it, destroy);
    }
    else
    {
        it->second.mutable_custom_name()->clear();

        notification.add_item_id(it->second.id());
        notification.set_request(k_EGCItemCustomizationNotification_RemoveItemName);

        ToSingleObject(update, it->second);
    }

    return true;
}

uint64_t Inventory::PurchaseItem(uint32_t defIndex, std::vector<CMsgSOSingleObject> &update)
{
    // Do not use CreateItem(defIndex) here: that helper allocates first and
    // historically ignored ItemSchema::CreateItem's failure result, leaving an
    // empty object with a valid id for invalid definitions.
    CSOEconItem &item = AllocateItem(0);
    const uint64_t itemId = item.id();

    if (!m_itemSchema.CreateItem(defIndex, ItemOriginPurchased, UnacknowledgedPurchased, item))
    {
        m_items.erase(itemId);
        Platform::Print("store: refused unknown/uncreatable item def %u\n", defIndex);
        return 0;
    }

    CMsgSOSingleObject &single = update.emplace_back();
    ToSingleObject(single, item);

    return item.id();
}

uint64_t Inventory::PurchaseOperationReward(uint32_t defIndex, std::vector<CMsgSOSingleObject> &update)
{
    const LootList *lootList = m_itemSchema.GetDirectLootList(defIndex);
    if (!lootList)
    {
        // Ordinary rewards such as the Operation Riptide Case are meant to be
        // delivered as the container itself.
        return PurchaseItem(defIndex, update);
    }

    CaseOpening rewardOpening{ m_itemSchema, m_random };
    CSOEconItem selected;
    if (!rewardOpening.SelectItemFromDirectLootList(*lootList, selected))
    {
        Platform::Print("operation shop: failed to resolve direct reward def %u\n", defIndex);
        return 0;
    }

    CSOEconItem &item = CreateItem(selected);
    CMsgSOSingleObject &single = update.emplace_back();
    ToSingleObject(single, item);

    Platform::Print("operation shop: resolved wrapper def %u -> reward def %u (item %llu)\n",
        defIndex, item.def_index(), item.id());
    return item.id();
}

bool Inventory::CreateMatchDrop(uint32_t defIndex,
    bool resolveDirectLoot,
    UnacknowledgedType unacknowledgedType,
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    uint64_t itemId = 0;

    if (resolveDirectLoot)
    {
        const LootList *lootList = m_itemSchema.GetDirectLootList(defIndex);
        if (!lootList)
        {
            Platform::Print("drops: no direct loot list for wrapper def %u\n", defIndex);
            return false;
        }

        CaseOpening opening{ m_itemSchema, m_random };
        CSOEconItem selected;
        if (!opening.SelectItemFromDirectLootList(*lootList, selected))
        {
            Platform::Print("drops: failed to roll collection wrapper def %u\n", defIndex);
            return false;
        }

        // The selector is also used by the Operation shop and therefore creates
        // Purchased items. End-match rewards need the legacy unacknowledged type
        // so the inventory/end-match UI treats them as drops.
        selected.set_origin(ItemOriginCrate);
        selected.set_inventory(InventoryUnacknowledged(unacknowledgedType));

        CSOEconItem &item = CreateItem(selected);
        itemId = item.id();
        ToSingleObject(create, item);
        ItemToPreviewDataBlock(item, *notification.mutable_iteminfo());
    }
    else
    {
        CSOEconItem &item = AllocateItem(0);
        itemId = item.id();
        if (!m_itemSchema.CreateItem(
            defIndex, ItemOriginCrate, unacknowledgedType, item))
        {
            m_items.erase(itemId);
            Platform::Print("drops: failed to create case def %u\n", defIndex);
            return false;
        }

        ToSingleObject(create, item);
        ItemToPreviewDataBlock(item, *notification.mutable_iteminfo());
    }

    notification.mutable_iteminfo()->set_dropreason(0);
    WriteToFile();

    Platform::Print("drops: created end-match item %llu from source def %u\n",
        itemId, defIndex);
    return true;
}

bool Inventory::CreateRandomCaseMatchDrop(
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    // Every stock weapon case in the installed legacy schema is eligible.
    // Cases first sold in 2017 or earlier live in a rarer 1-in-10 sub-pool.
    // The regular pool therefore naturally includes newer cases such as
    // Danger Zone/Prisma/Fracture/Snakebite/Riptide-era containers.
    const std::vector<uint32_t> &allCases = m_itemSchema.MatchDropWeaponCases();
    const std::vector<uint32_t> &oldCases = m_itemSchema.MatchDropOldWeaponCases();

    std::vector<uint32_t> regularCases;
    regularCases.reserve(allCases.size());
    for (uint32_t defIndex : allCases)
    {
        if (std::find(oldCases.begin(), oldCases.end(), defIndex)
            == oldCases.end())
        {
            regularCases.push_back(defIndex);
        }
    }

    uint32_t defIndex = 0;
    bool oldCase = false;

    if (!oldCases.empty()
        && m_random.Integer<uint32_t>(1, 10) == 1)
    {
        oldCase = true;
        defIndex = oldCases[
            m_random.Integer<size_t>(0, oldCases.size() - 1)];
    }
    else if (!regularCases.empty())
    {
        defIndex = regularCases[
            m_random.Integer<size_t>(0, regularCases.size() - 1)];
    }
    else if (!allCases.empty())
    {
        defIndex = allCases[
            m_random.Integer<size_t>(0, allCases.size() - 1)];
    }
    else
    {
        // Last-resort compatibility fallback for a malformed/incomplete schema.
        constexpr std::array<uint32_t, 6> Fallback{
            4471, 4548, 4598, 4695, 4698, 4747
        };
        defIndex = Fallback[
            m_random.Integer<size_t>(0, Fallback.size() - 1)];
        Platform::Print(
            "drops: WARNING schema case pool empty; using six-case fallback\n");
    }

    Platform::Print("drops: case roll %s def=%u (all=%zu old=%zu regular=%zu)\n",
        oldCase ? "OLD/RARE" : "regular",
        defIndex, allCases.size(), oldCases.size(), regularCases.size());

    return CreateMatchDrop(
        defIndex, false, UnacknowledgedDropped, create, notification);
}

bool Inventory::CreateRareLegacyStickerCapsuleMatchDrop(
    uint32_t oneIn,
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    const std::vector<uint32_t> &pool = m_itemSchema.LegacyStickerCapsules();
    if (!oneIn || pool.empty()
        || m_random.Integer<uint32_t>(1, oneIn) != 1)
    {
        return false;
    }

    const uint32_t defIndex =
        pool[m_random.Integer<size_t>(0, pool.size() - 1)];

    Platform::Print(
        "drops: RARE 2014-2017 sticker capsule def=%u pool=%zu\n",
        defIndex, pool.size());

    return CreateMatchDrop(
        defIndex, false, UnacknowledgedDropped, create, notification);
}

bool Inventory::CreateRareLegacySouvenirPackageMatchDrop(
    uint32_t oneIn,
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    const std::vector<uint32_t> &pool = m_itemSchema.LegacySouvenirPackages();
    if (!oneIn || pool.empty()
        || m_random.Integer<uint32_t>(1, oneIn) != 1)
    {
        return false;
    }

    const uint32_t defIndex =
        pool[m_random.Integer<size_t>(0, pool.size() - 1)];

    Platform::Print(
        "drops: VERY RARE 2014-2017 souvenir package def=%u pool=%zu\n",
        defIndex, pool.size());

    return CreateMatchDrop(
        defIndex, false, UnacknowledgedDropped, create, notification);
}

bool Inventory::CreateWeeklyLevelReward(
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    RefreshProfileWeek();
    if (m_weeklyLevelRewardClaimed)
    {
        return false;
    }

    // Legacy weekly level-up reward was either graffiti or a skin from the
    // normal map-collection pool. Use the real schema entries for both.
    const bool graffiti = m_random.Integer<int>(0, 1) == 0;
    bool created = false;

    if (graffiti)
    {
        // Standard graffiti box, resolved immediately to its actual spray.
        created = CreateMatchDrop(
            4621, true, UnacknowledgedLevelUpReward, create, notification);
    }
    else
    {
        static const std::vector<std::string_view> Collections{
            "set_italy",
            "set_lake",
            "set_safehouse",
            "set_bank",
            "set_dust_2",
            "set_train",
            "set_nuke_2",
            "set_inferno_2",
        };

        CSOEconItem selected;
        if (m_itemSchema.CreateRandomCollectionItem(
            m_random, Collections, ItemOriginCrate, UnacknowledgedLevelUpReward, selected))
        {
            CSOEconItem &item = CreateItem(selected);
            ToSingleObject(create, item);
            ItemToPreviewDataBlock(item, *notification.mutable_iteminfo());
            notification.mutable_iteminfo()->set_dropreason(0);
            created = true;
        }
    }

    if (created)
    {
        m_weeklyLevelRewardClaimed = true;
        WriteToFile();
        Platform::Print("drops: weekly profile-rank reward granted\n");
    }
    return created;
}

bool Inventory::AddMatchPlaytimeAndCreateCaseDrop(uint32_t seconds,
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    RefreshProfileWeek();

    const uint64_t newPlaytime =
        static_cast<uint64_t>(m_casePlaytimeSeconds) + seconds;
    m_casePlaytimeSeconds = newPlaytime > UINT32_MAX
        ? UINT32_MAX : static_cast<uint32_t>(newPlaytime);

    if (m_caseDropsThisWeek >= 2
        || m_casePlaytimeSeconds < m_nextCaseDropSeconds)
    {
        WriteToFile();
        return false;
    }

    if (!CreateRandomCaseMatchDrop(create, notification))
    {
        WriteToFile();
        return false;
    }

    ++m_caseDropsThisWeek;
    if (m_caseDropsThisWeek == 1)
    {
        // The normal first weekly case arrives after a few hours. A second case
        // existed in the legacy system but was deliberately much rarer.
        const uint32_t extra =
            m_random.Integer<uint32_t>(20u * 3600u, 100u * 3600u);
        const uint64_t next = static_cast<uint64_t>(m_casePlaytimeSeconds) + extra;
        m_nextCaseDropSeconds = next > UINT32_MAX
            ? UINT32_MAX : static_cast<uint32_t>(next);
    }
    else
    {
        m_nextCaseDropSeconds = UINT32_MAX;
    }

    WriteToFile();
    Platform::Print("drops: timed case drop %u/2 after %us weekly playtime\n",
        m_caseDropsThisWeek, m_casePlaytimeSeconds);
    return true;
}

bool Inventory::CreateRandomCollectionMatchDrop(
    const std::vector<std::string_view> &collectionNames,
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    CSOEconItem selected;
    if (!m_itemSchema.CreateRandomCollectionItem(
        m_random, collectionNames, ItemOriginCrate, UnacknowledgedDropped, selected))
    {
        return false;
    }

    CSOEconItem &item = CreateItem(selected);
    ToSingleObject(create, item);
    ItemToPreviewDataBlock(item, *notification.mutable_iteminfo());
    notification.mutable_iteminfo()->set_dropreason(0);
    WriteToFile();

    Platform::Print("drops: revival collection reward item=%llu def=%u\n",
        item.id(), item.def_index());
    return true;
}

bool Inventory::CreateRareCollectionBonusMatchDrop(
    const std::vector<std::string_view> &collectionNames,
    uint32_t oneIn,
    CMsgSOSingleObject &create,
    CMsgGCCStrike15_v2_MatchEndRewardDropsNotification &notification)
{
    if (!oneIn || m_random.Integer<uint32_t>(1, oneIn) != 1)
        return false;
    return CreateRandomCollectionMatchDrop(collectionNames, create, notification);
}

bool Inventory::ImportServerCreatedItem(const CMsgSOSingleObject &create)
{
    if (!create.has_owner_soid()
        || create.owner_soid().type() != SoIdTypeSteamId
        || create.owner_soid().id() != m_steamId
        || !create.has_type_id()
        || create.type_id() != SOTypeItem
        || !create.has_object_data())
    {
        Platform::Print("drops: rejected malformed server SO Create\n");
        return false;
    }

    CSOEconItem incoming;
    if (!incoming.ParseFromString(create.object_data())
        || !incoming.has_id()
        || incoming.account_id() != AccountId())
    {
        Platform::Print("drops: rejected malformed server item object\n");
        return false;
    }

    const uint64_t itemId = incoming.id();
    auto existing = m_items.find(itemId);
    if (existing != m_items.end())
    {
        if (existing->second.SerializeAsString() == incoming.SerializeAsString())
        {
            // Idempotent bridge retry: the exact authoritative item is already
            // persisted.
            return true;
        }

        const uint32_t oldDef = existing->second.def_index();
        existing->second = incoming;
        WriteToFile();
        Platform::Print(
            "REVIVAL_SERVER_DROP_IMPORT_V2 corrected item=%llu def=%u->%u account=%u\n",
            static_cast<unsigned long long>(itemId), oldDef,
            incoming.def_index(), incoming.account_id());
        return true;
    }

    const uint32_t highItemId = HighItemId(itemId);
    if (!highItemId || (itemId & ItemIdDefaultItemMask) == ItemIdDefaultItemMask)
    {
        Platform::Print("drops: rejected invalid server item id %llu\n", itemId);
        return false;
    }

    m_items.emplace(itemId, incoming);
    if (highItemId > m_lastHighItemId)
        m_lastHighItemId = highItemId;
    WriteToFile();

    Platform::Print(
        "REVIVAL_SERVER_DROP_IMPORT_V1 item=%llu def=%u account=%u\n",
        itemId, incoming.def_index(), incoming.account_id());
    return true;
}

bool Inventory::AddProfileXp(uint32_t amount, uint32_t *levelsGained)
{
    if (levelsGained)
        *levelsGained = 0;
    if (!amount || m_profileLevel >= 40)
        return false;

    const uint32_t oldLevel = m_profileLevel;
    uint64_t total = static_cast<uint64_t>(m_profileXp) + amount;

    while (total >= 5000 && m_profileLevel < 40)
    {
        total -= 5000;
        ++m_profileLevel;
    }

    m_profileXp = (m_profileLevel >= 40) ? 0 : static_cast<uint32_t>(total);
    if (levelsGained)
        *levelsGained = m_profileLevel - oldLevel;
    WriteToFile();

    Platform::Print("progression: profile XP +%u -> level %u, xp %u/5000\n",
        amount, m_profileLevel, m_profileXp);
    return true;
}

uint32_t Inventory::ApplyWeeklyProfileXp(uint32_t baseXp, uint32_t *levelsGained)
{
    if (levelsGained)
        *levelsGained = 0;
    if (!baseXp)
        return 0;

    RefreshProfileWeek();

    const uint64_t start = m_weeklyBaseXp;
    const uint64_t end = std::min<uint64_t>(UINT32_MAX, start + baseXp);

    auto overlap = [](uint64_t begin, uint64_t finish, uint64_t lo, uint64_t hi) -> uint64_t
    {
        const uint64_t a = std::max(begin, lo);
        const uint64_t b = std::min(finish, hi);
        return b > a ? b - a : 0;
    };

    // 2021 weekly XP curve:
    //   3500 bonus XP paid at +3x, then 1500 bonus XP at +1x.
    //   Once roughly 11167 total XP has been earned in the week, further base
    //   XP is reduced to about 17.5%. 6167 base + 5000 bonus = 11167 total.
    const uint64_t normalBase = overlap(start, end, 0, 6167);
    const uint64_t reducedBase = end - start - normalBase;

    auto cumulativeBonus = [](uint64_t raw) -> uint64_t
    {
        const uint64_t triple = std::min<uint64_t>(raw * 3, 3500);
        const uint64_t secondRaw = raw > 1167 ? raw - 1167 : 0;
        const uint64_t single = std::min<uint64_t>(secondRaw, 1500);
        return triple + single;
    };

    const uint64_t bonus = cumulativeBonus(end) - cumulativeBonus(start);
    const uint64_t reducedAward = (reducedBase * 175) / 1000;
    uint64_t awarded64 = normalBase + reducedAward + bonus;
    if (awarded64 > UINT32_MAX)
        awarded64 = UINT32_MAX;

    m_weeklyBaseXp = static_cast<uint32_t>(end);
    const uint32_t awarded = static_cast<uint32_t>(awarded64);
    AddProfileXp(awarded, levelsGained);
    WriteToFile();

    Platform::Print(
        "progression: weekly XP base=%u awarded=%u base_this_week=%u\n",
        baseXp, awarded, m_weeklyBaseXp);
    return awarded;
}

bool Inventory::ImportRevivalProfile(
    uint32_t level, uint32_t xp,
    uint32_t profileWeek, uint32_t weeklyBaseXp,
    bool weeklyLevelRewardClaimed,
    uint32_t casePlaytimeSeconds, uint32_t caseDropsThisWeek,
    uint32_t nextCaseDropSeconds,
    RankId competitiveRank, uint32_t competitiveWins,
    int32_t competitiveRating, uint32_t competitiveMatches)
{
    const uint32_t rankValue = static_cast<uint32_t>(competitiveRank);
    if (level < 1 || level > 40
        || xp >= 5000
        || rankValue > static_cast<uint32_t>(RankGlobalElite)
        || competitiveRating < 600 || competitiveRating > 2300)
    {
        Platform::Print("progression: rejected malformed revival profile state\n");
        return false;
    }

    m_profileLevel = level;
    m_profileXp = xp;
    m_profileWeek = profileWeek;
    m_weeklyBaseXp = weeklyBaseXp;
    m_weeklyLevelRewardClaimed = weeklyLevelRewardClaimed;
    m_casePlaytimeSeconds = casePlaytimeSeconds;
    m_caseDropsThisWeek = std::min<uint32_t>(caseDropsThisWeek, 2u);
    m_nextCaseDropSeconds = nextCaseDropSeconds;
    m_competitiveRank = competitiveRank;
    m_competitiveWins = competitiveWins;
    m_competitiveRating = competitiveRating;
    m_competitiveMatches = competitiveMatches;
    WriteToFile();

    Platform::Print(
        "REVIVAL_PROFILE_SYNC_V2 level=%u xp=%u rank=%u wins=%u matches=%u rating=%d weekly_base=%u\n",
        m_profileLevel, m_profileXp, static_cast<uint32_t>(m_competitiveRank),
        m_competitiveWins, m_competitiveMatches, m_competitiveRating,
        m_weeklyBaseXp);
    return true;
}

bool Inventory::ApplyCompetitiveMatchResult(bool won, bool tied)
{
    const RankId oldRank = m_competitiveRank;
    const uint32_t oldWins = m_competitiveWins;

    ++m_competitiveMatches;
    if (won)
    {
        ++m_competitiveWins;
        m_competitiveRating += (m_competitiveWins <= 10) ? 45 : 30;
    }
    else if (tied)
    {
        m_competitiveRating += 3;
    }
    else
    {
        m_competitiveRating -= (m_competitiveWins < 10) ? 20 : 28;
    }
    m_competitiveRating = std::clamp(m_competitiveRating, 600, 2300);

    // CS:GO kept players unranked until 10 Competitive wins. Once placed, map
    // the persistent hidden rating onto the real 18 legacy skill-group ids.
    if (oldRank == RankNone && m_competitiveWins < 10)
    {
        m_competitiveRank = RankNone;
    }
    else
    {
        const int rank = std::clamp(1 + (m_competitiveRating - 600) / 100, 1, 18);
        m_competitiveRank = static_cast<RankId>(rank);
    }

    WriteToFile();
    Platform::Print(
        "matchmaking: comp result %s rating=%d rank=%u wins=%u matches=%u\n",
        won ? "win" : (tied ? "tie" : "loss"),
        m_competitiveRating, static_cast<uint32_t>(m_competitiveRank),
        m_competitiveWins, m_competitiveMatches);

    return oldRank != m_competitiveRank || oldWins != m_competitiveWins;
}

void Inventory::BuildProfilePersonaUpdate(CMsgSOMultipleObjects &update)
{
    CSOPersonaDataPublic persona;
    persona.set_player_level(m_profileLevel);
    persona.set_elevated_state(true);
    AddToMultipleObjects(update, SOTypePersonaDataPublic, persona);
}


bool Inventory::SetOperationMissionCard(uint32_t season,
    uint32_t missionCardId,
    CMsgSOMultipleObjects &update)
{
    if (season != GetConfig().OperationSeason())
    {
        Platform::Print("operation: refused mission card %u for season %u (active %u)\n",
            missionCardId, season, GetConfig().OperationSeason());
        return false;
    }

    if (!FindOperationCoin(0))
    {
        Platform::Print("operation: refused mission card %u - pass not activated\n", missionCardId);
        return false;
    }

    if (!m_itemSchema.GetOperationMissionCard(missionCardId))
    {
        Platform::Print("operation: refused unknown mission card %u\n", missionCardId);
        return false;
    }

    m_operationMissionId = missionCardId;
    AddOperationSeasonalState(update);
    WriteToFile();

    Platform::Print("operation: active mission card set to %u (season %u)\n",
        missionCardId, season);
    return true;
}

std::string Inventory::PreferredOperationMissionMap() const
{
    if (!m_operationMissionId)
    {
        return {};
    }
    return m_itemSchema.PreferredOperationMissionMap(m_operationMissionId);
}


bool Inventory::ApplyOperationQuestProgress(uint32_t questId,
    int normalPointsEarned,
    int bonusPointsEarned,
    CMsgSOMultipleObjects &update)
{
    CSOEconItem *coin = FindOperationCoin(0);
    if (!coin)
    {
        return false;
    }

    const QuestDefinition *quest = m_itemSchema.GetQuestDefinition(questId);
    if (!quest || quest->Goal() == 0)
    {
        Platform::Print("operation: ignored unknown/non-Riptide quest %u\n", questId);
        return false;
    }

    if (normalPointsEarned <= 0 && bonusPointsEarned <= 0)
    {
        return false;
    }

    OperationQuestProgressState &state = m_operationQuestProgress[questId];
    const uint32_t goal = quest->Goal();
    const uint32_t oldProgress = std::min(state.progress, goal ? goal - 1 : 0u);
    const uint64_t added = static_cast<uint32_t>(std::max(normalPointsEarned, 0));
    const uint64_t total = static_cast<uint64_t>(oldProgress) + added;

    // Revival mission loop:
    //   - preserve Valve's per-threshold star award
    //   - remove the historical 10/6-stars-per-week card cap
    //   - add +1 bonus star whenever the full mission is completed
    //   - wrap progress back around so the same mission can be played forever
    const uint64_t completedCycles = goal ? total / goal : 0;
    const uint32_t newProgress = goal
        ? static_cast<uint32_t>(total % goal)
        : oldProgress;

    uint64_t crossedSegments = 0;
    if (!quest->thresholds.empty())
    {
        if (completedCycles == 0)
        {
            for (uint32_t threshold : quest->thresholds)
            {
                if (oldProgress < threshold && total >= threshold)
                    ++crossedSegments;
            }
        }
        else
        {
            // Finish the current cycle.
            for (uint32_t threshold : quest->thresholds)
            {
                if (oldProgress < threshold)
                    ++crossedSegments;
            }

            // Any fully completed extra cycles.
            if (completedCycles > 1)
            {
                crossedSegments +=
                    (completedCycles - 1) * quest->thresholds.size();
            }

            // Thresholds already reached in the new wrapped cycle.
            for (uint32_t threshold : quest->thresholds)
            {
                if (newProgress >= threshold)
                    ++crossedSegments;
            }
        }
    }

    uint64_t stars64 =
        crossedSegments * static_cast<uint64_t>(quest->operationalPoints);
    stars64 += completedCycles; // +1 revival completion bonus per full mission
    const uint32_t starsEarnedNow = stars64 > UINT32_MAX
        ? UINT32_MAX : static_cast<uint32_t>(stars64);

    state.progress = newProgress;

    if (completedCycles > 0)
    {
        // A completed repeatable mission immediately becomes available again.
        // Clear uncommitted bonus progress and the active-card pin so a normal
        // Competitive queue after completion does not keep forcing the mission map.
        state.bonusPoints = 0;
        m_operationMissionId = 0;
    }
    else if (bonusPointsEarned > 0)
    {
        const uint64_t bonusSum = static_cast<uint64_t>(state.bonusPoints)
            + static_cast<uint32_t>(bonusPointsEarned);
        state.bonusPoints = bonusSum > UINT32_MAX
            ? UINT32_MAX : static_cast<uint32_t>(bonusSum);
    }

    if (starsEarnedNow > 0)
    {
        const uint32_t oldWalletStars = OperationStars(*coin);
        const uint64_t walletSum =
            static_cast<uint64_t>(oldWalletStars) + starsEarnedNow;
        const uint32_t newWalletStars = walletSum > UINT32_MAX
            ? UINT32_MAX : static_cast<uint32_t>(walletSum);

        CSOEconItemAttribute *starAttribute = nullptr;
        for (int i = 0; i < coin->attribute_size(); ++i)
        {
            if (coin->mutable_attribute(i)->def_index()
                == GetConfig().OperationStarAttribute())
            {
                starAttribute = coin->mutable_attribute(i);
                break;
            }
        }
        if (!starAttribute)
        {
            starAttribute = coin->add_attribute();
            starAttribute->set_def_index(GetConfig().OperationStarAttribute());
        }

        if (!m_itemSchema.SetAttributeUint32(starAttribute, newWalletStars))
        {
            return false;
        }

        const uint64_t earnedSum =
            static_cast<uint64_t>(m_operationEarnedStars) + starsEarnedNow;
        m_operationEarnedStars = earnedSum > UINT32_MAX
            ? UINT32_MAX : static_cast<uint32_t>(earnedSum);

        // Coin tiers still use Valve's Riptide mission-earned thresholds:
        // 33 Silver, 66 Gold, 100 Diamond. Purchased stars remain wallet-only.
        if (completedCycles > 0)
        {
            const uint64_t completedSum =
                static_cast<uint64_t>(m_operationMissionsCompleted)
                + completedCycles;
            m_operationMissionsCompleted = completedSum > UINT32_MAX
                ? UINT32_MAX : static_cast<uint32_t>(completedSum);
        }

        const uint32_t targetCoinDef = OperationCoinDefForEarnedStars();
        if (targetCoinDef && coin->def_index() != targetCoinDef)
        {
            Platform::Print(
                "operation: coin upgraded def %u -> %u at %u earned stars\n",
                coin->def_index(), targetCoinDef, m_operationEarnedStars);
            coin->set_def_index(targetCoinDef);
        }

        AddToMultipleObjects(update, *coin);
    }

    AddOperationQuestState(questId, update);
    AddOperationSeasonalState(update);
    WriteToFile();

    Platform::Print(
        "REVIVAL_REPEATABLE_MISSIONS_V1 quest=%u +%d normal +%d bonus "
        "progress %u/%u cycles=%llu +%u stars (earned=%u)\n",
        questId, normalPointsEarned, bonusPointsEarned,
        state.progress, goal,
        static_cast<unsigned long long>(completedCycles),
        starsEarnedNow, m_operationEarnedStars);
    return true;
}

bool Inventory::ApplySelectedOperationCompetitiveMission(
    std::string_view mapName,
    uint32_t roundsWon,
    bool wonMatch,
    CMsgSOMultipleObjects &update)
{
    if (!m_operationMissionId || mapName.empty())
    {
        return false;
    }

    const OperationMissionCard *card =
        m_itemSchema.GetOperationMissionCard(m_operationMissionId);
    if (!card)
    {
        return false;
    }

    const QuestDefinition *selected = nullptr;
    for (uint32_t questId : card->questIds)
    {
        const QuestDefinition *quest = m_itemSchema.GetQuestDefinition(questId);
        if (!quest || quest->gameMode.find("competitive") != 0)
        {
            continue;
        }

        bool mapMatches = quest->map == mapName;

        // Premier's Riptide mission uses lobby_mapveto rather than a concrete
        // BSP. In the revival that means "any map in our curated Competitive
        // pool" instead of trying to resurrect Valve's veto backend.
        if (quest->map == "lobby_mapveto")
        {
            mapMatches = true;
        }

        if (!mapMatches && quest->mapGroup.rfind("mg_", 0) == 0)
        {
            mapMatches = quest->mapGroup.substr(3) == mapName;
        }

        if (!mapMatches)
        {
            continue;
        }

        selected = quest;
        break;
    }

    if (!selected)
    {
        Platform::Print(
            "operation: active card %u has no supported Competitive mission for map %s\n",
            m_operationMissionId, std::string(mapName).c_str());
        return false;
    }

    OperationQuestProgressState &state =
        m_operationQuestProgress[selected->id];

    // Riptide's main Competitive missions are OR graphs represented by a
    // parent quest with expression "QQ:|...|...": complete by either winning
    // the match or accumulating 21 round wins across attempts. We intentionally
    // keep the stock parent quest id so the original Operation UI remains the
    // source of truth for selection/display.
    if (selected->expression.rfind("QQ:", 0) == 0)
    {
        const std::vector<uint32_t> children =
            m_itemSchema.QuestGraphChildren(selected->id);

        uint32_t roundChildId = 0;
        uint32_t matchChildId = 0;
        uint32_t roundGoal = 21;
        for (uint32_t childId : children)
        {
            const QuestDefinition *child =
                m_itemSchema.GetQuestDefinition(childId);
            if (!child)
            {
                continue;
            }

            if (child->expression.find("%act_win_round%")
                != std::string::npos)
            {
                roundChildId = childId;
                if (child->Goal())
                    roundGoal = child->Goal();
            }
            if (child->expression.find("%act_win_match%")
                != std::string::npos)
            {
                matchChildId = childId;
            }
        }

        const uint64_t roundTotal =
            static_cast<uint64_t>(state.repeatableRounds) + roundsWon;
        const bool completed =
            (wonMatch && matchChildId != 0)
            || (roundChildId != 0 && roundTotal >= roundGoal);

        if (!completed)
        {
            state.repeatableRounds = static_cast<uint32_t>(
                std::min<uint64_t>(
                    roundTotal,
                    roundGoal > 0 ? roundGoal - 1 : 0));

            // Mirror the accumulator into the real Riptide round-win child
            // quest. Panorama's stock graph then shows e.g. 14/21 instead of
            // the mission looking frozen while the revival tracks it privately.
            if (roundChildId)
            {
                OperationQuestProgressState &roundState =
                    m_operationQuestProgress[roundChildId];
                roundState.progress = state.repeatableRounds;
                roundState.bonusPoints = 0;
                AddOperationQuestState(roundChildId, update);
                AddOperationSeasonalState(update);
            }

            WriteToFile();
            Platform::Print(
                "REVIVAL_REPEATABLE_MISSIONS_V3 quest=%u map=%s rounds=%u/%u awaiting completion\n",
                selected->id, std::string(mapName).c_str(),
                state.repeatableRounds, roundGoal);
            return roundChildId != 0;
        }

        // Clear both real child branches before completing the parent. The
        // parent itself wraps back to zero in ApplyOperationQuestProgress(), so
        // after the reward animation the original mission UI is immediately
        // ready for another run.
        state.repeatableRounds = 0;
        for (uint32_t childId : children)
        {
            OperationQuestProgressState &childState =
                m_operationQuestProgress[childId];
            childState.progress = 0;
            childState.bonusPoints = 0;
            childState.repeatableRounds = 0;
            AddOperationQuestState(childId, update);
        }

        const bool changed = ApplyOperationQuestProgress(
            selected->id, 1, 0, update);

        Platform::Print(
            "REVIVAL_REPEATABLE_MISSIONS_V3 quest=%u map=%s completed via %s\n",
            selected->id, std::string(mapName).c_str(),
            wonMatch ? "match-win" : "round-goal");
        return changed;
    }

    int normalPoints = 0;
    if (selected->expression.find("%act_win_match%") != std::string::npos)
    {
        normalPoints = wonMatch ? 1 : 0;
    }
    else if (selected->expression.find("%act_win_round%") != std::string::npos)
    {
        normalPoints = static_cast<int>(
            std::min<uint32_t>(roundsWon, static_cast<uint32_t>(INT_MAX)));
    }
    else
    {
        // We do not synthesize unsupported kill/MVP/etc. counters. Leaving the
        // mission untouched is safer than awarding progress for the wrong stat.
        Platform::Print(
            "operation: selected Competitive quest %u uses unsupported expression '%s'\n",
            selected->id, selected->expression.c_str());
        return false;
    }

    if (normalPoints <= 0)
    {
        return false;
    }

    return ApplyOperationQuestProgress(
        selected->id, normalPoints, 0, update);
}

bool Inventory::CanSpendStars(int cost) const
{
    if (cost <= 0)
    {
        return true;
    }

    return FindOperationCoin(static_cast<uint32_t>(cost)) != nullptr;
}

// operation shop (revival): spend stars from the player's Operation coin
bool Inventory::SpendStars(int cost, CMsgSOMultipleObjects &update)
{
    if (cost <= 0)
    {
        return true;
    }

    CSOEconItem *item = FindOperationCoin(static_cast<uint32_t>(cost));
    if (!item)
    {
        return false;
    }

    const uint32_t oldStars = OperationStars(*item);
    CSOEconItemAttribute *starAttribute = nullptr;
    for (int i = 0; i < item->attribute_size(); i++)
    {
        if (item->mutable_attribute(i)->def_index() == GetConfig().OperationStarAttribute())
        {
            starAttribute = item->mutable_attribute(i);
            break;
        }
    }

    if (!starAttribute)
    {
        starAttribute = item->add_attribute();
        starAttribute->set_def_index(GetConfig().OperationStarAttribute());
    }

    const uint32_t newStars = oldStars - static_cast<uint32_t>(cost);
    if (!m_itemSchema.SetAttributeUint32(starAttribute, newStars))
    {
        return false;
    }

    AddToMultipleObjects(update, *item);
    AddOperationSeasonalState(update);
    WriteToFile();

    Platform::Print("operation shop: spent %d stars (coin def %u now has %u)\n",
        cost, item->def_index(), newStars);
    return true;
}

bool Inventory::UnequipItem(uint64_t itemId, CMsgSOMultipleObjects &update)
{
    uint32_t defIndex, paintKitIndex;
    if (IsDefaultItemId(itemId, defIndex, paintKitIndex))
    {
        // not supported
        assert(false);
        return false;
    }

    auto it = m_items.find(itemId);
    if (it == m_items.end())
    {
        assert(false);
        return false;
    }

    CSOEconItem &item = it->second;
    item.clear_equipped_state();

    AddToMultipleObjects(update, item);

    return true;
}

// this goes through everything on purpose
void Inventory::UnequipItem(uint32_t classId, uint32_t slotId, CMsgSOMultipleObjects &update)
{
    // check non default items first
    for (auto &pair : m_items)
    {
        CSOEconItem &item = pair.second;

        bool modified = false;

        for (auto it = item.mutable_equipped_state()->begin(); it != item.mutable_equipped_state()->end();)
        {
            if (it->new_class() == classId && it->new_slot() == slotId)
            {
                Platform::Print("Unequip %llu class %d slot %d\n", pair.first, classId, slotId);

                it = item.mutable_equipped_state()->erase(it);
                modified = true;
            }
            else
            {
                it++;
            }
        }

        if (modified)
        {
            AddToMultipleObjects(update, item);
        }
    }

    // check default equips
    for (auto it = m_defaultEquips.begin(); it != m_defaultEquips.end();)
    {
        if (it->class_id() == classId && it->slot_id() == slotId)
        {
            Platform::Print("Unequip %u class %d slot %d\n", it->item_definition(), classId, slotId);

            // mikkotodo is this correct???
            // mikkotodo rpobably not correct.. i gess we don't even have to do this
            // because the new equip overrides the old one
            // but we can't just remove it either because "update" would get fucked
            it->set_item_definition(0);
            AddToMultipleObjects(update, *it);

            it = m_defaultEquips.erase(it);
        }
        else
        {
            it++;
        }
    }
}

void Inventory::DestroyItem(ItemMap::iterator iterator, CMsgSOSingleObject &message)
{
    CSOEconItem item;
    item.set_id(iterator->second.id());

    ToSingleObject(message, item);

    m_items.erase(iterator);
}
