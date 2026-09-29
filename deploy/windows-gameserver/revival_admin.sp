#include <sourcemod>
#include <sdktools>
#include <cstrike>

public Plugin myinfo = {
    name = "CS:GO Revival Admin",
    author = "KINKERM",
    description = "Revival server admin moderation",
    version = "1.0.0"
};

bool g_BanGun[MAXPLAYERS + 1];

bool IsRevivalAdmin(int client)
{
    return client >= 1 && client <= MaxClients && IsClientInGame(client)
        && CheckCommandAccess(client, "revival_admin", ADMFLAG_ROOT, true);
}

public void OnPluginStart()
{
    HookEvent("player_hurt", Event_PlayerHurt, EventHookMode_Post);
    RegAdminCmd("sm_bangun", Command_BanGun, ADMFLAG_ROOT, "Arm the Revival VAC-ban Glock.");
    RegAdminCmd("sm_bangoff", Command_BanGunOff, ADMFLAG_ROOT, "Disarm the Revival VAC-ban Glock.");
    RegAdminCmd("sm_revival_rcon", Command_Rcon, ADMFLAG_ROOT, "Run any server console command.");
    RegAdminCmd("sm_revival_unban", Command_Unban, ADMFLAG_ROOT, "Remove a SteamID from the server ban list.");
}

public void OnClientPostAdminCheck(int client)
{
    if (IsRevivalAdmin(client))
        CS_SetClientClanTag(client, "[ADMIN]");
}

public void OnClientDisconnect(int client)
{
    if (client >= 1 && client <= MaxClients)
        g_BanGun[client] = false;
}

public Action Command_BanGun(int client, int args)
{
    if (!IsRevivalAdmin(client))
        return Plugin_Handled;
    g_BanGun[client] = true;
    GivePlayerItem(client, "weapon_glock");
    PrintToChat(client, "\x04[ADMIN]\x01 VAC ban gun armed. Hit a player with the Glock to ban them.");
    return Plugin_Handled;
}

public Action Command_BanGunOff(int client, int args)
{
    if (!IsRevivalAdmin(client))
        return Plugin_Handled;
    g_BanGun[client] = false;
    PrintToChat(client, "\x04[ADMIN]\x01 VAC ban gun disabled.");
    return Plugin_Handled;
}

public Action Command_Rcon(int client, int args)
{
    if (!IsRevivalAdmin(client))
        return Plugin_Handled;
    if (args < 1)
    {
        ReplyToCommand(client, "Usage: sm_revival_rcon <server command>");
        return Plugin_Handled;
    }
    char command[1024];
    GetCmdArgString(command, sizeof(command));
    ServerCommand("%s", command);
    ServerExecute();
    ReplyToCommand(client, "[REVIVAL ADMIN] executed: %s", command);
    return Plugin_Handled;
}

public Action Command_Unban(int client, int args)
{
    if (!IsRevivalAdmin(client))
        return Plugin_Handled;
    if (args != 1)
    {
        ReplyToCommand(client, "Usage: sm_revival_unban <SteamID>");
        return Plugin_Handled;
    }
    char auth[64];
    GetCmdArg(1, auth, sizeof(auth));
    if (!RemoveBan(auth, BANFLAG_AUTHID, "revival_unban", client))
    {
        ReplyToCommand(client, "Could not remove ban for %s", auth);
        return Plugin_Handled;
    }
    ReplyToCommand(client, "Unbanned %s", auth);
    return Plugin_Handled;
}

public Action OnClientSayCommand(int client, const char[] command, const char[] sArgs)
{
    if (!IsRevivalAdmin(client) || sArgs[0] == '\0')
        return Plugin_Continue;

    char text[256];
    strcopy(text, sizeof(text), sArgs);
    TrimString(text);

    if (StrEqual(text, "!bangun", false) || StrEqual(text, "!ban_gun", false))
        return Command_BanGun(client, 0);

    if (StrContains(text, "!rcon ", false) == 0)
    {
        char serverCommand[1024];
        strcopy(serverCommand, sizeof(serverCommand), text[6]);
        TrimString(serverCommand);
        if (serverCommand[0] != '\0')
        {
            ServerCommand("%s", serverCommand);
            ServerExecute();
            PrintToChat(client, "\x04[ADMIN]\x01 executed: %s", serverCommand);
        }
        return Plugin_Handled;
    }

    char tagColor[8];
    switch ((GetTime() / 1) % 6)
    {
        case 0: strcopy(tagColor, sizeof(tagColor), "\x07");
        case 1: strcopy(tagColor, sizeof(tagColor), "\x08");
        case 2: strcopy(tagColor, sizeof(tagColor), "\x09");
        case 3: strcopy(tagColor, sizeof(tagColor), "\x04");
        case 4: strcopy(tagColor, sizeof(tagColor), "\x0B");
        default: strcopy(tagColor, sizeof(tagColor), "\x0C");
    }

    if (StrEqual(command, "say_team", false))
    {
        int team = GetClientTeam(client);
        for (int target = 1; target <= MaxClients; target++)
        {
            if (IsClientInGame(target) && GetClientTeam(target) == team)
                PrintToChat(target, "%s[ADMIN]\x01 %N: %s", tagColor, client, sArgs);
        }
    }
    else
    {
        PrintToChatAll("%s[ADMIN]\x01 %N: %s", tagColor, client, sArgs);
    }
    return Plugin_Handled;
}

public void Event_PlayerHurt(Event event, const char[] name, bool dontBroadcast)
{
    int victim = GetClientOfUserId(event.GetInt("userid"));
    int attacker = GetClientOfUserId(event.GetInt("attacker"));
    if (victim <= 0 || attacker <= 0 || victim == attacker)
        return;
    if (!g_BanGun[attacker] || !IsRevivalAdmin(attacker))
        return;

    char weapon[64];
    event.GetString("weapon", weapon, sizeof(weapon));
    if (!StrEqual(weapon, "glock", false) && !StrEqual(weapon, "weapon_glock", false))
        return;

    g_BanGun[attacker] = false;
    char targetAuth[64];
    if (!GetClientAuthId(victim, AuthId_SteamID64, targetAuth, sizeof(targetAuth), true))
        strcopy(targetAuth, sizeof(targetAuth), "unknown");

    BanClient(victim, 0, BANFLAG_AUTHID, "VAC banned from secure server",
        "VAC banned from secure server", "revival_vac_ban", attacker);
    LogMessage("REVIVAL_SERVER_VACBAN_V1 admin=%N target=%N target_steamid64=%s",
        attacker, victim, targetAuth);
    PrintToChatAll("\x08[ADMIN]\x01 %N VAC banned %N from the secure server.", attacker, victim);
}
