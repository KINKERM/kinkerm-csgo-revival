#include "config.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>

namespace
{
    std::string Trim(const std::string &s)
    {
        size_t begin = 0;
        size_t end = s.size();
        while (begin < end && std::isspace(static_cast<unsigned char>(s[begin]))) ++begin;
        while (end > begin && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;
        return s.substr(begin, end - begin);
    }

    bool ParseBool(const std::string &value, bool fallback)
    {
        if (value.empty()) return fallback;
        std::string v = value;
        std::transform(v.begin(), v.end(), v.begin(), [](unsigned char c) { return std::tolower(c); });
        if (v == "1" || v == "true" || v == "yes" || v == "on") return true;
        if (v == "0" || v == "false" || v == "no" || v == "off") return false;
        return fallback;
    }
}

bool LauncherConfig::Load(const std::string &path, LauncherConfig &out, std::string &error)
{
    std::ifstream file(path);
    if (!file.is_open())
    {
        error = "could not open config file: " + path;
        return false;
    }

    std::string line;
    while (std::getline(file, line))
    {
        std::string trimmed = Trim(line);
        if (trimmed.empty() || trimmed[0] == '#' || trimmed[0] == ';')
            continue;

        size_t eq = trimmed.find('=');
        if (eq == std::string::npos)
            continue;

        std::string key = Trim(trimmed.substr(0, eq));
        std::string value = Trim(trimmed.substr(eq + 1));

        if (key == "server_url")       out.serverUrl = value;
        else if (key == "steam_id")    out.steamId = value;
        else if (key == "csgo_dir")    out.csgoDir = value;
        else if (key == "game_exe")    out.gameExe = value;
        else if (key == "game_args")   out.gameArgs = value;
        else if (key == "launch_game") out.launchGame = ParseBool(value, true);
    }

    if (out.serverUrl.empty()) { error = "server_url is required"; return false; }
    if (out.steamId.empty())   { error = "steam_id is required"; return false; }
    if (out.csgoDir.empty())   { error = "csgo_dir is required"; return false; }

    return true;
}

void LauncherConfig::ApplyDefaults()
{
    if (gameExe.empty())
    {
#if defined(_WIN32)
        gameExe = "csgo.exe";
#elif defined(__APPLE__)
        gameExe = "csgo_osx64";
#else
        gameExe = "csgo_linux64";
#endif
    }

    if (gameArgs.empty())
        gameArgs = "-steam -game csgo -novid";
}
