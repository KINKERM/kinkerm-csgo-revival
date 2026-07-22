# Dedicated Gameserver (Competitive + Wingman) on Oracle Always Free

This sets up a persistent CS:GO Legacy dedicated server running under `csgo_gc`, so
friends can join a real server (competitive 5v5 and wingman 2v2) instead of only Steam
P2P lobbies. Skins/cases still work because each connecting player runs `csgo_gc`.

> **Straight talk on difficulty.** Two things make this harder than the inventory server:
> 1. **Getting the *legacy* server binary.** Valve's anonymous `steamcmd +app_update 740`
>    downloads the **current** dedicated server, which will **not** match the
>    `csgo_legacy` client. There is no clean anonymous route to the legacy server, so this
>    one step is manual (below).
> 2. **`csgo_gc` dedicated-server support is young** (its own README calls the project
>    incomplete). Expect to iterate. Everything here is scaffolding I can't test end-to-end
>    for you, so treat the first boot as a shakedown.

---

## 1. Pick the VM

Two Always-Free options:

| Option | Native? | RAM | Notes |
|--------|---------|-----|-------|
| **VM.Standard.E2.1.Micro** (x86 AMD) | ✅ yes | 1 GB | Simplest — srcds is x86. Tight RAM; the setup script adds 4 GB swap. Good for a small friends server. |
| **VM.Standard.A1.Flex** (ARM) | ❌ no | up to 24 GB | More headroom, but x86 srcds needs **box64** emulation (extra setup + overhead). Only worth it for bigger player counts. |

**Recommended: the x86 Micro** — native, no emulation. Use the ARM+box64 route only if you
outgrow it.

Create it like the inventory VM in [HOSTING.md](HOSTING.md), Ubuntu 22.04/24.04.

## 2. Run the setup script

```bash
git clone https://github.com/KINKERM/kinkerm-csgo-revival.git
cd kinkerm-csgo-revival
bash deploy/gameserver-setup.sh
```

This installs the 32-bit runtime, SteamCMD, a swapfile (if low RAM), and creates
`~/csgo-ds`. It then prints the manual step:

## 3. Get the LEGACY server files (the manual step)

You need the pre-CS2 CS:GO server build in `~/csgo-ds`. Options, easiest first:

- **Copy from a machine that already has the legacy client.** The cleanest source is a PC
  where you enrolled CS2 in the `csgo_legacy` beta. A dedicated server can run from a copy
  of those game files plus the server binaries.
- **Depot download via the Steam console** (needs a Steam account that owns CS:GO):
  open `steam://open/console`, then use `download_depot 740 <depotid> <manifestid>` for the
  legacy server depot, and `download_depot 730 731 <legacy manifest>` for the game content.
  Manifest IDs change over time — check SteamDB for the `csgo_legacy` build. Copy the
  resulting files up to `~/csgo-ds` (e.g. with `scp`).

You know it worked when `~/csgo-ds/srcds` (or `srcds_linux`) and `~/csgo-ds/csgo/` exist.

## 4. Overlay `csgo_gc` + our content

Use the **same pack** `build_pack.py` produces (it already contains the `srcds` launcher,
the csgo_gc library, `config.txt`, `items_game.txt` and the panorama UI). Extract it over
`~/csgo-ds` so the server runs through csgo_gc:

```bash
cd ~/csgo-ds
unzip -o /path/to/csgo-revival-pack.zip
```

Then drop in the configs and mode scripts:

```bash
cp ~/kinkerm-csgo-revival/deploy/gameserver/server.cfg     ~/csgo-ds/csgo/cfg/
cp ~/kinkerm-csgo-revival/deploy/gameserver/start-*.sh     ~/csgo-ds/
chmod +x ~/csgo-ds/start-*.sh
# edit rcon_password in csgo/cfg/server.cfg
```

## 5. Open the ports

CS:GO uses **UDP 27015** (game) — open it in **both** places:

```bash
sudo iptables -I INPUT 6 -p udp --dport 27015 -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 27015 -j ACCEPT   # rcon / server browser
sudo netfilter-persistent save
```
Plus an OCI **Security List** ingress rule: TCP+UDP `27015` from `0.0.0.0/0`.

## 6. Launch

```bash
bash ~/csgo-ds/start-competitive.sh    # 5v5 competitive, de_dust2
# or
bash ~/csgo-ds/start-wingman.sh        # 2v2 wingman, de_lake
```

Mode is set by `+game_type`/`+game_mode`:
- **Competitive:** `game_type 0`, `game_mode 1`
- **Wingman:** `game_type 0`, `game_mode 2`

## 7. Run it 24/7 (optional)

```bash
sudo cp deploy/csgo-gameserver.service /etc/systemd/system/
# edit WorkingDirectory + ExecStart (competitive vs wingman) inside it
sudo systemctl daemon-reload
sudo systemctl enable --now csgo-gameserver
journalctl -u csgo-gameserver -f
```

## 8. Friends connect

- In-game console: `connect <VM_PUBLIC_IP>:27015`
- Or the **server browser** (csgo_gc shows csgo_gc servers). If `sv_password` is set,
  they'll be prompted for it.

Everyone must be on the same `csgo_legacy` client + the same `csgo_gc` pack (they already
are if they used `install.py`).

---

## Troubleshooting first boot
- **Server exits immediately / "engine error"**: usually a legacy vs current version
  mismatch in the game files (step 3) — the server build must match the client.
- **Players can't connect but server is up**: port 27015 UDP not open (OCI Security List
  *and* iptables), or `sv_lan 1` — set `sv_lan 0`.
- **OOM / killed on the Micro**: confirm the 4 GB swap is active (`free -m`); consider the
  A1 shape.
- **Skins/cases not showing on the server**: the csgo_gc library didn't load — confirm you
  launched `./srcds` (the csgo_gc-wrapped one from the pack), not a stock server binary.
