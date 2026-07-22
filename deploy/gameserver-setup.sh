#!/usr/bin/env bash
# CS:GO Revival - dedicated gameserver setup for Oracle Cloud (Ubuntu).
#
# Automates everything that CAN be automated for a CS:GO Legacy dedicated server
# running under csgo_gc (competitive + wingman). The ONE step it can't do for you
# is fetching the *legacy* server files (Valve only serves those via a Steam
# account depot download, not anonymous SteamCMD) - see docs/GAMESERVER.md.
#
# Usage (on the VM):   bash deploy/gameserver-setup.sh
set -euo pipefail

SERVER_DIR="${SERVER_DIR:-$HOME/csgo-ds}"
STEAMCMD_DIR="${STEAMCMD_DIR:-$HOME/steamcmd}"

echo "[setup] server dir: $SERVER_DIR"

# --- 1. 32-bit runtime deps (CS:GO srcds is 32-bit x86) --------------------
echo "[setup] installing dependencies (needs sudo)..."
sudo dpkg --add-architecture i386 || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    lib32gcc-s1 lib32stdc++6 libc6-i386 libncurses6:i386 libtinfo6:i386 \
    ca-certificates wget tar screen

# --- 2. swap (the Always-Free x86 Micro has only ~1 GB RAM) -----------------
if [ ! -f /swapfile ] && [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2500 ]; then
    echo "[setup] low RAM detected -> creating 4G swapfile"
    sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# --- 3. SteamCMD ------------------------------------------------------------
mkdir -p "$STEAMCMD_DIR"
if [ ! -f "$STEAMCMD_DIR/steamcmd.sh" ]; then
    echo "[setup] installing SteamCMD..."
    wget -qO- https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
        | tar -xz -C "$STEAMCMD_DIR"
fi

mkdir -p "$SERVER_DIR"

cat <<EOF

============================================================================
[setup] Base system is ready.

NEXT (the manual step only you can do - see docs/GAMESERVER.md for detail):

  Get the LEGACY CS:GO dedicated server files into:
      $SERVER_DIR

  Valve's anonymous 'app_update 740' gives the CURRENT server, which will NOT
  match the csgo_legacy client. You must fetch the legacy build via a Steam
  account that owns CS:GO (download_depot in the Steam console, then copy the
  files up to this VM), OR copy them from a machine that has the legacy client.

AFTER the server files are in place:
  1) Overlay your csgo_gc pack files (srcds + csgo_gc.so/.dll + config.txt +
     items_game.txt + panorama) into $SERVER_DIR  (same pack build_pack.py makes).
  2) Copy the configs:
       cp deploy/gameserver/server.cfg      $SERVER_DIR/csgo/cfg/
       cp deploy/gameserver/start-*.sh      $SERVER_DIR/
  3) Open the game ports (Oracle Security List + local firewall):
       sudo iptables -I INPUT 6 -p udp --dport 27015 -j ACCEPT
       sudo iptables -I INPUT 6 -p tcp --dport 27015 -j ACCEPT
       sudo netfilter-persistent save
     (also add TCP+UDP 27015 ingress from 0.0.0.0/0 in the OCI Security List)
  4) Launch:  bash $SERVER_DIR/start-competitive.sh   (or start-wingman.sh)
============================================================================
EOF
