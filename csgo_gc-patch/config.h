#pragma once

#include "gc_const_csgo.h"
#include "item_schema.h" // rarity constants

struct RarityWeight
{
    uint32_t rarity;
    float weight;
};

// operation shop (revival): star price of one shop reward item def
struct ShopReward
{
    uint32_t defIndex;
    int cost;
};

// Operation star-pack item def -> number of stars applied when the item is used.
struct OperationStarPack
{
    uint32_t defIndex;
    uint32_t stars;
};

// for Platform::Print calls
enum LogOutput
{
    LogOutputNone, // don't output anything
    LogOutputConsole, // game console
    LogOutputFile // game console and gc_log.txt
};

class GCConfig
{
public:
    GCConfig();

    // options used by platform layer (bruh)
    LogOutput GetLogOutput() const { return m_logOutput; }

    // options used by steam hook
    uint32_t AppIdOverride() const { return m_appIdOverride; }
    bool ShowCsgoGCServersOnly() const { return m_showCsgoGCServersOnly; }

    RankId CompetitiveRank() const { return m_competitiveRank; }
    int CompetitiveWins() const { return m_competitiveWins; }
    RankId WingmanRank() const { return m_wingmanRank; }
    int WingmanWins() const { return m_wingmanWins; }
    DangerZoneRankId DangerZoneRank() const { return m_dangerZoneRank; }
    int DangerZoneWins() const { return m_dangerZoneWins; }

    bool DestroyUsedItems() const { return m_destroyUsedItems; }

    // trade-up contracts (revival addition): opening this crate def index runs
    // the 5-Covert -> gold recipe instead of a normal case roll. 0 = disabled.
    uint32_t GoldTradeUpCrate() const { return m_goldTradeUpCrate; }

    // "gold only" case (revival addition): opening this crate def always rolls a
    // random gold (knife/glove) from any collection - no Coverts consumed, just
    // the case. Admin-grant it like any case. 0 = disabled.
    uint32_t GoldOnlyCrate() const { return m_goldOnlyCrate; }

    // operation shop (revival): buying a reward in the Operation Shop spends stars
    // from the player's Operation coin. OperationShopCost returns a reward item def's
    // star price (0 if it isn't a shop reward). Stars live on one of OperationCoinDefs
    // in the OperationStarAttribute ("upgrade level"). Keep the costs here in sync with
    // operation_util.js m_rewardSchema points.
    int OperationShopCost(uint32_t defIndex) const;
    uint32_t OperationStarPackValue(uint32_t defIndex) const;
    const std::vector<uint32_t> &OperationCoinDefs() const { return m_operationCoinDefs; }
    uint32_t OperationStarAttribute() const { return m_operationStarAttribute; }
    uint32_t OperationSeason() const { return m_operationSeason; }
    uint32_t OperationPassDef() const { return m_operationPassDef; }
    uint32_t OperationActivationCoinDef() const { return m_operationActivationCoinDef; }

    bool VacBanned() const { return m_vacBanned; }
    int CommendedFriendly() const { return m_commendedFriendly; }
    int CommendedTeaching() const { return m_commendedTeaching; }
    int CommendedLeader() const { return m_commendedLeader; }
    int Level() const { return m_level; }
    int Xp() const { return m_xp; }

    float GetRarityWeight(uint32_t rarity) const;

private:
    LogOutput m_logOutput{ LogOutputConsole };

    // actually default to 4465480 instead of 730, people are going to use old configs
    // and then wonder why the game doesn't work and open an issue on github otherwise
    uint32_t m_appIdOverride{ 4465480 };
    bool m_showCsgoGCServersOnly{ true };

    RankId m_competitiveRank{ RankNone };
    int m_competitiveWins{ 0 };
    RankId m_wingmanRank{ RankNone };
    int m_wingmanWins{ 0 };
    DangerZoneRankId m_dangerZoneRank{ DangerZoneRankNone };
    int m_dangerZoneWins{ 0 };

    bool m_destroyUsedItems{ true };

    // trade-up contracts (revival addition): crate def that triggers 5 Covert -> gold
    uint32_t m_goldTradeUpCrate{ 0 };

    // "gold only" case (revival addition): crate def that always rolls a gold
    uint32_t m_goldOnlyCrate{ 0 };

    // operation shop (revival): coin defs that hold stars, the star attribute, and
    // the per-reward star costs (parsed from config's operation_shop block)
    std::vector<uint32_t> m_operationCoinDefs{ 4759, 4760, 4761, 4762 };
    uint32_t m_operationStarAttribute{ 268 };
    uint32_t m_operationSeason{ 11 };
    uint32_t m_operationPassDef{ 4758 };
    uint32_t m_operationActivationCoinDef{ 4759 };
    std::vector<ShopReward> m_operationShopRewards;
    std::vector<OperationStarPack> m_operationStarPacks{
        { 4763, 1 },
        { 4764, 10 },
        { 4765, 100 },
    };

    bool m_vacBanned{ false };
    int m_commendedFriendly{ 0 };
    int m_commendedTeaching{ 0 };
    int m_commendedLeader{ 0 };
    int m_level{ 0 };
    int m_xp{ 0 };

    // default to valve weights
    std::vector<RarityWeight> m_rarityWeights{
        { ItemSchema::RarityCommon, 10000000 },
        { ItemSchema::RarityUncommon, 2000000 },
        { ItemSchema::RarityRare, 400000 },
        { ItemSchema::RarityMythical, 80000 },
        { ItemSchema::RarityLegendary, 16000 },
        { ItemSchema::RarityAncient, 3200 },
        { ItemSchema::RarityUnusual, 1280 },
    };
};

const GCConfig &GetConfig();
