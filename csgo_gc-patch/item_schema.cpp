#include "stdafx.h"
#include "item_schema.h"
#include "config.h"
#include "keyvalue.h"
#include "random.h"

// ideally this would get parsed from the item schema...
static uint32_t ItemRarityFromString(std::string_view name)
{
    const std::pair<std::string_view, uint32_t> rarityNames[] = {
        { "default", ItemSchema::RarityDefault },
        { "common", ItemSchema::RarityCommon },
        { "uncommon", ItemSchema::RarityUncommon },
        { "rare", ItemSchema::RarityRare },
        { "mythical", ItemSchema::RarityMythical },
        { "legendary", ItemSchema::RarityLegendary },
        { "ancient", ItemSchema::RarityAncient },
        { "immortal", ItemSchema::RarityImmortal },
        { "unusual", ItemSchema::RarityUnusual },
    };

    for (const auto &pair : rarityNames)
    {
        if (pair.first == name)
        {
            return pair.second;
        }
    }

    assert(false);
    return ItemSchema::RarityCommon;
}

AttributeInfo::AttributeInfo(const KeyValue &key)
{
    std::string_view type = key.GetString("attribute_type");
    if (type.size())
    {
        if (type == "float")
        {
            m_type = AttributeType::Float;
        }
        else if (type == "uint32")
        {
            m_type = AttributeType::Uint32;
        }
        else if (type == "string")
        {
            m_type = AttributeType::String;
        }
        else
        {
            // not supported, fall back to float
            Platform::Print("Unsupported attribute type %s\n", std::string{ type }.c_str());
            m_type = AttributeType::Float;
        }
    }
    else
    {
        bool integer = key.GetNumber<int>("stored_as_integer");
        m_type = integer ? AttributeType::Uint32 : AttributeType::Float;
    }
}

ItemInfo::ItemInfo(uint32_t defIndex)
    : m_defIndex{ defIndex }
    , m_rarity{ ItemSchema::RarityCommon }
    , m_quality{ ItemSchema::QualityNormal }
    , m_level{ 1 }
    , m_supplyCrateSeries{ 0 }
    , m_isCoupon{ false }
    , m_willProduceStatTrak{ false }
{
    // RecursiveParseItem parses the rest
}

PaintKitInfo::PaintKitInfo(const KeyValue &key)
    : m_defIndex{ FromString<uint32_t>(key.Name()) }
    , m_rarity{ ItemSchema::RarityCommon } // rarity is not set here, done in ParsePaintKitRarities
{
    m_minFloat = key.GetNumber<float>("wear_remap_min", 0.0f);
    m_maxFloat = key.GetNumber<float>("wear_remap_max", 1.0f);
}

StickerKitInfo::StickerKitInfo(const KeyValue &key)
    : m_defIndex{ FromString<uint32_t>(key.Name()) }
    , m_rarity{ ItemSchema::RarityDefault } // mikkotodo revisit... currently using item rarity if this is default
{
    std::string_view rarity = key.GetString("item_rarity");
    if (rarity.size())
    {
        m_rarity = ItemRarityFromString(rarity);
    }
}

MusicDefinitionInfo::MusicDefinitionInfo(const KeyValue &key)
    : m_defIndex{ FromString<uint32_t>(key.Name()) }
{
    assert(m_defIndex);
}

uint32_t LootListItem::CaseRarity() const
{
    if (quality == ItemSchema::QualityUnusual)
    {
        return ItemSchema::RarityUnusual;
    }

    return rarity;
}

ItemSchema::ItemSchema()
{
    KeyValue itemSchema{ "root" };
    if (!itemSchema.ParseFromFile("csgo/scripts/items/items_game.txt"))
    {
        assert(false);
        return;
    }

    const KeyValue *itemsGame = itemSchema.GetSubkey("items_game");
    if (!itemsGame)
    {
        assert(false);
        return;
    }

    const KeyValue *itemsKey = itemsGame->GetSubkey("items");
    if (itemsKey)
    {
        ParseItems(itemsKey, itemsGame->GetSubkey("prefabs"));
    }

    const KeyValue *questsKey = itemsGame->GetSubkey("quests");
    if (questsKey)
    {
        ParseQuests(questsKey);
    }

    const KeyValue *seasonalOperationsKey = itemsGame->GetSubkey("seasonaloperations");
    if (seasonalOperationsKey)
    {
        ParseSeasonalOperation(seasonalOperationsKey, GetConfig().OperationSeason());
    }

    const KeyValue *attributesKey = itemsGame->GetSubkey("attributes");
    if (attributesKey)
    {
        ParseAttributes(attributesKey);
    }

    const KeyValue *stickerKitsKey = itemsGame->GetSubkey("sticker_kits");
    if (stickerKitsKey)
    {
        ParseStickerKits(stickerKitsKey);
    }

    const KeyValue *paintKitsKey = itemsGame->GetSubkey("paint_kits");
    if (paintKitsKey)
    {
        ParsePaintKits(paintKitsKey);
    }

    const KeyValue *paintKitsRarityKey = itemsGame->GetSubkey("paint_kits_rarity");
    if (paintKitsRarityKey)
    {
        ParsePaintKitRarities(paintKitsRarityKey);
    }

    // trade-up contracts (revival addition): collections (item_sets). must run
    // after paint kits + paint kit rarities so PaintedItemRarity is accurate.
    const KeyValue *itemSetsKey = itemsGame->GetSubkey("item_sets");
    if (itemSetsKey)
    {
        ParseItemSets(itemSetsKey);
    }

    const KeyValue *musicDefinitionsKey = itemsGame->GetSubkey("music_definitions");
    if (musicDefinitionsKey)
    {
        ParseMusicDefinitions(musicDefinitionsKey);
    }

    // unusual loot lists are not included in client_loot_lists
    // we need to parse these after items and paint kits but before client_loot_lists
    {
        KeyValue unusualLootLists{ "unusual_loot_lists" };

        if (unusualLootLists.ParseFromFile("csgo_gc/unusual_loot_lists.txt"))
        {
            ParseLootLists(&unusualLootLists, true);
        }
        else
        {
            // no knives sorry
            assert(false);
        }
    }

    const KeyValue *lootListsKey = itemsGame->GetSubkey("client_loot_lists");
    if (lootListsKey)
    {
        ParseLootLists(lootListsKey, false);
    }

    const KeyValue *revolvingLootListsKey = itemsGame->GetSubkey("revolving_loot_lists");
    if (revolvingLootListsKey)
    {
        ParseRevolvingLootLists(revolvingLootListsKey);
    }

    // trade-up contracts (revival addition): map each skin to its case's knife/
    // glove pool for the 5 Covert -> gold recipe. Runs last so every loot list
    // (including the unusual sublists) is parsed and linked.
    BuildUnusualPools();
}

