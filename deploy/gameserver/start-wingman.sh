#!/usr/bin/env bash
# Launch the CS:GO Revival dedicated server in WINGMAN (2v2) mode.
# Run from the server root (the folder containing ./srcds and ./csgo).
set -euo pipefail
cd "$(dirname "$0")"

# game_type 0 + game_mode 2 = Wingman (2v2, one bombsite)
exec ./srcds \
    -game csgo -console -usercon \
    -secure \
    +game_type 0 +game_mode 2 \
    +mapgroup mg_de_lake \
    +map de_lake \
    +sv_setsteamaccount "" \
    -maxplayers_override 4 \
    -tickrate 128 \
    -port 27015 \
    +exec server.cfg
