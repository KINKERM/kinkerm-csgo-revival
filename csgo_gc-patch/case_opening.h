#pragma once

class ItemSchema;
class Random;

struct LootListItem;
struct LootList;

class CaseOpening
{
public:
    CaseOpening(const ItemSchema &itemSchema, Random &random);

    bool SelectItemFromCrate(const CSOEconItem &crate, CSOEconItem &item);

private:
    const LootListItem *SelectLootListItem(const std::vector<const LootListItem *> &items, int pity, bool containsUnusuals);
    uint32_t RandomRarityForItems(const std::vector<const LootListItem *> &items, int pity, bool containsUnusuals);
    bool ShouldMakeStatTrak(const LootListItem &item, const LootList &lootList, bool containsUnusuals);

    // --- pity system ---
    int LoadPityCounter() const;
    void SavePityCounter(int value) const;
    const LootListItem *SelectItemOfRarity(const std::vector<const LootListItem *> &items, uint32_t rarity);

    const ItemSchema &m_itemSchema;
    Random &m_random;
};