float ItemSchema::AttributeFloat(const CSOEconItemAttribute *attribute) const
{
    auto it = m_attributeInfo.find(attribute->def_index());
    if (it == m_attributeInfo.end())
    {
        assert(false);
        return 0;
    }

    switch (it->second.m_type)
    {
    case AttributeType::Float:
        return *reinterpret_cast<const float *>(attribute->value_bytes().data());

    case AttributeType::Uint32:
        return *reinterpret_cast<const uint32_t *>(attribute->value_bytes().data());

    case AttributeType::String:
        return FromString<float>(attribute->value_bytes());

    default:
        assert(false);
        return 0;
    }
}

uint32_t ItemSchema::AttributeUint32(const CSOEconItemAttribute *attribute) const
{
    auto it = m_attributeInfo.find(attribute->def_index());
    if (it == m_attributeInfo.end())
    {
        assert(false);
        return 0;
    }

    switch (it->second.m_type)
    {
    case AttributeType::Float:
        return *reinterpret_cast<const float *>(attribute->value_bytes().data());

    case AttributeType::Uint32:
        return *reinterpret_cast<const uint32_t *>(attribute->value_bytes().data());

    case AttributeType::String:
        return FromString<uint32_t>(attribute->value_bytes());

    default:
        assert(false);
        return 0;
    }
}

std::string ItemSchema::AttributeString(const CSOEconItemAttribute *attribute) const
{
    auto it = m_attributeInfo.find(attribute->def_index());
    if (it == m_attributeInfo.end())
    {
        assert(false);
        return {};
    }

    switch (it->second.m_type)
    {
    case AttributeType::Float:
        return std::to_string(*reinterpret_cast<const float *>(attribute->value_bytes().data()));

    case AttributeType::Uint32:
        return std::to_string(*reinterpret_cast<const uint32_t *>(attribute->value_bytes().data()));

    case AttributeType::String:
        return attribute->value_bytes();

    default:
        assert(false);
        return {};
    }
}

bool ItemSchema::SetAttributeFloat(CSOEconItemAttribute *attribute, float value) const
{
    auto it = m_attributeInfo.find(attribute->def_index());
    if (it == m_attributeInfo.end())
    {
        assert(false);
        return false;
    }

    switch (it->second.m_type)
    {
    case AttributeType::Float:
    {
        attribute->set_value_bytes(&value, sizeof(value));
        break;
    }

    case AttributeType::Uint32:
    {
        uint32_t convert = static_cast<uint32_t>(value);
        attribute->set_value_bytes(&convert, sizeof(convert));
        break;
    }

    case AttributeType::String:
    {
        std::string convert = std::to_string(value);
        attribute->set_value_bytes(std::move(convert));
        break;
    }

    default:
        assert(false);
        return false;
    }

    return true;
}

bool ItemSchema::SetAttributeUint32(CSOEconItemAttribute *attribute, uint32_t value) const
{
    auto it = m_attributeInfo.find(attribute->def_index());
    if (it == m_attributeInfo.end())
    {
        assert(false);
        return false;
    }

    switch (it->second.m_type)
    {
    case AttributeType::Float:
    {
        float convert = static_cast<float>(value);
        attribute->set_value_bytes(&convert, sizeof(convert));
        break;
    }

    case AttributeType::Uint32:
    {
        attribute->set_value_bytes(&value, sizeof(value));
        break;
    }

    case AttributeType::String:
    {
        std::string convert = std::to_string(value);
        attribute->set_value_bytes(std::move(convert));
        break;
    }

    default:
        assert(false);
        return false;
    }

    return true;
}

bool ItemSchema::SetAttributeString(CSOEconItemAttribute *attribute, std::string_view value) const
{
    auto it = m_attributeInfo.find(attribute->def_index());
    if (it == m_attributeInfo.end())
    {
        assert(false);
        return false;
    }

    switch (it->second.m_type)
    {
    case AttributeType::Float:
    {
        float convert = FromString<float>(value);
        attribute->set_value_bytes(&convert, sizeof(convert));
        break;
    }

    case AttributeType::Uint32:
    {
        uint32_t convert = FromString<uint32_t>(value);
        attribute->set_value_bytes(&convert, sizeof(convert));
        break;
    }

    case AttributeType::String:
    {
        attribute->set_value_bytes(value.data(), value.size());
        break;
    }

    default:
        assert(false);
        return false;
    }

    return true;
}

const LootList *ItemSchema::GetCrateLootList(uint32_t crateDefIndex) const
{
    auto itemSearch = m_itemInfo.find(crateDefIndex);
    if (itemSearch == m_itemInfo.end())
    {
        assert(false);
        return nullptr;
    }

    assert(itemSearch->second.m_supplyCrateSeries);

    auto lootListSearch = m_revolvingLootLists.find(itemSearch->second.m_supplyCrateSeries);
    if (lootListSearch == m_revolvingLootLists.end())
    {
        assert(false);
        return nullptr;
    }

    return &lootListSearch->second;
}

