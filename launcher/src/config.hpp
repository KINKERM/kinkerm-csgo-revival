#pragma once

#include <string>

// Launcher configuration, loaded from a simple key=value file (launcher.cfg).
struct LauncherConfig
{
    std::string serverUrl;   // e.g. http://127.0.0.1:8787
    std::string steamId;     // SteamID64 of the player
    std::string csgoDir;     // CS:GO Legacy install root (folder containing the game exe)
    std::string gameExe;     // executable name (platform default if empty)
    std::string gameArgs;    // launch arguments
    bool launchGame = true;  // if false, only sync the inventory and exit

    // Parse a config file. Returns false and fills `error` on failure.
    static bool Load(const std::string &path, LauncherConfig &out, std::string &error);

    // Fill in platform-appropriate defaults for any unset fields.
    void ApplyDefaults();
};
