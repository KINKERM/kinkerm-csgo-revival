CS:GO REVIVAL - WINDOWS ONE-MATCH SERVER
========================================

PURPOSE
-------
This folder turns one low-end Windows laptop into the single dedicated match
server for the revival.

Target:
- one 5v5 ranked Competitive match at a time
- one shared queue; the server chooses a map from the large installed map pool
- 64 tick
- no router port forwarding
- playit.gg UDP tunnel for public game traffic
- native legacy GC matchmaking messages:
    client 9101 -> shared queue
    ServerGC 9105 -> srcds reservation
    srcds 9106 -> accepted reservation id
    ClientGC 9107 -> MATCH FOUND / ACCEPT / join
- native end-match GC path for XP, profile levels, ranks, Operation missions and
  item-drop notifications

WHY 64 TICK
-----------
Use 64 tick on the 4 GB Celeron laptop. 128 tick roughly doubles the simulation
frequency and gives a weak CPU far less frametime headroom. Stable 64 tick is
better than an unstable 128-tick server.

There is no honest way to guarantee perfect server frametime before testing the
specific Celeron and its thermals. This setup minimizes the load: exactly one
srcds process, no bots, ABOVE_NORMAL process priority, no extra match instances.

WHAT MUST ALREADY EXIST
-----------------------
1. Python 3.10 or newer on the laptop. In Command Prompt:
       py -3 --version
   or:
       python --version

   If it reports Python 3.9 or older, install a current Python first.

2. A CS:GO LEGACY installation that contains:
       <csgo_dir>\srcds.exe
       <csgo_dir>\csgo\

   IMPORTANT: the server/client build must match the legacy CS:GO build your
   revival clients use. A current CS2 dedicated server is not compatible.

3. The CURRENT revival csgo_gc build installed in that same server folder.
   After rebuilding operation-revival-finish, copy these freshly built files:
       build\launcher\Release\srcds.exe
           -> <laptop csgo_dir>\srcds.exe
       build\csgo_gc\Release\csgo_gc.dll
           -> <laptop csgo_dir>\csgo_gc\csgo_gc.dll

   Also keep the revival GC data beside the DLL, especially config.txt and the
   schema files your normal revival pack already installs.

   The patched source overlay now includes gc_shared.cpp/h. When rebuilding,
   copy EVERY file from csgo_gc-patch\ over the matching file in upstream
   csgo_gc\ before compiling. Do not use an older DLL.

4. The central revival HTTP backend must be reachable by BOTH:
   - every player's launcher
   - this laptop agent

   It is server\revival_server.py from this repository. backend_url below is
   the same public base URL clients use as server_url.

   playit only replaces port forwarding for GAME traffic. It does not magically
   host the central HTTP backend.

REBUILD THE REVIVAL GC
----------------------
On the PC where you already build csgo_gc:

1. Pull operation-revival-finish.
2. Copy all files from:
       csgo_gc-patch\*
   over:
       <your csgo_gc source>\csgo_gc\*
   replacing matching files.

   New matchmaking files that MUST be copied include:
       gc_shared.cpp
       gc_shared.h
       gc_client.cpp
       gc_client.h
       gc_server.cpp
       gc_server.h

3. Build 32-bit Release using the same working build tree you already use:
       cmake --build build --config Release --target csgo srcds csgo_gc

4. Install the new client files on every player machine:
       build\launcher\Release\csgo.exe
       build\csgo_gc\Release\csgo_gc.dll
   plus the current Panorama/revival pack.

5. Install the new SERVER files on the laptop:
       build\launcher\Release\srcds.exe
       build\csgo_gc\Release\csgo_gc.dll

   Do not copy an old DLL from a previous Operation test.

PLAYIT.GG - NO PORT FORWARDING
------------------------------
1. Install/sign in to playit.gg on the laptop.
2. Create ONE UDP game tunnel.
3. Set the local target to:
       127.0.0.1:27015
4. Save the tunnel.
5. Copy the public hostname and public port playit gives you.

Example only:
       hostname: example.gl.joinmc.link
       public port: 30123

The public port does NOT have to be 27015. Put exactly what playit shows in
server_agent.json.

AGENT CONFIG
------------
Copy:
    server_agent.example.json
to:
    server_agent.json