const LootList *ItemSchema::GetDirectLootList(uint32_t defIndex) const
{
    auto itemSearch = m_itemInfo.find(defIndex);
    if (itemSearch == m_itemInfo.end() || itemSearch->second.m_lootListName.empty())
    {
        return nullptr;
    }

    auto lootListSearch = m_lootLists.find(itemSearch->second.m_lootListName);
    if (lootListSearch == m_lootLists.end())
    {
        Platform::Print("No direct loot list '%s' for def %u\n",
            itemSearch->second.m_lootListName.c_str(), defIndex);
        return nullptr;
    }

    return &lootListSearch->second;
}


const QuestDefinition *ItemSchema::GetQuestDefinition(uint32_t questId) const
{
    auto it = m_questDefinitions.find(questId);
    return (it == m_questDefinitions.end()) ? nullptr : &it->second;
}

const OperationMissionCard *ItemSchema::GetOperationMissionCardForQuest(uint32_t questId) const
{
    auto it = m_operationMissionCardByQuest.find(questId);
    if (it == m_operationMissionCardByQuest.end() || it->second >= m_operationMissionCards.size())
    {
        return nullptr;
    }

    return &m_operationMissionCards[it->second];
}

const OperationMissionCard *ItemSchema::GetOperationMissionCard(uint32_t cardId) const
{
    for (const OperationMissionCard &card : m_operationMissionCards)
    {
        if (card.id == cardId)
        {
            return &card;
        }
    }

    return nullptr;
}


bool ItemSchema::CreateItemFromLootListItem(Random &random,
    const LootListItem &lootListItem,
    bool statTrak,
    ItemOrigin origin,
    UnacknowledgedType unacknowledgedType,
    CSOEconItem &item) const
{
    if (!CreateItem(lootListItem.itemInfo->m_defIndex, origin, unacknowledgedType, item))
    {
        assert(false);
        return false;
    }

    // quality override, stattrak makes it strange if it's not an unusual
    if (statTrak && lootListItem.quality != ItemSchema::QualityUnusual)
    {
        item.set_quality(ItemSchema::QualityStrange);
    }
    else
    {
        // revival fix: real CS:GO gives painted weapon skins the "Unique"
        // quality, but the item schema leaves most weapon defs at the default
        // "Normal" (0). The Trade Up Contract only treats Unique (normal) or
        // Strange (StatTrak) quality items as eligible inputs, so normal skins
        // created at quality Normal never showed up in the contract while
        // StatTrak ones (Strange) did. Promote painted skins from Normal to
        // Unique so they qualify. Knives/gloves are QualityUnusual and stickers/
        // graffiti/music kits keep their own quality, so this only touches
        // regular weapon skins.
        uint32_t quality = lootListItem.quality;
        if (lootListItem.type == LootListItemPaintable
            && quality == ItemSchema::QualityNormal)
        {
            quality = ItemSchema::QualityUnique;
        }

        item.set_quality(quality);
    }

    // rarity override
    assert(lootListItem.rarity >= ItemSchema::RarityCommon && lootListItem.rarity <= ItemSchema::RarityImmortal);
    item.set_rarity(lootListItem.rarity);

    // setup type specficic attributes

    if (lootListItem.type == LootListItemSticker)
    {
        // mikkotodo anything else?
        CSOEconItemAttribute *attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeStickerId0);
        SetAttributeUint32(attribute, lootListItem.stickerKitInfo->m_defIndex);
    }
    else if (lootListItem.type == LootListItemSpray)
    {
        CSOEconItemAttribute *attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeStickerId0);
        SetAttributeUint32(attribute, lootListItem.stickerKitInfo->m_defIndex);

        // add AttributeSpraysRemaining when it's unsealed (mikkotodo how does the real gc do this)

        attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeSprayTintId);
        SetAttributeUint32(attribute, random.Integer<uint32_t>(ItemSchema::GraffitiTintMin, ItemSchema::GraffitiTintMax));
    }
    else if (lootListItem.type == LootListItemPatch)
    {
        // mikkotodo anything else?
        CSOEconItemAttribute *attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeStickerId0);
        SetAttributeUint32(attribute, lootListItem.stickerKitInfo->m_defIndex);
    }
    else if (lootListItem.type == LootListItemMusicKit)
    {
        CSOEconItemAttribute *attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeMusicId);
        SetAttributeUint32(attribute, lootListItem.musicDefinitionInfo->m_defIndex);
    }
    else if (lootListItem.type == LootListItemPaintable)
    {
        const PaintKitInfo *paintKitInfo = lootListItem.paintKitInfo;

        CSOEconItemAttribute *attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeTexturePrefab);
        SetAttributeUint32(attribute, paintKitInfo->m_defIndex);

        attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeTextureSeed);
        SetAttributeUint32(attribute, random.Integer<uint32_t>(0, 1000));

        // mikkotodo how does the float distribution work?
        attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeTextureWear);
        SetAttributeFloat(attribute, random.Float(paintKitInfo->m_minFloat, paintKitInfo->m_maxFloat));
    }
    else if (lootListItem.type == LootListItemNoAttribute)
    {
        // nothing
    }
    else
    {
        assert(false);
    }

    if (statTrak)
    {
        assert((lootListItem.type == LootListItemMusicKit) || (lootListItem.type == LootListItemPaintable));

        CSOEconItemAttribute *attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeKillEater);
        SetAttributeUint32(attribute, 0);

        // mikkotodo fix magic
        int scoreType = (lootListItem.type == LootListItemMusicKit) ? 1 : 0;

        attribute = item.add_attribute();
        attribute->set_def_index(ItemSchema::AttributeKillEaterScoreType);
        SetAttributeUint32(attribute, scoreType);
    }

    return true;
}

