#include "stdafx.h"
#include "config.h"
#include "keyvalue.h"
#include "random.h"

constexpr const char *ConfigFilePath = "csgo_gc/config.txt";

const GCConfig &GetConfig()
{
    static GCConfig instance;
    return instance;
}

GCConfig::GCConfig()
{
    KeyValue config{ "config" };

    if (!config.ParseFromFile(ConfigFilePath))
    {
        return;
    }

    m_logOutput = config.GetNumber("log_output", m_logOutput);

    m_appIdOverride = config.GetNumber("appid_override", m_appIdOverride);
    m_showCsgoGCServersOnly = config.GetNumber("show_csgo_gc_servers_only", m_showCsgoGCServersOnly);

    const KeyValue *ranks = config.GetSubkey("ranks");
    if (ranks)
    {
        m_competitiveRank = ranks->GetNumber("competitive_rank", m_competitiveRank);
        m_competitiveWins = ranks->GetNumber("competitive_wins", m_competitiveWins);

        m_wingmanRank = ranks->GetNumber("wingman_rank", m_wingmanRank);
        m_wingmanWins = ranks->GetNumber("wingman_wins", m_wingmanWins);

        m_dangerZoneRank = ranks->GetNumber("dangerzone_rank", m_dangerZoneRank);
        m_dangerZoneWins = ranks->GetNumber("dangerzone_wins", m_dangerZoneWins);
    }

    m_destroyUsedItems = config.GetNumber("destroy_used_items", m_destroyUsedItems);

    // trade-up contracts (revival addition): the crate that performs 5 Covert -> gold
    m_goldTradeUpCrate = config.GetNumber("gold_tradeup_crate", m_goldTradeUpCrate);

    // "gold only" case (revival addition): the crate that always rolls a gold
    m_goldOnlyCrate = config.GetNumber("gold_only_crate", m_goldOnlyCrate);

    const KeyValue *rarityWeights = config.GetSubkey("rarity_weights");
    if (rarityWeights)
    {
        m_rarityWeights.clear();
        m_rarityWeights.reserve(rarityWeights->SubkeyCount());

        for (const KeyValue &subkey : *rarityWeights)
        {
            RarityWeight weight;
            weight.rarity = FromString<uint32_t>(subkey.Name());
            weight.weight = FromString<float>(subkey.String());
            m_rarityWeights.push_back(weight);
        }
    }

    // operation shop (revival): star costs per reward + (optional) coin defs / attr
    const KeyValue *opShop = config.GetSubkey("operation_shop");
    if (opShop)
    {
        m_operationStarAttribute = opShop->GetNumber("star_attribute", m_operationStarAttribute);
        m_operationSeason = opShop->GetNumber("season", m_operationSeason);
        m_operationPassDef = opShop->GetNumber("pass_def", m_operationPassDef);
        m_operationActivationCoinDef = opShop->GetNumber("activation_coin_def", m_operationActivationCoinDef);

        const KeyValue *coinDefs = opShop->GetSubkey("coin_defs");
        if (coinDefs)
        {
            m_operationCoinDefs.clear();
            for (const KeyValue &subkey : *coinDefs)
            {
                m_operationCoinDefs.push_back(FromString<uint32_t>(subkey.Name()));
            }
        }

        const KeyValue *rewards = opShop->GetSubkey("rewards");
        if (rewards)
        {
            m_operationShopRewards.clear();
            for (const KeyValue &subkey : *rewards)
            {
                ShopReward reward;
                reward.defIndex = FromString<uint32_t>(subkey.Name());
                reward.cost = FromString<int>(subkey.String());
                m_operationShopRewards.push_back(reward);
            }
        }

        const KeyValue *starPacks = opShop->GetSubkey("star_packs");
        if (starPacks)
        {
            m_operationStarPacks.clear();
            for (const KeyValue &subkey : *starPacks)
            {
                OperationStarPack pack;
                pack.defIndex = FromString<uint32_t>(subkey.Name());
                pack.stars = FromString<uint32_t>(subkey.String());
                m_operationStarPacks.push_back(pack);
            }
        }
    }

    m_vacBanned = config.GetNumber("vac_banned", m_vacBanned);
    m_commendedFriendly = config.GetNumber("cmd_friendly", m_commendedFriendly);
    m_commendedTeaching = config.GetNumber("cmd_teaching", m_commendedTeaching);
    m_commendedLeader = config.GetNumber("cmd_leader", m_commendedLeader);
    m_level = config.GetNumber("player_level", m_level);
    m_xp = config.GetNumber("player_cur_xp", m_xp);
}

float GCConfig::GetRarityWeight(uint32_t rarity) const
{
    for (const RarityWeight &weight : m_rarityWeights)
    {
        if (weight.rarity == rarity)
        {
            return weight.weight;
        }
    }

    return 0;
}

int GCConfig::OperationShopCost(uint32_t defIndex) const
{
    for (const ShopReward &reward : m_operationShopRewards)
    {
        if (reward.defIndex == defIndex)
        {
            return reward.cost;
        }
    }

    return 0;
}

uint32_t GCConfig::OperationStarPackValue(uint32_t defIndex) const
{
    for (const OperationStarPack &pack : m_operationStarPacks)
    {
        if (pack.defIndex == defIndex)
        {
            return pack.stars;
        }
    }

    return 0;
}
