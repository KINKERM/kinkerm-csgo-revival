#!/usr/bin/env bash
# Launch the CS:GO Revival dedicated server in COMPETITIVE (5v5) mode.
# Run from the server root (the folder containing ./srcds and ./csgo).
set -euo pipefail
cd "$(dirname "$0")"

# game_type 0 + game_mode 1 = Competitive
exec ./srcds \
    -game csgo -console -usercon \
    -secure \
    +game_type 0 +game_mode 1 \
    +mapgroup mg_active \
    +map de_dust2 \
    +sv_setsteamaccount "" \
    -maxplayers_override 12 \
    -tickrate 128 \
    -port 27015 \
    +exec server.cfg