bool ItemSchema::CreateItem(uint32_t defIndex, ItemOrigin origin, UnacknowledgedType unacknowledgedType, CSOEconItem &econItem) const
{
    auto itemSearch = m_itemInfo.find(defIndex);
    if (itemSearch == m_itemInfo.end())
    {
        assert(false);
        return false;
    }

    const ItemInfo &itemInfo = itemSearch->second;

    // urgh wtf is this crap
    if (itemInfo.m_isCoupon)
    {
        assert(itemInfo.m_lootListName.size());
        auto lootListSearch = m_lootLists.find(itemInfo.m_lootListName);
        if (lootListSearch == m_lootLists.end())
        {
            assert(false);
            return false;
        }

        const LootList &lootList = lootListSearch->second;
        assert(lootList.subLists.size() == 0 && lootList.items.size() == 1);
        assert(lootList.willProduceStatTrak == false && lootList.isUnusual == false);

        Random random;

        return CreateItemFromLootListItem(random,
            lootList.items.front(),
            itemInfo.m_willProduceStatTrak,
            origin,
            unacknowledgedType,
            econItem);
    }

    econItem.set_inventory(InventoryUnacknowledged(unacknowledgedType));
    econItem.set_def_index(defIndex);
    econItem.set_quantity(1);
    econItem.set_level(itemInfo.m_level);
    econItem.set_quality(itemInfo.m_quality);
    econItem.set_flags(0);
    econItem.set_origin(origin);
    econItem.set_in_use(false);
    econItem.set_rarity(itemInfo.m_rarity);

    return true;
}

void ItemSchema::ParseItems(const KeyValue *itemsKey, const KeyValue *prefabsKey)
{
    m_itemInfo.reserve(itemsKey->SubkeyCount());

    for (const KeyValue &itemKey : *itemsKey)
    {
        if (itemKey.Name() == "default")
        {
            // ignore this
            continue;
        }

        uint32_t defIndex = FromString<uint32_t>(itemKey.Name());
        auto emplace = m_itemInfo.try_emplace(defIndex, defIndex);

        ParseItemRecursive(emplace.first->second, itemKey, prefabsKey);

        // FIXME: remove, temp slop to make sure we parse correctly
        auto &itemInfo = emplace.first->second;
        if (!itemInfo.m_isCoupon)
        {
            // FIXME: self opening purchases
            if (itemInfo.m_lootListName.size())
            {
                Platform::Print("Non coupon item associated loot list in %s!!!\n", itemInfo.m_name.c_str());
            }

            //assert(!itemInfo.m_lootListName.size());
            assert(!itemInfo.m_willProduceStatTrak);
        }
        else
        {
            assert(itemInfo.m_lootListName.size());
        }
    }
}

// ideally this would get parsed from the item schema...
static uint32_t ItemQualityFromString(std::string_view name)
{
    const std::pair<std::string_view, uint32_t> qualityNames[] = {
        { "normal", ItemSchema::QualityNormal },
        { "genuine", ItemSchema::QualityGenuine },
        { "vintage", ItemSchema::QualityVintage },
        { "unusual", ItemSchema::QualityUnusual },
        { "unique", ItemSchema::QualityUnique },
        { "community", ItemSchema::QualityCommunity },
        { "developer", ItemSchema::QualityDeveloper },
        { "selfmade", ItemSchema::QualitySelfmade },
        { "customized", ItemSchema::QualityCustomized },
        { "strange", ItemSchema::QualityStrange },
        { "completed", ItemSchema::QualityCompleted },
        { "haunted", ItemSchema::QualityHaunted },
        { "tournament", ItemSchema::QualityTournament },
    };

    for (const auto &pair : qualityNames)
    {
        if (pair.first == name)
        {
            return pair.second;
        }
    }

    assert(false);
    return ItemSchema::QualityUnique; // i guess???
}

// i hate my life
static std::vector<std::string_view> SplitString(std::string_view input, char delimiter)
{
    size_t offset = 0;
    std::vector<std::string_view> result;

    while (true)
    {
        size_t i = input.find(delimiter, offset);
        if (i == std::string_view::npos)
        {
            result.emplace_back(input.substr(offset));
            break;
        }

        result.emplace_back(input.substr(offset, i - offset));
        offset = i + 1;
    }

    return result;
}

void ItemSchema::ParseItemRecursive(ItemInfo &info, const KeyValue &itemKey, const KeyValue *prefabsKey)
{
    std::string_view prefabString = itemKey.GetString("prefab");
    if (prefabString.size() && prefabsKey)
    {
        // might have multiple specifications in a single statement
        std::vector<std::string_view> prefabNames = SplitString(prefabString, ' ');
        for (std::string_view prefabName : prefabNames)
        {
            const KeyValue *prefabKey = prefabsKey->GetSubkey(prefabName);
            if (prefabKey)
            {
                ParseItemRecursive(info, *prefabKey, prefabsKey);
            }
            else
            {
                // not available to us mortals...
                Platform::Print("No such prefab '%s'\n", std::string{ prefabName }.c_str());
            }
        }
    }

    std::string_view name = itemKey.GetString("name");
    if (name.size())
    {
        info.m_name = name;
    }

    std::string_view quality = itemKey.GetString("item_quality");
    if (quality.size())
    {
        info.m_quality = ItemQualityFromString(quality);
    }

    std::string_view rarity = itemKey.GetString("item_rarity");
    if (rarity.size())
    {
        info.m_rarity = ItemRarityFromString(rarity);
    }

    uint32_t minLevel = itemKey.GetNumber<uint32_t>("min_ilevel", 0);
    uint32_t maxLevel = itemKey.GetNumber<uint32_t>("max_ilevel", 0);
    if (minLevel && maxLevel)
    {
        assert(minLevel == maxLevel);
        info.m_level = minLevel;
    }

    std::string_view itemType = itemKey.GetString("item_type");
    if (itemType.size())
    {
        info.m_isCoupon = (itemType == "coupon");
    }

    std::string_view lootListName = itemKey.GetString("loot_list_name");
    if (lootListName.size())
    {
        info.m_lootListName = lootListName;
    }

    info.m_willProduceStatTrak = itemKey.GetNumber("will_produce_stattrak", false);

    const KeyValue *attributes = itemKey.GetSubkey("attributes");
    if (attributes)
    {
        const KeyValue *supplyCrateSeries = attributes->GetSubkey("set supply crate series");
        if (supplyCrateSeries)
        {
            info.m_supplyCrateSeries = supplyCrateSeries->GetNumber<uint32_t>("value");
        }
    }
}


