CS:GO REVIVAL - WINDOWS MATCH SERVER
====================================

This folder is the one-slot Windows dedicated-server agent.

It is intentionally configured for:
- one 5v5 Competitive match at a time
- 64 tick
- no bots
- the large shared matchmaking map pool
- playit.gg (or another UDP tunnel) instead of router port forwarding

QUICK SETUP
-----------
1. Copy server_agent.example.json to server_agent.json.
2. Set backend_url to the same public revival backend URL used by the clients.
3. Set csgo_dir to the folder containing srcds.exe and the csgo folder.
4. Create a UDP tunnel in playit.gg:
      local address: 127.0.0.1
      local port:    27015
   Put the tunnel hostname and public port in public_host/public_port.
5. Optional: put the full path to playit.exe in playit_exe so this agent starts it.
6. Run start-agent.bat.

If srcds.exe is not present in your existing legacy CS:GO folder, install the
free "CS:GO Dedicated Server" files with SteamCMD (app 740) and point csgo_dir
at that folder. Copy/install the same revival server-side GC patch into that
server installation before matchmaking testing.

The agent automatically writes:
  csgo/cfg/revival_competitive.cfg

It starts exactly one process with:
  game_type 0
  game_mode 1
  -tickrate 64
  10 max players

The agent reports the tunnel address to the coordinator. When ten players queue,
it receives a random installed map, boots srcds, waits for startup, and then the
GC sends the native matchmaking reservation to all players.

PERFORMANCE
-----------
On a 4 GB Celeron Windows laptop, close browsers, Discord, launchers and Windows
Update work before hosting. 64 tick is deliberate: 128 tick roughly doubles the
server simulation frequency and is much more likely to produce server-frame
spikes on this CPU. The agent raises srcds to ABOVE_NORMAL priority, but exact
server FPS still depends on the specific Celeron, thermals and Windows load.


ACCEPT FLOW
-----------
When ten players are found, the server boots into a paused Competitive warmup.
The client receives the native 9107 match reservation and shows the normal
match-found/ACCEPT flow. The agent watches srcds logs for the ten assigned Steam
accounts entering the game. Once all ten have entered, it ends warmup and marks
the match in progress.

If the full group has not entered within accept_timeout_seconds (default 90),
the server is cancelled. Players who entered are returned to the queue and the
missing player(s) are returned to idle.

END OF MATCH
------------
Do not lower post_match_grace_seconds below about 20 seconds. The default 25
seconds intentionally leaves srcds alive after Game_Over so the game's native
match-end GC messages, XP/rank updates, Operation progress and item-drop reveal
can reach clients before the server process exits.
