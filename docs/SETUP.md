# CS:GO Revival — Setup Guide

This walks you from nothing to a working revival where **you** grant cases and players open them for
free in CS:GO Legacy.

There are three moving parts:

1. **`csgo_gc`** — the open-source local Game Coordinator that actually restores inventories and case
   opening. You install this into CS:GO. (Not part of this repo.)
2. **The revival server** (`server/`) — runs on your machine/VPS, stores who owns what, and lets you
   grant cases. This is what makes it *your* servers instead of each player editing local files.
3. **The launcher** (`launcher/`) — each player runs this instead of launching CS:GO directly. It pulls
   their inventory from your server and boots the game.

---

## Prerequisites

- **CS:GO Legacy.** In Steam, right-click Counter-Strike 2 → **Properties → Betas** → select
  **`csgo_legacy`**. This downloads the last CS:GO build alongside CS2.
- **Python 3.10+** on the machine that runs the server.
- **A C++17 compiler + CMake** to build the launcher (or hand players a prebuilt binary).

---

## Step 1 — Install `csgo_gc` into CS:GO

`csgo_gc` is what opens cases locally without Valve's backend. Every player (and any dedicated server)
needs it.

1. Grab the latest release for the platform from the
   [`csgo_gc` releases page](https://github.com/mikkokko/csgo_gc/releases).
2. Open the CS:GO Legacy install folder (the one containing `csgo.exe` / `csgo_linux64` and the `csgo`
   folder).
3. **Back up** the existing `csgo.exe` / `srcds.exe` / `csgo_linux64` etc. — they get overwritten.
4. Extract the release into that folder, replacing the executables.
5. Copy this repo's tuned config into place: `gc-config/config.txt` → `<csgo install>/csgo_gc/config.txt`.

> If you get a VAC message box on launch, add `-steam` to the launch arguments (the launcher already does).

---

## Step 2 — Build the case catalog from your CS:GO install

The catalog maps friendly case names → the `def_index` values `csgo_gc` understands, plus each case's
matching key. Generate it straight from your own `items_game.txt` so it's always accurate:

```bash
cd server
python3 build_catalog.py \
  --items-game "/path/to/Counter-Strike Global Offensive/csgo/scripts/items/items_game.txt" \
  --out data/catalog.json
```

You'll see something like `142 cases, 142 keys`. To try things out before doing this, you can instead
`cp data/catalog.sample.json data/catalog.json` (a tiny starter set).

---

## Step 3 — Run the revival server

```bash
cd server
python3 revival_server.py --host 0.0.0.0 --port 8787
```

On first run it prints (and saves to `data/server_config.json`) a random **admin token**. Keep it secret —
it's what authorizes granting items. Data lives in:

- `data/players.json` — every player's inventory
- `data/catalog.json` — the case/key catalog
- `data/server_config.json` — admin token + file paths

If you expose the server on the public internet, put it behind a reverse proxy (nginx/Caddy) for HTTPS.
The `/inventory/<steamid>` endpoint is public by design (it carries no secrets); only the `/admin/*`
endpoints require the token.

---

## Step 4 — Grant cases (you're the admin)

From anywhere that can reach the server, use the admin CLI. Point it at the server and give it the token
(via `--token`, the `CSGO_REVIVAL_TOKEN` env var, or automatically from `data/server_config.json` if run
on the server machine).

```bash
cd server

# see what cases exist
python3 admin.py catalog

# give a player 5 of a case (the matching key is bundled automatically)
python3 admin.py grant-case 76561198XXXXXXXXX weapon_case_1 --count 5

# give a specific item by def_index (e.g. a raw skin/tool)
python3 admin.py grant-item 76561198XXXXXXXXX --def-index 7 --rarity 3

# inspect / undo
python3 admin.py show   76561198XXXXXXXXX
python3 admin.py revoke 76561198XXXXXXXXX --def-index 4001 --count 1
python3 admin.py clear  76561198XXXXXXXXX
```

`76561198XXXXXXXXX` is the player's **SteamID64** (17 digits).

---

## Step 5 — Players launch with the launcher

Build the launcher once:

```bash
cd launcher
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Then each player copies `launcher.example.cfg` → `launcher.cfg`, sets their `server_url`, `steam_id`, and
`csgo_dir`, and runs `csgo-launcher`. It will:

1. fetch their inventory from your server,
2. write it to `<csgo_dir>/csgo_gc/inventory.txt`,
3. launch CS:GO Legacy.

Whatever you granted shows up the moment the game opens. Cases open with the normal CS:GO animation, for
free — the matching key was bundled with the case, and `csgo_gc` never charges for or validates it.

---

## How "free case opening" works

- Real CS:GO requires a paid key to open a case. `csgo_gc`'s `UnlockCrate` **does not validate keys**
  (see the comment in its `inventory.cpp`), and there's no payment backend.
- This repo's server **bundles the matching key with every case it grants**, so the client shows the
  normal "open" flow and plays the real animation — but nothing is ever purchased.
- With `destroy_used_items "1"` (the default in `gc-config/config.txt`), the case + key are consumed on
  open, exactly like real CS:GO.

**Alternative — infinite re-opening:** set `destroy_used_items "0"` in `csgo_gc/config.txt`, grant cases
with `--no-key`, and hand each player one permanent key. Then a single case can be opened over and over.

---

## Running a community dedicated server (optional)

`csgo_gc` supports dedicated servers too. Install it over your `srcds` the same way, then run the CS:GO
dedicated server as usual (SteamCMD + `srcds`). With `show_csgo_gc_servers_only "1"`, players' server
browsers will group around your community. Add SourceMod/MetaMod for admin tools and game modes
(retakes, surf, 1v1, etc.).

---

## Reminder on scope

Items exist only on your servers. They are **not** real Steam inventory assets, can't be traded on the
Steam Market, and have no monetary value. Keep it free and cosmetic — don't attach real money to case
opening, which would turn it into online gambling with a completely different legal footprint.


---

## One-click setup for friends + playing together (Steam P2P)

This is the easy path: your friends run **one script** and get an identical setup
(patched `csgo_gc`, our `config.txt`, and our custom `items_game.txt` with Kinkerm's
Case and everything we added), then you all play over **Steam P2P lobbies** — no port
forwarding, no dedicated server.

### Host (you) — do this once

1. **Build the pack.** After you've compiled the patched `csgo_gc` (VS2022, Release),
   point `build_pack.py` at your build output:
   ```bash
   cd launcher
   python3 build_pack.py --csgo-gc-dir /path/to/your/built/csgo_gc
   ```
   This produces `csgo-revival-pack.zip` = your patched `csgo_gc` + this repo's
   `gc-config/config.txt` + this repo's custom `items_game.txt`, laid out to drop over a
   CS:GO install.
2. **Publish it.** Upload `csgo-revival-pack.zip` as an asset on a
   **GitHub Release** of this repo (Releases → Draft a new release → attach the zip).
   The default URL `.../releases/latest/download/csgo-revival-pack.zip` then always
   points at your newest pack.
3. **Fill in `install.py`.** Edit the two constants at the top of `launcher/install.py`:
   - `SERVER_URL` → your Oracle inventory server (e.g. `http://<PUBLIC_IP>:8787`)
   - `PACK_URL`  → your release zip URL
4. **Grant your friends their stuff** (see Step 4) using their SteamID64.
5. **Share `install.py` and `launcher.py`** (send both files, or just have them clone the
   repo). That's all they need.

### Friend — do this once

1. Enable **CS:GO Legacy** in Steam (right-click CS2 → Properties → Betas → `csgo_legacy`).
2. Make sure **Python 3** is installed.
3. Run:
   ```bash
   python3 install.py
   ```
   It auto-detects their CS:GO folder and SteamID64, downloads and installs the pack,
   writes their `launcher.cfg`, syncs their granted inventory, and launches the game.
   (If auto-detect fails, it just asks them to paste the folder / SteamID64.)

From then on they launch with `python3 launcher.py` (syncs inventory + boots the game).

### Playing together over Steam P2P

Because everyone installed the **same pack** (same `csgo_gc` build + config + items),
lobbies are compatible. To play:

1. Everyone launches via the launcher so inventories are synced.
2. **Host creates a lobby** in-game; friends **join through Steam** (friends list →
   Join Game, or accept a lobby invite).
3. Host starts the match — traffic rides Steam's relay network. No IPs, no ports.

Cases/skins/trade-ups/Kinkerm's Case all work the same regardless of how you connect —
case opening is handled locally by each player's `csgo_gc`.

> Re-run `build_pack.py` and re-upload whenever you change `config.txt`, `items_game.txt`,
> or rebuild `csgo_gc`; friends get the update next time they run `install.py`.