static std::vector<uint32_t> ParsePositiveUintList(std::string_view input)
{
    std::vector<uint32_t> values;
    size_t offset = 0;

    while (offset < input.size())
    {
        size_t comma = input.find(',', offset);
        std::string_view token = (comma == std::string_view::npos)
            ? input.substr(offset)
            : input.substr(offset, comma - offset);

        uint32_t value = FromString<uint32_t>(token);
        if (value > 0)
        {
            values.push_back(value);
        }

        if (comma == std::string_view::npos)
        {
            break;
        }
        offset = comma + 1;
    }

    std::sort(values.begin(), values.end());
    values.erase(std::unique(values.begin(), values.end()), values.end());
    return values;
}

static void AppendQuestRange(std::string_view token, std::vector<uint32_t> &out)
{
    size_t dash = token.find('-');
    if (dash == std::string_view::npos)
    {
        uint32_t id = FromString<uint32_t>(token);
        if (id)
        {
            out.push_back(id);
        }
        return;
    }

    uint32_t first = FromString<uint32_t>(token.substr(0, dash));
    uint32_t last = FromString<uint32_t>(token.substr(dash + 1));
    if (!first || !last || last < first)
    {
        return;
    }

    for (uint32_t id = first; id <= last; ++id)
    {
        out.push_back(id);
        if (id == UINT32_MAX)
        {
            break;
        }
    }
}

void ItemSchema::ParseQuests(const KeyValue *questsKey)
{
    m_questDefinitions.reserve(questsKey->SubkeyCount());

    for (const KeyValue &questKey : *questsKey)
    {
        uint32_t id = FromString<uint32_t>(questKey.Name());
        if (!id)
        {
            continue;
        }

        QuestDefinition quest;
        quest.id = id;
        quest.operationalPoints = questKey.GetNumber<uint32_t>("operational_points", 0);
        quest.thresholds = ParsePositiveUintList(questKey.GetString("points"));

        // Tournament/challenge quests can live in the same table but are not
        // Operation-star missions. Keep only definitions that have both a goal
        // and an Operation star value.
        if (!quest.thresholds.empty() && quest.operationalPoints > 0)
        {
            m_questDefinitions.emplace(id, std::move(quest));
        }
    }
}

void ItemSchema::ParseSeasonalOperation(const KeyValue *seasonalOperationsKey, uint32_t season)
{
    const KeyValue *seasonKey = seasonalOperationsKey->GetSubkey(std::to_string(season));
    if (!seasonKey)
    {
        Platform::Print("operation: no seasonaloperations block for season %u\n", season);
        return;
    }

    for (const KeyValue &entry : *seasonKey)
    {
        if (entry.Name() != "quest_mission_card")
        {
            continue;
        }

        OperationMissionCard card;
        card.id = entry.GetNumber<uint32_t>("id", 0);
        card.maxStars = entry.GetNumber<uint32_t>("operational_points", 0);

        std::string_view quests = entry.GetString("quests");
        size_t offset = 0;
        while (offset < quests.size())
        {
            size_t comma = quests.find(',', offset);
            std::string_view token = (comma == std::string_view::npos)
                ? quests.substr(offset)
                : quests.substr(offset, comma - offset);
            AppendQuestRange(token, card.questIds);

            if (comma == std::string_view::npos)
            {
                break;
            }
            offset = comma + 1;
        }

        if (!card.id || !card.maxStars || card.questIds.empty())
        {
            continue;
        }

        size_t cardIndex = m_operationMissionCards.size();
        m_operationMissionCards.push_back(std::move(card));

        for (uint32_t questId : m_operationMissionCards.back().questIds)
        {
            if (m_questDefinitions.find(questId) != m_questDefinitions.end())
            {
                m_operationMissionCardByQuest[questId] = cardIndex;
            }
        }
    }

    Platform::Print("operation: parsed %zu mission cards and %zu Operation quests for season %u\n",
        m_operationMissionCards.size(), m_operationMissionCardByQuest.size(), season);
}

void ItemSchema::ParseAttributes(const KeyValue *attributesKey)
{
    m_attributeInfo.reserve(attributesKey->SubkeyCount());

    for (const KeyValue &attributeKey : *attributesKey)
    {
        uint32_t defIndex = FromString<uint32_t>(attributeKey.Name());
        assert(defIndex);
        m_attributeInfo.try_emplace(defIndex, attributeKey);
    }
}

void ItemSchema::ParseStickerKits(const KeyValue *stickerKitsKey)
{
    m_stickerKitInfo.reserve(stickerKitsKey->SubkeyCount());

    for (const KeyValue &stickerKitKey : *stickerKitsKey)
    {
        std::string_view name = stickerKitKey.GetString("name");

        m_stickerKitInfo.emplace(std::piecewise_construct,
            std::forward_as_tuple(name),
            std::forward_as_tuple(stickerKitKey));
    }
}

void ItemSchema::ParsePaintKits(const KeyValue *paintKitsKey)
{
    m_paintKitInfo.reserve(paintKitsKey->SubkeyCount());

    for (const KeyValue &paintKitKey : *paintKitsKey)
    {
        std::string_view name = paintKitKey.GetString("name");

        m_paintKitInfo.emplace(std::piecewise_construct,
            std::forward_as_tuple(name),
            std::forward_as_tuple(paintKitKey));
    }
}

void ItemSchema::ParsePaintKitRarities(const KeyValue *raritiesKey)
{
    for (const KeyValue &key : *raritiesKey)
    {
        PaintKitInfo *paintKitInfo = PaintKitInfoByName(key.Name());
        if (!paintKitInfo)
        {
            //assert(false);
            //Platform::Print("No such paint kit '%s'!!!\n", std::string{ key.Name() }.c_str());
            continue;
        }

        assert(paintKitInfo->m_rarity == RarityCommon);
        paintKitInfo->m_rarity = ItemRarityFromString(key.String());
    }
}

