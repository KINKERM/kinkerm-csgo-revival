# CS:GO Revival — Current Ranked Matchmaking Setup

This document is the source of truth for the current revival on the
`operation-revival-finish` branch.

The old Steam-P2P-only instructions are obsolete. The current revival has:

- one shared **ranked Competitive 5v5 queue**
- 10-player match allocation
- native-style MATCH FOUND / ACCEPT / reserve flow through the patched GC
- one lightweight dedicated match server, started only when a match is allocated
- 64-tick Competitive matches
- a large rotating map pool chosen by the coordinator, not by individual clients
- persistent profile XP/levels and Competitive rank/wins
- 2021-style weekly XP and end-match reward handling
- Operation Riptide mission/progression persistence
- central inventory/config persistence in `server/data`

## Components

### Main PC

Runs:

- `server/revival_server.py` on port 8787
- the player's `launcher/launcher.py`
- the patched client `csgo_gc.dll`

### Match-server laptop

Runs:

- Playit UDP tunnel
- `deploy/windows-gameserver/agent.py`
- patched `srcds.exe`
- patched server-side `csgo_gc.dll`

The laptop can remain idle between matches. The agent starts srcds only when the
coordinator allocates a full 10-player match.

## Existing installation: safe update

If you already have:

- a working `server/data` directory
- `launcher/launcher.cfg`
- an existing `csgo_gc_clean` build tree

do **not** start over and do not replace your data.

From PowerShell run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

& "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned\UPDATE_EXISTING_WINDOWS.ps1"
```

Defaults used by that script:

```text
revival repo:
%USERPROFILE%\Documents\kinkerm-csgo-revival-pinned

csgo_gc source/build:
%USERPROFILE%\Documents\csgo_gc_clean

CS:GO Legacy:
C:\Program Files (x86)\Steam\steamapps\common\csgo legacy
```

Override any path if needed:

```powershell
& "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned\UPDATE_EXISTING_WINDOWS.ps1" `
  -RevivalRepo "C:\path\to\kinkerm-csgo-revival-pinned" `
  -CsgoGcSource "C:\path\to\csgo_gc_clean" `
  -CsgoDir "C:\path\to\csgo legacy"
```

The updater:

1. backs up `server/data` and `launcher/launcher.cfg`
2. fetches the newest `operation-revival-finish`
3. restores the saved data/config
4. applies the complete `csgo_gc-patch` overlay
5. builds `csgo.exe`, `srcds.exe`, and `csgo_gc.dll`
6. builds the full client pack including Panorama
7. installs the pack into CS:GO Legacy
8. checks that the installed queue UI matches the repo

Backups are written under:

```text
%USERPROFILE%\Documents\CSGO_Revival_Backups\
```

## Persistent data

The backend uses:

```text
server/data/catalog.json
server/data/players.json
server/data/configs.json
server/data/server_config.json
```

Do not regenerate or replace these when updating an existing revival unless you
explicitly want to reset data.

`server_config.json` should point its three file paths at the current checkout's
`server/data` directory:

```json
{
  "players_file": "...\\server\\data\\players.json",
  "catalog_file": "...\\server\\data\\catalog.json",
  "configs_file": "...\\server\\data\\configs.json"
}
```

Keep the existing admin/sync tokens.

## Start the backend

```powershell
cd "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned\server"
py -3 revival_server.py --host 0.0.0.0 --port 8787
```

Or use the setup package's backend script and pass the repo path.

The public HTTPS/Funnel URL points to this backend. Playit is **not** used for
backend HTTP traffic.

## launcher.cfg

Each player has a `launcher/launcher.cfg`:

```ini
server_url=<PUBLIC BACKEND HTTPS URL>
steam_id=<PLAYER STEAMID64>
csgo_dir=<PLAYER CS:GO LEGACY ROOT>
game_args=-steam -game csgo -novid
launch_game=1
sync_token=<BACKEND SYNC TOKEN>
```

Existing working launcher configs can be reused.

Launch through:

```powershell
cd "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned\launcher"
py -3 launcher.py
```

The launcher synchronizes inventory/profile state and keeps the HTTP-to-GC
matchmaking bridge alive while the game runs.

## Client Play screen

The revival Panorama overrides intentionally provide:

- Competitive only
- one ranked 5v5 queue
- no per-map checkboxes
- no Wingman/Casual/Deathmatch queue tabs
- no Short Match/private/ranked-option strip
- one visible Revival Competitive queue card

The backend, not Panorama, chooses the map.

The full client pack must contain at least:

