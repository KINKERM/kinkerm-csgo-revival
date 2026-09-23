// Modified case_opening.cpp for csgo_gc (BSD-2-Clause, (c) Mikko Kokko).
// Adds a pity system on top of the original case-opening logic:
//   - tracks opens since the last gold (unusual) in csgo_gc/pity.txt
//   - gold odds ramp up the longer you go without one
//   - a gold is GUARANTEED once an open would reach PityMax (default 350)
//   - the counter resets to 0 whenever you hit a gold
// Everything else is unchanged from upstream.

#include "stdafx.h"
#include "case_opening.h"
#include "config.h"
#include "item_schema.h"
#include "random.h"

#include <cstdio>

// --- pity system tunables ---
static constexpr const char *PityFilePath = "csgo_gc/pity.txt";
static constexpr int PityMax = 350;         // guaranteed gold at this many consecutive non-gold opens
static constexpr float PityBoost = 40.0f;   // how strongly gold odds ramp up as you approach PityMax

CaseOpening::CaseOpening(const ItemSchema &itemSchema, Random &random)
    : m_itemSchema{ itemSchema }
    , m_random{ random }
{
}

// returns true if there are unusuals (stattrak able)
static bool GetLootListItems(const LootList &lootList, std::vector<const LootListItem *> &items)
{
    bool unusuals = lootList.isUnusual;

    for (const LootList *other : lootList.subLists)
    {
        unusuals |= GetLootListItems(*other, items);
    }

    for (const LootListItem &item : lootList.items)
    {
        items.push_back(&item);
    }

    return unusuals;
}

static bool CompareRarity(const LootListItem *a, const LootListItem *b) { return a->CaseRarity() < b->CaseRarity(); }
static bool RarityLower(const LootListItem *a, uint32_t b) { return a->CaseRarity() < b; }
static bool RarityUpper(uint32_t a, const LootListItem *b) { return a < b->CaseRarity(); }

// --- pity system: persistent counter stored next to the other csgo_gc files ---
int CaseOpening::LoadPityCounter() const
{
    int value = 0;
    FILE *file = fopen(PityFilePath, "rb");
    if (file)
    {
        if (fscanf(file, "%d", &value) != 1)
            value = 0;
        fclose(file);
    }
    return (value < 0) ? 0 : value;
}

void CaseOpening::SavePityCounter(int value) const
{
    FILE *file = fopen(PityFilePath, "wb");
    if (file)
    {
        fprintf(file, "%d", value);
        fclose(file);
    }
}

bool CaseOpening::SelectItemFromDirectLootList(const LootList &lootList, CSOEconItem &item)
{
    std::vector<const LootListItem *> lootListItems;
    lootListItems.reserve(32);

    const bool containsUnusuals = GetLootListItems(lootList, lootListItems);
    if (lootListItems.empty())
    {
        Platform::Print("operation reward: direct loot list was empty\n");
        return false;
    }

    std::sort(lootListItems.begin(), lootListItems.end(), CompareRarity);

    // Operation rewards use the normal rarity weighting but not the case pity
    // system. They also never roll StatTrak just because a case could.
    const LootListItem *lootListItem = SelectLootListItem(lootListItems, 0, containsUnusuals);
    if (!lootListItem)
    {
        return false;
    }

    return m_itemSchema.CreateItemFromLootListItem(
        m_random,
        *lootListItem,
        false,
        ItemOriginPurchased,
        UnacknowledgedPurchased,
        item);
}

bool CaseOpening::SelectItemFromCrate(const CSOEconItem &crate, CSOEconItem &item)
{
    const LootList *lootList = m_itemSchema.GetCrateLootList(crate.def_index());
    if (!lootList)
    {
        assert(false);
        return false;
    }

    assert(lootList->subLists.empty() != lootList->items.empty());

    std::vector<const LootListItem *> lootListItems;
    lootListItems.reserve(32); // overkill
    bool containsUnusuals = GetLootListItems(*lootList, lootListItems);
    if (!lootListItems.size())
    {
        assert(false);
        return false;
    }

    // must be sorted for SelectLootListItem and friends
    std::sort(lootListItems.begin(), lootListItems.end(), CompareRarity);

    // --- PITY SYSTEM ---
    // How many opens since our last gold (unusual)?
    int pity = LoadPityCounter();

    const LootListItem *lootListItem = nullptr;

    // Guaranteed gold once this open would reach PityMax (only if the case has one).
    if (containsUnusuals && (pity + 1) >= PityMax)
    {
        lootListItem = SelectItemOfRarity(lootListItems, ItemSchema::RarityUnusual);
    }

    // Otherwise roll normally, but with gold odds boosted by the current pity.
    if (!lootListItem)
    {
        lootListItem = SelectLootListItem(lootListItems, pity, containsUnusuals);
    }

    if (!lootListItem)
    {
        assert(false);
        return false;
    }

    // Reset the counter on a gold, otherwise increment it.
    if (lootListItem->CaseRarity() == ItemSchema::RarityUnusual)
        SavePityCounter(0);
    else
        SavePityCounter(pity + 1);
    // --- END PITY SYSTEM ---

    bool statTrak = ShouldMakeStatTrak(*lootListItem, *lootList, containsUnusuals);
    return m_itemSchema.CreateItemFromLootListItem(m_random, *lootListItem, statTrak, ItemOriginCrate, UnacknowledgedFoundInCrate, item);
}