void ItemSchema::ParseMusicDefinitions(const KeyValue *musicDefinitionsKey)
{
    m_musicDefinitionInfo.reserve(musicDefinitionsKey->SubkeyCount());

    for (const KeyValue &musicDefinitionKey : *musicDefinitionsKey)
    {
        std::string_view name = musicDefinitionKey.GetString("name");

        m_musicDefinitionInfo.emplace(std::piecewise_construct,
            std::forward_as_tuple(name),
            std::forward_as_tuple(musicDefinitionKey));
    }
}

static void ParseAttributeAndItemName(std::string_view input, std::string_view &attribute, std::string_view &item)
{
    // fallbacks (mikkotodo unfuck)
    attribute = {};
    item = input;

    if (input[0] != '[')
        return;

    size_t attribEnd = input.find(']', 1);
    if (attribEnd == std::string_view::npos)
    {
        assert(false);
        return;
    }

    attribute = input.substr(1, attribEnd - 1);
    item = input.substr(attribEnd + 1);

    assert(attribute.size() && item.size());
}

static LootListItemType LootListItemTypeFromName(std::string_view name, std::string_view attributeName)
{
    if (attributeName.empty())
    {
        return LootListItemNoAttribute;
    }

    const std::pair<std::string_view, LootListItemType> mapNames[] = {
        { "sticker", LootListItemSticker },
        { "spray", LootListItemSpray },
        { "patch", LootListItemPatch },
        { "musickit", LootListItemMusicKit }
    };

    for (const auto &pair : mapNames)
    {
        if (pair.first == name)
        {
            return pair.second;
        }
    }

    return LootListItemPaintable;
}

void ItemSchema::ParseLootLists(const KeyValue *lootListsKey, bool unusual)
{
    m_lootLists.reserve(lootListsKey->SubkeyCount());

    for (const KeyValue &lootListKey : *lootListsKey)
    {
        auto emplace = m_lootLists.emplace(std::piecewise_construct,
            std::forward_as_tuple(lootListKey.Name()),
            std::forward_as_tuple());

        LootList &lootList = emplace.first->second;
        lootList.isUnusual = unusual;

        for (const KeyValue &entryKey : lootListKey)
        {
            std::string_view entryName = entryKey.Name();

            // check for options that we ignore
            if (entryName == "will_produce_stattrak")
            {
                lootList.willProduceStatTrak = true;
                continue;
            }

            // check for options that we ignore
            if (entryName == "all_entries_as_additional_drops"
                || entryName == "contains_patches_representing_organizations"
                || entryName == "contains_stickers_autographed_by_proplayers"
                || entryName == "contains_stickers_representing_organizations"
                || entryName == "limit_description_to_number_rnd"
                || entryName == "public_list_contents")
            {
                continue;
            }

            std::string entryNameKey{ entryKey.Name() };

            // check if it's another loot list
            auto listSearch = m_lootLists.find(entryNameKey);
            if (listSearch != m_lootLists.end())
            {
                lootList.subLists.push_back(&listSearch->second);
                continue;
            }

            // check for an item
            LootListItem item;
            if (ParseLootListItem(item, entryName))
            {
                if (unusual)
                {
                    // override the quality here...
                    item.quality = QualityUnusual;
                }

                lootList.items.push_back(item);
            }
            else
            {
                // what the fuck is this...
                Platform::Print("Unhandled loot list entry %s!!!!\n", entryNameKey.c_str());
            }
        }
    }
}

static uint32_t PaintedItemRarity(uint32_t itemRarity, uint32_t paintKitRarity)
{
    int rarity = (itemRarity - 1) + paintKitRarity;
    if (rarity < 0)
    {
        return 0;
    }

    if (rarity > ItemSchema::RarityAncient)
    {
        if (paintKitRarity == ItemSchema::RarityImmortal)
        {
            return ItemSchema::RarityImmortal;
        }

        return ItemSchema::RarityAncient;
    }

    return rarity;
}

// trade-up contracts (revival addition)
// parse the item_sets block (collections). each entry looks like:
//   "set_community_2"
//   {
//       "name" "#CSGO_set_community_2"
//       "items"
//       {
//           "[cu_m4a1_hot_rod]weapon_m4a1"  "1"
//           ...
//       }
//   }
// the value after each item is ignored; a skin's grade comes from the paint kit
// rarity (via PaintedItemRarity), exactly like ParseLootListItem computes it.
void ItemSchema::ParseItemSets(const KeyValue *itemSetsKey)
{
    m_collections.reserve(itemSetsKey->SubkeyCount());

    for (const KeyValue &setKey : *itemSetsKey)
    {
        const KeyValue *itemsKey = setKey.GetSubkey("items");
        if (!itemsKey)
        {
            continue;
        }

        Collection collection;
        collection.name = setKey.Name();

        for (const KeyValue &entryKey : *itemsKey)
        {
            if (entryKey.Name().empty())
            {
                continue;
            }

            std::string_view attributeName, itemName;
            ParseAttributeAndItemName(entryKey.Name(), attributeName, itemName);

            // only painted weapon skins ("[paintkit]weapon_xxx") are trade-up
            // inputs/outputs; skip anything without a paint kit attribute
            if (attributeName.empty())
            {
                continue;
            }

            const ItemInfo *itemInfo = ItemInfoByName(itemName);
            if (!itemInfo)
            {
                continue;
            }

            const PaintKitInfo *paintKitInfo = PaintKitInfoByName(attributeName);
            if (!paintKitInfo)
            {
                continue;
            }

            CollectionItem item;
            item.itemDefIndex = itemInfo->m_defIndex;
            item.paintKitDefIndex = paintKitInfo->m_defIndex;
            item.rarity = PaintedItemRarity(itemInfo->m_rarity, paintKitInfo->m_rarity);
            item.itemInfo = itemInfo;
            item.paintKitInfo = paintKitInfo;

            collection.items.push_back(item);
        }

        if (collection.items.empty())
        {
            continue;
        }

        size_t collectionIndex = m_collections.size();
        m_collections.push_back(std::move(collection));

        // build the reverse lookup for every skin in this collection
        const Collection &stored = m_collections[collectionIndex];
        for (const CollectionItem &item : stored.items)
        {
            uint64_t key = (static_cast<uint64_t>(item.itemDefIndex) << 32) | item.paintKitDefIndex;
            m_collectionByItem.try_emplace(key, collectionIndex);
        }
    }

    Platform::Print("Parsed %zu collections for trade-up contracts\n", m_collections.size());
}

