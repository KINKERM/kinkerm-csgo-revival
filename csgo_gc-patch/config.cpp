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

const ShopReward *GCConfig::OperationShopReward(uint32_t redeemId) const
{
    // Valve's native Operation redeem message sends CRC32(item_name), not a
    // reward row or item definition. Map the real Riptide season-10 ids to the
    // reward defs already configured in operation_shop.rewards.
    uint32_t defIndex = 0;
    switch (redeemId)
    {
    case 3161140792u: defIndex = 4795; break; // crate_patch_pack03
    case 486239559u:  defIndex = 4783; break; // crate_sticker_pack_op_riptide_capsule
    case 3394577585u: defIndex = 4779; break; // crate_sticker_pack_riptide_surfshop
    case 766835476u:  defIndex = 4790; break; // crate_community_29
    case 4262277037u: defIndex = 4788; break; // Train Covert
    case 2179775250u: defIndex = 4787; break; // Train Classified
    case 1690286011u: defIndex = 4786; break; // Train Restricted
    case 3750698461u: defIndex = 4785; break; // Train Mil-Spec
    case 3820610303u: defIndex = 4794; break; // Mirage 2021
    case 205604202u:  defIndex = 4793; break; // Dust II 2021
    case 1519885759u: defIndex = 4792; break; // Vertigo 2021
    case 3278305639u: defIndex = 4769; break; // CT Master Agents
    case 1517267165u: defIndex = 4770; break; // T Master Agents
    case 3736209238u: defIndex = 4768; break; // Superior Agents
    case 1969402445u: defIndex = 4767; break; // Exceptional Agents
    case 2249793569u: defIndex = 4766; break; // Distinguished Agents
    default:
        // Compatibility for tools/builds that send a row index or item def.
        if (redeemId < m_operationShopRewards.size())
        {
            return &m_operationShopRewards[redeemId];
        }
        defIndex = redeemId;
        break;
    }

    for (const ShopReward &reward : m_operationShopRewards)
    {
        if (reward.defIndex == defIndex)
        {
            return &reward;
        }
    }

    return nullptr;
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