```text
csgo_gc.dll
csgo_revival.exe
srcds.exe
csgo_gc/config.txt
csgo/scripts/items/items_game.txt
csgo/panorama/layout/mainmenu_play.xml
csgo/panorama/scripts/mainmenu_play.js
```

## Match-server laptop

Copy/update:

```text
deploy/windows-gameserver/agent.py
deploy/windows-gameserver/start-agent.bat
deploy/windows-gameserver/server_agent.json
```

The laptop CS:GO root also needs the newly built:

```text
srcds.exe
csgo_gc.dll
csgo_gc/config.txt
csgo/scripts/items/items_game.txt
```

### Playit

Create one UDP tunnel:

```text
local target: 127.0.0.1:27015
```

Put Playit's public host and public port in `server_agent.json`.

Example shape:

```json
{
  "backend_url": "https://your-backend.example",
  "agent_id": "revival-laptop-1",
  "csgo_dir": "C:\\path\\to\\CSGO",
  "public_host": "example.ply.gg",
  "public_port": 30123,
  "local_port": 27015,
  "playit_exe": "",
  "extra_srcds_args": "",
  "accept_timeout_seconds": 90,
  "post_match_grace_seconds": 25
}
```

Keep `post_match_grace_seconds` at 25 so final GC progression/reward traffic can
finish before srcds shuts down.

Start Playit, then run:

```text
start-agent.bat
```

The agent prints the exact installed matchmaking maps it advertises.

## Map pool

The agent now advertises every installed top-level map whose BSP name begins with:

```text
de_
cs_
```

Known rotation entries include Dust II, Mirage, Inferno, Nuke, Overpass,
Vertigo, Train, Cache, Cobblestone, Ancient, Anubis, Tuscan, Canals, Breach,
Basalt, Abbey, Austria, Biome, Black Gold, Chlorine, Engage, Grind, Lite,
Mocha, Mutiny, Ruby, Seaside, Shipped, Studio, Subzero, Swamp, Thrill, Zoo,
Office, Agency, Italy, Insertion, and Insertion II.

The coordinator chooses from maps actually reported by the laptop, so missing BSPs
are not selected.

## Match flow

```text
player presses GO
    ↓
patched client GC sends matchmaking start
    ↓
launcher forwards queue request to backend
    ↓
backend waits for 10 players
    ↓
backend chooses an installed map
    ↓
laptop agent receives assignment
    ↓
srcds starts at 64 tick
    ↓
native reservation / GC reserve flow
    ↓
MATCH FOUND
    ↓
all players ACCEPT
    ↓
players connect
    ↓
Competitive match starts
```

The laptop does not need to be running for the Play screen to render correctly,
but it must be online for a full queue to receive a server allocation.

## Profile XP, levels and Competitive rank

The patched GC persists a `revival_profile` block per player.

It stores:

- profile level
- current profile XP
- Competitive skill-group id
- Competitive wins
- weekly XP state
- weekly level reward state
- timed case-drop state
- internal Competitive rating/match count

Profile ranks use the legacy 1–40 range and 5,000 XP per level.

2021 weekly XP behavior is implemented with bonus XP followed by reduced XP later
in the week.

New profiles default to level 1 / 0 XP / unranked instead of the old static
level-40 / Global-Elite config.

Competitive players remain unranked until the placement threshold and then expose
the real legacy rank ids. Valve's exact hidden skill-rating formula was never
public, so the revival keeps a persistent internal rating while using the real
visible CS:GO skill-group ids.

## End-match rewards

Match-end processing uses the game's native `9136` / `9137` path.

The final behavior is the 2021-style system:

- XP is processed from the real match-end packet
- profile rank-ups can trigger the weekly level reward
- case drops are playtime/weekly-state driven, not forced every match
- reward state persists across launches
- duplicate final match packets for the same reservation are ignored

## Operation Riptide

Operation progress is carried through the same match-end path and persisted in the
inventory/profile sync.

The existing Riptide pass/coin/stars/shop/mission state is preserved through
updates. Do not replace `server/data` when updating the matchmaking build.

## Validation

After updating, verify:

1. backend starts with the migrated non-empty catalog
2. launcher starts without regenerating `launcher.cfg`
3. Play shows only the Revival Competitive queue card
4. laptop agent prints the expected large installed map list
5. queue reaches MATCH FOUND when 10 players are present
6. reservation id reported by server/client matches
7. after a completed match, profile XP/rank state is still present after restart

The CI workflow also compiles the Win32 GC and builds a complete
`csgo-revival-pack.zip`, validating that the required Panorama/runtime files are
inside it.