const Collection *ItemSchema::FindCollectionForItem(uint32_t itemDefIndex,
    uint32_t paintKitDefIndex,
    const CollectionItem **outItem) const
{
    if (outItem)
    {
        *outItem = nullptr;
    }

    uint64_t key = (static_cast<uint64_t>(itemDefIndex) << 32) | paintKitDefIndex;
    auto search = m_collectionByItem.find(key);
    if (search == m_collectionByItem.end())
    {
        return nullptr;
    }

    const Collection &collection = m_collections[search->second];

    if (outItem)
    {
        for (const CollectionItem &item : collection.items)
        {
            if (item.itemDefIndex == itemDefIndex && item.paintKitDefIndex == paintKitDefIndex)
            {
                *outItem = &item;
                break;
            }
        }
    }

    return &collection;
}

bool ItemSchema::CreateRandomCollectionItem(Random &random,
    const std::vector<std::string_view> &collectionNames,
    ItemOrigin origin,
    UnacknowledgedType unacknowledgedType,
    CSOEconItem &item) const
{
    std::vector<const CollectionItem *> candidates;
    for (const Collection &collection : m_collections)
    {
        bool wanted = false;
        for (std::string_view name : collectionNames)
        {
            if (collection.name == name)
            {
                wanted = true;
                break;
            }
        }
        if (!wanted)
        {
            continue;
        }

        for (const CollectionItem &entry : collection.items)
        {
            if (entry.itemInfo && entry.paintKitInfo)
            {
                candidates.push_back(&entry);
            }
        }
    }

    if (candidates.empty())
    {
        Platform::Print("drops: no items found in weekly collection pool\n");
        return false;
    }

    // Match case/collection rarity behavior: choose the grade by rarity weight,
    // then choose uniformly among the skins of that grade.
    std::vector<uint32_t> rarities;
    float totalWeight = 0.0f;
    for (const CollectionItem *entry : candidates)
    {
        if (std::find(rarities.begin(), rarities.end(), entry->rarity) != rarities.end())
        {
            continue;
        }
        rarities.push_back(entry->rarity);
        totalWeight += GetConfig().GetRarityWeight(entry->rarity);
    }

    if (rarities.empty() || totalWeight <= 0.0f)
    {
        return false;
    }

    float roll = random.Float(0.0f, totalWeight);
    uint32_t chosenRarity = rarities.front();
    float accum = 0.0f;
    for (uint32_t rarity : rarities)
    {
        accum += GetConfig().GetRarityWeight(rarity);
        if (roll < accum)
        {
            chosenRarity = rarity;
            break;
        }
    }

    std::vector<const CollectionItem *> tier;
    for (const CollectionItem *entry : candidates)
    {
        if (entry->rarity == chosenRarity)
        {
            tier.push_back(entry);
        }
    }
    if (tier.empty())
    {
        return false;
    }

    const CollectionItem *chosen = tier[random.Integer<size_t>(0, tier.size() - 1)];

    LootListItem loot;
    loot.itemInfo = chosen->itemInfo;
    loot.type = LootListItemPaintable;
    loot.paintKitInfo = chosen->paintKitInfo;
    loot.rarity = chosen->rarity;
    loot.quality = chosen->itemInfo->m_quality;

    return CreateItemFromLootListItem(
        random, loot, false, origin, unacknowledgedType, item);
}

// trade-up contracts (revival addition) --- 5 Covert -> gold recipe
// collect every unusual (knife/glove) leaf list reachable from a loot list
static void CollectUnusualLists(const LootList *list, std::vector<const LootList *> &out)
{
    if (list->isUnusual)
    {
        // unusual lists are leaves (their items are the golds)
        out.push_back(list);
        return;
    }

    for (const LootList *sub : list->subLists)
    {
        CollectUnusualLists(sub, out);
    }
}

// collect every painted-skin entry reachable from a loot list, ignoring unusual
// (gold) sublists
static void CollectNormalSkins(const LootList *list, std::vector<const LootListItem *> &out)
{
    if (list->isUnusual)
    {
        return;
    }

    for (const LootListItem &item : list->items)
    {
        if (item.type == LootListItemPaintable)
        {
            out.push_back(&item);
        }
    }

    for (const LootList *sub : list->subLists)
    {
        CollectNormalSkins(sub, out);
    }
}

// A case's loot list contains the collection's skins (in rarity-tier sublists)
// plus exactly one unusual sublist (its knife/glove pool - this is how the pity
// system finds golds). So for every loot list that reaches exactly one unusual
// list, we map each of its skins to that pool. A Covert input can then be traded
// up into a random gold from its case's pool.
void ItemSchema::BuildUnusualPools()
{
    for (const auto &pair : m_lootLists)
    {
        const LootList &list = pair.second;

        std::vector<const LootList *> unusuals;
        CollectUnusualLists(&list, unusuals);
        if (unusuals.size() != 1)
        {
            // no pool, or ambiguous (multiple) - skip
            continue;
        }

        std::vector<const LootListItem *> skins;
        CollectNormalSkins(&list, skins);

        for (const LootListItem *skin : skins)
        {
            if (!skin->itemInfo || !skin->paintKitInfo)
            {
                continue;
            }

            uint64_t key = (static_cast<uint64_t>(skin->itemInfo->m_defIndex) << 32)
                | skin->paintKitInfo->m_defIndex;

            // first mapping wins; skins in one case share a single pool
            m_unusualPoolByItem.try_emplace(key, unusuals.front());
        }
    }

    Platform::Print("Mapped %zu skins to gold (knife/glove) pools for trade-ups\n",
        m_unusualPoolByItem.size());
}