bool CaseOpening::SelectItemFromDirectLootList(const LootList &lootList, CSOEconItem &item)
{
    std::vector<const LootListItem *> lootListItems;
    lootListItems.reserve(32);
    GetLootListItems(lootList, lootListItems);

    if (lootListItems.empty())
    {
        Platform::Print("operation shop: direct loot list is empty\n");
        return false;
    }

    // Reuse the normal rarity-weighted picker, but without case pity and without
    // StatTrak generation. This matches the operation-store style of drawing a
    // random item from a collection/dossier/capsule reward definition.
    std::sort(lootListItems.begin(), lootListItems.end(), CompareRarity);
    const LootListItem *lootListItem = SelectLootListItem(lootListItems, 0, false);
    if (!lootListItem)
    {
        return false;
    }

    return m_itemSchema.CreateItemFromLootListItem(
        m_random,
        *lootListItem,
        false,
        ItemOriginPurchased,
        UnacknowledgedPurchased,
        item);
}

// get a range of loot list items with a specific rarity from a vector sorted by rarity
static std::pair<size_t, size_t> FindRarityRange(const std::vector<const LootListItem *> &items, uint32_t rarity)
{
    // MUST have items and they MUST be sorted
    assert(items.size() && std::is_sorted(items.begin(), items.end(), CompareRarity));

    auto lower = std::lower_bound(items.begin(), items.end(), rarity, RarityLower);
    auto upper = std::upper_bound(lower, items.end(), rarity, RarityUpper);

    size_t begin = std::distance(items.begin(), lower);
    size_t end = std::distance(items.begin(), upper);

    return std::make_pair(begin, end);
}

// --- pity system: pick a random item of an exact rarity (used for the guaranteed gold) ---
const LootListItem *CaseOpening::SelectItemOfRarity(const std::vector<const LootListItem *> &items, uint32_t rarity)
{
    auto [begin, end] = FindRarityRange(items, rarity);
    if (begin == end)
    {
        return nullptr;
    }

    size_t index = m_random.Integer(begin, end - 1);
    return items[index];
}

const LootListItem *CaseOpening::SelectLootListItem(const std::vector<const LootListItem *> &items, int pity, bool containsUnusuals)
{
    uint32_t rarity = RandomRarityForItems(items, pity, containsUnusuals);

    auto [begin, end] = FindRarityRange(items, rarity);
    if (begin == end)
    {
        assert(false);
        return nullptr;
    }

    size_t index = m_random.Integer(begin, end - 1);
    return items[index];
}

uint32_t CaseOpening::RandomRarityForItems(const std::vector<const LootListItem *> &items, int pity, bool containsUnusuals)
{
    // MUST have items and they MUST be sorted
    assert(items.size() && std::is_sorted(items.begin(), items.end(), CompareRarity));

    std::vector<RarityWeight> weights;
    weights.reserve(items.size()); // overkill

    float totalWeight = 0;

    // Pity ramp: no effect at pity 0, ramping up as we approach PityMax.
    // Quadratic (t*t) so it stays close to normal early and climbs hard late.
    float pityMultiplier = 1.0f;
    if (containsUnusuals && pity > 0)
    {
        float t = static_cast<float>(pity) / static_cast<float>(PityMax); // 0..1
        pityMultiplier = 1.0f + PityBoost * (t * t);
    }

    // items are sorted by rarity, so iterate through the available rarities like this
    for (size_t i = 0; i < items.size(); i++)
    {
        uint32_t rarity = items[i]->CaseRarity();
        float weight = GetConfig().GetRarityWeight(rarity);

        // boost the gold (unusual) tier according to the pity ramp
        if (rarity == ItemSchema::RarityUnusual)
        {
            weight *= pityMultiplier;
        }

        weights.push_back({ rarity, weight });
        totalWeight += weight;

        // skip over any duplicate rarities
        while (i + 1 < items.size() && items[i]->CaseRarity() == items[i + 1]->CaseRarity())
        {
            i++;
        }
    }

    // play the game of chance...
    float value = m_random.Float(0.0f, totalWeight);
    float accum = 0.0f;

    for (const RarityWeight &pair : weights)
    {
        accum += pair.weight;
        if (value < accum)
        {
            return pair.rarity;
        }
    }

    assert(false);
    return 0;
}

bool CaseOpening::ShouldMakeStatTrak(const LootListItem &item, const LootList &lootList, bool containsUnusuals)
{
    if (lootList.willProduceStatTrak)
    {
        // must be stattrak
        return true;
    }

    if (!containsUnusuals)
    {
        // not a chance
        return false;
    }

    // unusual stattraks only valid below id 1000
    if (item.quality == ItemSchema::QualityUnusual
        && item.itemInfo->m_defIndex >= 1000)
    {
        return false;
    }

    // roll the dice
    return (m_random.Integer(1, 10) == 1);
}
