#pragma once

#include <cstdint>

void SteamHookInstall(bool dedicated);

// Revival matchmaking needs the actual dedicated-server SteamID for 9107.
// Returns 0 until SteamGameServer has a valid identity.
std::uint64_t RevivalGameServerSteamId();
