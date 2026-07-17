# CS:GO Revival

A self-hosted revival stack for **CS:GO Legacy** where **you** run everything.

- You (the admin) grant cases and items to players from a central server.
- Cases open **for free** — no key required — using the exact CS:GO opening flow and animation.
- A small C++ launcher boots CS:GO Legacy and pulls each player's inventory down from your server before the game starts.
- No trading, no real-money economy, no Valve backend. Everything is local to your servers.

This project is **not** a reimplementation of Valve's Game Coordinator. It builds on top of
[`csgo_gc`](https://github.com/mikkokko/csgo_gc) (an open-source in-process Game Coordinator for CS:GO),
which already restores inventories, case opening, the in-game store and dedicated-server support without a
centralized server. This repo adds the two pieces `csgo_gc` does not provide:

1. **A central inventory/admin server** so *you* control what every player owns (instead of each player
   editing their own local `inventory.txt`).
2. **A C++ launcher** that syncs a player's inventory from your server, drops it into `csgo_gc/inventory.txt`,
   and launches CS:GO Legacy.

## How it fits together

```
                         ┌──────────────────────────────┐
        admin.py  ─────▶ │        revival server         │   (your machine / VPS)
   (grant cases/items)   │  - players.json  (who owns    │
                         │      what)                    │
                         │  - catalog.json  (cases/keys) │
                         │  - serves inventory.txt       │
                         └───────────────┬───────────────┘
                                         │  GET /inventory/<steamid64>
                                         ▼
                         ┌──────────────────────────────┐
   player runs  ──────▶  │     csgo-launcher (C++)       │
                         │  1. fetch inventory.txt       │
                         │  2. write csgo_gc/inventory.txt│
                         │  3. launch CS:GO Legacy        │
                         └───────────────┬───────────────┘
                                         ▼
                         ┌──────────────────────────────┐
                         │   CS:GO Legacy + csgo_gc      │
                         │  opens cases locally (free)   │
                         └──────────────────────────────┘
```

## Multiplayer & hosting

- **Playing together:** use **Steam P2P lobbies** (built into `csgo_gc`). The host creates a lobby, friends
  join via Steam — traffic rides Steam's relay network, so **nobody needs to port-forward** or run a
  dedicated server. Best for friend groups; there is no public matchmaker (that requires a Valve backend).
- **Reaching the inventory server over the internet without port forwarding:** host it on a free, always-on
  **Oracle Cloud Always Free** VM (it has a real public IP). Step-by-step in
  [`docs/HOSTING.md`](docs/HOSTING.md), including a `systemd` service so it survives reboots.
- Case opening always happens **client-side and for free** via `csgo_gc`, independent of how you connect.

## Layout

| Path                 | What it is                                                            |
|----------------------|-----------------------------------------------------------------------|
| `server/`            | Python (stdlib-only) inventory + admin server, catalog parser, admin CLI |
| `server/start-server.*` | One-command start helpers (`.sh` for Linux/macOS, `.bat` for Windows) |
| `launcher/`          | C++ launcher (CMake) that syncs inventory and boots CS:GO Legacy       |
| `gc-config/`         | A tuned `csgo_gc/config.txt` for free case opening                     |
| `deploy/`            | `systemd` unit for running the server 24/7 on a VM                     |
| `docs/SETUP.md`      | Full end-to-end setup guide                                            |
| `docs/HOSTING.md`    | Free no-port-forward hosting (Oracle Cloud) + Steam P2P lobbies        |

## Legal / scope note

This is a **free, cosmetic, community-only** recreation. Items exist only on your servers; they are not real
Steam inventory assets, cannot be traded on the Steam Market, and have no monetary value. Do **not** attach
real money to case opening — that turns it into online gambling with an entirely different legal footprint.

To get running, start with [`docs/SETUP.md`](docs/SETUP.md); to host it online for free without port
forwarding, follow [`docs/HOSTING.md`](docs/HOSTING.md).
