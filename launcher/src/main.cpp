// CS:GO Revival launcher
//
// 1. Loads launcher.cfg
// 2. Fetches the player's inventory from the revival server
// 3. Writes it to <csgo_dir>/csgo_gc/inventory.txt (what csgo_gc reads)
// 4. Launches CS:GO Legacy
//
// The inventory sync happens BEFORE launch so whatever the admin has granted
// (cases, keys, items) is present the moment the game starts.

#include "config.hpp"
#include "http_client.hpp"

#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#if defined(_WIN32)
#  include <windows.h>
#else
#  include <sys/wait.h>
#  include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace
{
    bool WriteFile(const fs::path &path, const std::string &contents, std::string &error)
    {
        std::error_code ec;
        fs::create_directories(path.parent_path(), ec);
        if (ec)
        {
            error = "could not create directory " + path.parent_path().string() + ": " + ec.message();
            return false;
        }

        std::ofstream out(path, std::ios::binary | std::ios::trunc);
        if (!out.is_open())
        {
            error = "could not open for writing: " + path.string();
            return false;
        }
        out.write(contents.data(), static_cast<std::streamsize>(contents.size()));
        return out.good();
    }

    std::vector<std::string> SplitArgs(const std::string &args)
    {
        std::vector<std::string> tokens;
        std::istringstream iss(args);
        std::string token;
        while (iss >> token)
            tokens.push_back(token);
        return tokens;
    }

    bool LaunchGame(const LauncherConfig &config, std::string &error)
    {
        fs::path exePath = fs::path(config.csgoDir) / config.gameExe;

#if defined(_WIN32)
        std::string commandLine = "\"" + exePath.string() + "\" " + config.gameArgs;

        STARTUPINFOA si;
        PROCESS_INFORMATION pi;
        std::memset(&si, 0, sizeof(si));
        si.cb = sizeof(si);
        std::memset(&pi, 0, sizeof(pi));

        std::vector<char> mutableCmd(commandLine.begin(), commandLine.end());
        mutableCmd.push_back('\0');

        BOOL created = CreateProcessA(
            nullptr, mutableCmd.data(), nullptr, nullptr, FALSE,
            0, nullptr, config.csgoDir.c_str(), &si, &pi);

        if (!created)
        {
            error = "CreateProcess failed (error " + std::to_string(GetLastError()) + ")";
            return false;
        }
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return true;
#else
        pid_t pid = fork();
        if (pid < 0)
        {
            error = "fork failed";
            return false;
        }

        if (pid == 0)
        {
            // child: run from the game directory so csgo_gc finds its relative paths
            if (chdir(config.csgoDir.c_str()) != 0)
                _exit(127);

            std::vector<std::string> argStrings;
            argStrings.push_back(exePath.string());
            for (const std::string &a : SplitArgs(config.gameArgs))
                argStrings.push_back(a);

            std::vector<char *> argv;
            for (std::string &s : argStrings)
                argv.push_back(s.data());
            argv.push_back(nullptr);

            execv(exePath.c_str(), argv.data());
            _exit(127); // exec failed
        }

        // parent returns immediately
        return true;
#endif
    }
}

int main(int argc, char **argv)
{
    std::string configPath = "launcher.cfg";
    if (argc > 1)
        configPath = argv[1];

    LauncherConfig config;
    std::string error;
    if (!LauncherConfig::Load(configPath, config, error))
    {
        std::fprintf(stderr, "[launcher] config error: %s\n", error.c_str());
        std::fprintf(stderr, "[launcher] usage: %s [path/to/launcher.cfg]\n", argv[0]);
        return 1;
    }
    config.ApplyDefaults();

    std::string invUrl = config.serverUrl;
    if (!invUrl.empty() && invUrl.back() == '/')
        invUrl.pop_back();
    invUrl += "/inventory/" + config.steamId;

    std::printf("[launcher] syncing inventory for %s\n", config.steamId.c_str());
    std::printf("[launcher] server: %s\n", invUrl.c_str());

    http::Response resp = http::Get(invUrl);
    if (!resp.ok)
    {
        std::fprintf(stderr, "[launcher] failed to fetch inventory: %s\n", resp.error.c_str());
        std::fprintf(stderr, "[launcher] aborting so we don't launch with a stale inventory.\n");
        return 2;
    }

    fs::path invPath = fs::path(config.csgoDir) / "csgo_gc" / "inventory.txt";
    if (!WriteFile(invPath, resp.body, error))
    {
        std::fprintf(stderr, "[launcher] failed to write inventory: %s\n", error.c_str());
        return 3;
    }
    std::printf("[launcher] wrote %zu bytes -> %s\n", resp.body.size(), invPath.string().c_str());

    if (!config.launchGame)
    {
        std::printf("[launcher] launch_game=0, inventory synced, not launching.\n");
        return 0;
    }

    std::printf("[launcher] launching CS:GO Legacy...\n");
    if (!LaunchGame(config, error))
    {
        std::fprintf(stderr, "[launcher] failed to launch game: %s\n", error.c_str());
        return 4;
    }

    std::printf("[launcher] launched. Have fun!\n");
    return 0;
}