Edit server_agent.json.

Example:
{
  "backend_url": "https://revival.example.com",
  "agent_id": "revival-laptop-1",
  "csgo_dir": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Counter-Strike Global Offensive",
  "public_host": "example.gl.joinmc.link",
  "public_port": 30123,
  "local_port": 27015,
  "playit_exe": "C:\\playit_gg\\playit.exe",
  "extra_srcds_args": "",
  "accept_timeout_seconds": 90,
  "post_match_grace_seconds": 25
}

backend_url:
    Same public revival backend base URL used by client launcher.cfg.

csgo_dir:
    Folder containing the PATCHED srcds.exe and csgo folder.

public_host/public_port:
    The PLAYIT public UDP endpoint, not 127.0.0.1 and not your LAN address.

local_port:
    Keep 27015 unless you deliberately change the playit local target too.

playit_exe:
    Optional. Leave "" if you prefer to start playit yourself.

accept_timeout_seconds:
    How long the match waits for all ten players after the real srcds
    reservation has been accepted. Default 90.

post_match_grace_seconds:
    Keep 25. This leaves srcds alive after Game_Over so final GC packets,
    scoreboard, XP, ranks, Operation progress and item-drop reveal finish before
    the process is stopped.

FIRST BOOT
----------
1. Start the central revival backend first.
2. Make sure playit says the UDP tunnel is online.
3. Run:
       start-agent.bat

At idle you should see:
       [agent] installed matchmaking maps: ...
       [agent] public tunnel: <host>:<port>

The agent DOES NOT keep srcds running while nobody has a match. That saves RAM
and CPU.

WHAT HAPPENS WHEN TEN PLAYERS QUEUE
-----------------------------------
1. All players press GO in the single Competitive queue.
2. The central coordinator collects 10 accounts.
3. It chooses one map that is both:
       - in the revival's large map pool
       - physically installed on the laptop
4. The laptop receives the assignment.
5. agent.py writes:
       <csgo_dir>\csgo_gc\server_reservation.txt
6. The patched srcds starts at 64 tick.
7. The patched ServerGC sends native message 9105 to srcds.
8. srcds answers with native 9106.
9. ServerGC writes:
       <csgo_dir>\csgo_gc\server_reservation_response.txt
   containing the REAL reservation id generated/accepted by srcds.
10. The laptop heartbeats that exact reservation id to the coordinator.
11. Each client receives native 9107 with:
       reservation id
       map
       playit hostname
       numeric UDP IP
       public UDP port
       account list
12. The normal CS:GO MATCH FOUND / ACCEPT flow should appear.
13. Accepted players enter the paused warmup.
14. agent.py reads the Source log Steam IDs.
15. When all 10 assigned accounts have entered:
       mp_warmup_pausetimer 0
       mp_warmup_end
    and the Competitive match begins.

IMPORTANT CONSOLE LINES
-----------------------
On the LAPTOP srcds console, a healthy reservation should include:
    matchmaking server: sent native 9105 match=... accounts=10 ...
    matchmaking server: native 9106 accepted match=... reservation=... map=...

In the LAPTOP agent console:
    [agent] native 9106 accepted match ... reservation=...
    [agent] accepted player entered: ... (1/10)
    ...
    [agent] accepted player entered: ... (10/10)
    [agent] all 10 players entered; match ... started

On a PLAYER console:
    matchmaking: queued Competitive search through revival bridge ...
    matchmaking: MATCH FOUND reservation=... map=... server=...
    matchmaking: 9164 returning active reserve ... server=... map=...

The reservation number printed by the laptop 9106 line and the player MATCH
FOUND line MUST be identical.

FAILED ACCEPT
-------------
The server waits in paused warmup. If the full ten do not enter before
accept_timeout_seconds:
- the reservation is cancelled,
- srcds stops,
- players who actually entered are put back into the queue,
- missing players return to idle,
- the laptop returns to its one free server slot.

END OF MATCH / XP / RANKS / DROPS
---------------------------------
At Game_Over the laptop does NOT kill srcds immediately.

For post_match_grace_seconds (25 by default) it stays alive so the patched
ServerGC can forward the native 9136 match-end payload to each player's local
ClientGC. That path updates:
- profile XP
- 40-level profile progression
- weekly XP bonus/reduction state
- Competitive wins and persistent skill-group state
- Operation Riptide mission progress/stars
- normal end-of-match item-drop notifications (9137)
- weekly profile-rank reward state
- timed case-drop state