const LootList *ItemSchema::FindUnusualPoolForItem(uint32_t itemDefIndex,
    uint32_t paintKitDefIndex) const
{
    uint64_t key = (static_cast<uint64_t>(itemDefIndex) << 32) | paintKitDefIndex;
    auto search = m_unusualPoolByItem.find(key);
    if (search == m_unusualPoolByItem.end())
    {
        return nullptr;
    }

    return search->second;
}

// "gold only" case (revival addition)
// Walks every unusual (knife/glove) loot list in the schema and collects each
// painted gold entry, then returns a uniform-random one. This spans all
// collections, so opening the gold-only case can yield any gold in the game.
const LootListItem *ItemSchema::PickRandomGold(Random &random) const
{
    std::vector<const LootListItem *> golds;

    for (const auto &pair : m_lootLists)
    {
        const LootList &list = pair.second;
        if (!list.isUnusual)
        {
            continue;
        }

        // unusual lists are leaves - their items are the golds
        for (const LootListItem &item : list.items)
        {
            if (item.type == LootListItemPaintable && item.itemInfo && item.paintKitInfo)
            {
                golds.push_back(&item);
            }
        }
    }

    if (golds.empty())
    {
        return nullptr;
    }

    return golds[random.Integer<size_t>(0, golds.size() - 1)];
}

// mikkotodo rewrite this function
bool ItemSchema::ParseLootListItem(LootListItem &item, std::string_view name)
{
    // check for an attribute + item combo
    std::string_view attributeName, itemName;
    ParseAttributeAndItemName(name, attributeName, itemName);

    const ItemInfo *itemInfo = ItemInfoByName(itemName);
    if (!itemInfo)
    {
        Platform::Print("No such item %s!!!\n", std::string{ itemName }.c_str());
        return false;
    }

    item.itemInfo = itemInfo;
    item.type = LootListItemTypeFromName(itemName, attributeName);

    // until proven otherwise...
    item.rarity = itemInfo->m_rarity;
    item.quality = itemInfo->m_quality;

    if (item.type == LootListItemNoAttribute)
    {
        // no attribute
    }
    else if (item.type == LootListItemSticker || item.type == LootListItemSpray || item.type == LootListItemPatch)
    {
        // the attribute is the sticker name
        item.stickerKitInfo = StickerKitInfoByName(attributeName);
        if (!item.stickerKitInfo)
        {
            Platform::Print("WARNING: No such sticker kit %s\n", std::string{ attributeName }.c_str());
            return false;
        }

        // sticker kits affect the item rarity (mikkotodo how do these work, something like PaintedItemRarity???)
        assert(itemInfo->m_rarity == 1);

        if (item.stickerKitInfo->m_rarity)
        {
            item.rarity = item.stickerKitInfo->m_rarity;
        }
    }
    else if (item.type == LootListItemMusicKit)
    {
        // the attribute is the music definition name
        item.musicDefinitionInfo = MusicDefinitionInfoByName(attributeName);
        if (!item.musicDefinitionInfo)
        {
            Platform::Print("WARNING: No such music definition %s\n", std::string{ attributeName }.c_str());
            return false;
        }
    }
    else
    {
        // probably a paint kit
        assert(item.type == LootListItemPaintable);
        item.paintKitInfo = PaintKitInfoByName(attributeName);
        if (!item.paintKitInfo)
        {
            assert(false);
            Platform::Print("WARNING: No such paint kit %s\n", std::string{ attributeName }.c_str());
            return false;
        }

        // paint kits affect the item rarity
        item.rarity = PaintedItemRarity(itemInfo->m_rarity, item.paintKitInfo->m_rarity);
    }

    return true;
}

void ItemSchema::ParseRevolvingLootLists(const KeyValue *revolvingLootListsKey)
{
    m_revolvingLootLists.reserve(revolvingLootListsKey->SubkeyCount());

    for (const KeyValue &revolvingLootListKey : *revolvingLootListsKey)
    {
        uint32_t index = FromString<uint32_t>(revolvingLootListKey.Name());
        assert(index);

        // ugh
        std::string lootListName = std::string{ revolvingLootListKey.String() };

        auto it = m_lootLists.find(lootListName);
        if (it == m_lootLists.end())
        {
            //Platform::Print("Ignoring revolving loot list %s\n", lootListName.c_str());
            continue;
        }

        m_revolvingLootLists.try_emplace(index, it->second);
    }
}

ItemInfo *ItemSchema::ItemInfoByName(std::string_view name)
{
    for (auto &pair : m_itemInfo)
    {
        const ItemInfo &info = pair.second;
        if (info.m_name == name)
        {
            return &pair.second;
        }
    }

    assert(false);
    return nullptr;
}

StickerKitInfo *ItemSchema::StickerKitInfoByName(std::string_view name)
{
    auto it = m_stickerKitInfo.find(std::string{ name });
    if (it == m_stickerKitInfo.end())
    {
        assert(false);
        return nullptr;
    }

    return &it->second;
}

PaintKitInfo *ItemSchema::PaintKitInfoByName(std::string_view name)
{
    auto it = m_paintKitInfo.find(std::string{ name });
    if (it == m_paintKitInfo.end())
    {
        //assert(false);
        return nullptr;
    }

    return &it->second;
}

MusicDefinitionInfo *ItemSchema::MusicDefinitionInfoByName(std::string_view name)
{
    auto it = m_musicDefinitionInfo.find(std::string{ name });
    if (it == m_musicDefinitionInfo.end())
    {
        assert(false);
        return nullptr;
    }

    return &it->second;
}