The revival reproduces the visible 2021 progression/drop behavior, but Valve's
exact hidden Competitive rating algorithm and exact hidden random-drop timing
were never public. Those two server-side formulas are necessarily revival
approximations; the client-visible rank/XP/drop messages and persistence use the
legacy GC paths.

PERFORMANCE ON 4 GB CELERON
---------------------------
Before hosting:
- reboot the laptop,
- keep the Windows pagefile ENABLED (System managed is fine),
- close browsers,
- close Discord,
- close OneDrive sync if it is busy,
- pause large Windows Update/download work,
- do not run another game on the laptop,
- keep it plugged in,
- use the Windows Best performance power mode if available,
- make sure the laptop is not thermal-throttling.

The agent raises srcds to ABOVE_NORMAL priority automatically.

While testing, use the srcds console command:
    stats

Watch CPU and server behavior during gunfights/smokes/10-player action. A 64-tick
server gets one simulation tick every ~15.625 ms. If the Celeron cannot sustain
that under load, no config can honestly turn it into a faster CPU; close more
background work and check cooling first.

MAP POOL
--------
There is one shared Competitive queue. The laptop agent scans its top-level
csgo\maps folder and advertises every installed BSP whose name begins with:
    de_
    cs_

The coordinator chooses randomly from the maps the laptop actually reports.
That means preserved/removed maps can join the rotation without creating a
separate client-side map queue.

The known rotation order includes:
    de_dust2, de_mirage, de_inferno, de_nuke, de_overpass, de_vertigo,
    de_train, de_cache, de_cbble, de_ancient, de_anubis, de_tuscan,
    de_canals, de_breach, de_basalt, de_abbey, de_austria, de_biome,
    de_blackgold, de_chlorine, de_engage, de_grind, de_lite, de_mocha,
    de_mutiny, de_ruby, de_seaside, de_shipped, de_studio, de_subzero,
    de_swamp, de_thrill, de_zoo, cs_office, cs_agency, cs_italy,
    cs_insertion, cs_insertion2

Any other installed top-level de_/cs_ BSP is appended automatically. _se and
_ve variants are excluded. A missing BSP cannot be selected because it is never
advertised to the coordinator.

TROUBLESHOOTING
---------------
A) Agent says srcds.exe not found:
   csgo_dir is wrong or that install has client-only files. Point it at the
   matching legacy dedicated-server folder.

B) You see 9105 but NEVER 9106:
   The server is not accepting the GC reservation. Confirm the laptop is using
   the NEW srcds.exe AND NEW csgo_gc.dll from the current branch. Also confirm:
       csgo_gc\server_reservation.txt
   exists before srcds starts.

C) 9106 is healthy but players never get MATCH FOUND:
   On the player PC, confirm the current launcher.py is being used and the new
   client csgo_gc.dll was installed. Check:
       <client csgo_dir>\csgo_gc\mm_state.txt
   It should eventually say:
       state=reserved
       reservation_id=<same id as laptop 9106>

D) MATCH FOUND appears but ACCEPT does not connect:
   Check the player console for:
       matchmaking: 9164 returning active reserve ...
   Confirm playit is online and server_agent.json has the exact public host/port.

E) Players connect but warmup never ends:
   The agent did not identify all assigned Steam IDs in srcds logs. Send the
   agent/srcds lines containing "entered the game".

F) Everyone connects but server is choppy:
   Run "stats" in srcds. Close background processes, check thermals and keep
   64 tick. Do NOT switch this Celeron to 128 tick while debugging performance.

G) XP/drops/ranks do not appear:
   Do not close srcds manually at Game_Over. Keep post_match_grace_seconds=25.
   Send the 9136/9137 and "progression:" / "drops:" console lines.

FILES THE AGENT CREATES
-----------------------
    <csgo_dir>\csgo\cfg\revival_competitive.cfg
    <csgo_dir>\csgo_gc\server_reservation.txt
    <csgo_dir>\csgo_gc\server_reservation_response.txt

The two reservation files are runtime state and may be deleted while the agent
and srcds are stopped.
