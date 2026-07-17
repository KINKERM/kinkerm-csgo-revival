# Hosting Guide — Free, No Port Forwarding

This guide covers two separate things:

1. **The inventory server** — must be reachable by every player's launcher. We host it on a free,
   always-on **Oracle Cloud Always Free** VM so there's **no port forwarding** and it stays up 24/7.
2. **The actual multiplayer game** — we use **Steam P2P lobbies** (supported by `csgo_gc`), so gameplay
   traffic rides over Steam's relay network. No dedicated server and no port forwarding needed.

> TL;DR: Oracle hosts the "who owns what" server. Steam P2P handles people actually playing together.

---

## Part 1 — Host the inventory server on Oracle Cloud (Always Free)

Oracle even publishes their own "host a CS:GO server for free forever" guide, so this is a blessed use
case. We only need the VM for the lightweight Python inventory server, so the free tier is far more than
enough.

### 1.1 Create the account

Sign up at **https://www.oracle.com/cloud/free/**.

Two things that will bite you if you skip them:

- **Home region is permanent.** During signup, pick the region **geographically closest to your players**.
  This is the single biggest factor in latency later. You cannot change it after signup.
- You'll get **$300 trial credits (30 days)** *and* **Always Free** resources. Ignore the credits — the
  Always Free resources are what never expire.

### 1.2 Create the VM

1. In the console: **Menu → Compute → Instances → Create Instance**.
2. **Image and shape:**
   - Image: **Canonical Ubuntu** (22.04 or 24.04 LTS).
   - Shape: click **Change shape → Ampere → VM.Standard.A1.Flex** (the ARM "Always Free" shape).
   - Set **1–2 OCPUs** and **6–12 GB RAM**. The free ceiling (as of mid-2026) is **2 OCPU / 12 GB**
     total across your A1 instances — even 1 OCPU / 6 GB is overkill for this server.
   - *If you see "Out of host capacity"*, that's the well-known free-tier ARM shortage. Retry later or in
     a different availability domain; it does free up.
3. **SSH keys:** choose **Generate a key pair** and **download both** the private and public keys. You
   need the private key to log in.
4. **Networking:** leave "Create new VCN" selected, and ensure **"Assign a public IPv4 address"** is on.
5. Click **Create**. In ~1 minute you'll have a **public IP** — note it down.

### 1.3 Open the server port in the OCI firewall (Security List)

The VM has a real public IP, so there is no router to port-forward — but OCI has its own firewall you must
open.

1. From the instance page, click the **subnet** link → open the **Default Security List**.
2. **Add Ingress Rule:**
   - Source Type: **CIDR**
   - Source CIDR: `0.0.0.0/0`  (anywhere)
   - IP Protocol: **TCP**
   - Destination Port Range: `8787`
   - Description: `revival inventory server`
3. Save.

### 1.4 Connect and set up

From your laptop (replace with your key path + IP):

```bash
chmod 600 ~/Downloads/ssh-key.key
ssh -i ~/Downloads/ssh-key.key ubuntu@<YOUR_PUBLIC_IP>
```

On the VM, also open the port in Ubuntu's own firewall (Oracle images ship with iptables rules that block
everything except SSH):

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8787 -j ACCEPT
sudo netfilter-persistent save        # persist across reboots
# (if netfilter-persistent is missing: sudo apt-get install -y iptables-persistent)
```

Install git + Python and grab this repo:

```bash
sudo apt-get update
sudo apt-get install -y git python3
git clone https://github.com/KINKERM/kinkerm-csgo-revival.git
cd kinkerm-csgo-revival/server
```

### 1.5 Build your catalog and do a first run

You need CS:GO's `items_game.txt` to build the full catalog. Two options:

- **Quick start:** `cp data/catalog.sample.json data/catalog.json` (tiny starter set of cases).
- **Full catalog:** copy `items_game.txt` from your local CS:GO install up to the VM with `scp`, then:
  ```bash
  python3 build_catalog.py --items-game /path/on/vm/items_game.txt --out data/catalog.json
  ```

Test it:

```bash
python3 revival_server.py --host 0.0.0.0 --port 8787
```

It prints your **admin token** (also saved to `data/server_config.json`). From your laptop, verify it's
reachable over the internet:

```
http://<YOUR_PUBLIC_IP>:8787/health   -> should say "ok"
```

Then stop it with Ctrl-C and make it permanent (next step).

### 1.6 Run it forever with systemd

This repo ships a unit file at [`deploy/csgo-revival.service`](../deploy/csgo-revival.service). Install it:

```bash
# from the repo root on the VM
sudo cp deploy/csgo-revival.service /etc/systemd/system/
# edit the paths/user inside if you cloned somewhere other than /home/ubuntu
sudo systemctl daemon-reload
sudo systemctl enable --now csgo-revival
sudo systemctl status csgo-revival      # confirm it's active
journalctl -u csgo-revival -f           # live logs (includes the admin token on first start)
```

The server now starts on boot and restarts if it crashes.

### 1.7 Point everything at the VM

- **Launcher** (`launcher.cfg`): `server_url=http://<YOUR_PUBLIC_IP>:8787`
- **Admin CLI** from your laptop:
  ```bash
  python3 admin.py --server http://<YOUR_PUBLIC_IP>:8787 --token <ADMIN_TOKEN> grant-case <steamid64> weapon_case_1
  ```

> **HTTPS (optional):** the inventory endpoint carries no secrets, so plain HTTP is fine. If you want TLS,
> put Caddy in front (`caddy reverse-proxy --from your.domain --to :8787`) — it auto-provisions a
> certificate. You'd then use `https://your.domain` as the `server_url`.

---

## Part 2 — Multiplayer via Steam P2P lobbies (no server, no port forwarding)

`csgo_gc`'s feature list includes **functional lobbies** and **networking using Steam's P2P interface**.
Steam P2P routes traffic through Valve's relay network, so a small group can play together **without
anyone port-forwarding or running a dedicated server**.

### How it works for players

1. Everyone installs **CS:GO Legacy** + **`csgo_gc`** (see [SETUP.md](SETUP.md)).
2. Everyone runs the **launcher** so their granted inventory syncs from your Oracle server.
3. The host **creates a lobby** in-game; friends **join via the Steam lobby/invite** (Steam friends list →
   Join Game, or a lobby invite).
4. The host starts the match. Traffic flows over Steam P2P — no IPs to share, no ports to open.

### Notes and limits

- **Best for friend groups**, not anonymous public matchmaking (there is no central matchmaker — that
  genuinely requires a Valve backend and is out of scope).
- Everyone must be on the **same CS:GO Legacy build** and the **same `csgo_gc` release** so the lobby is
  compatible.
- Cases still open **client-side and for free** regardless of how you connect — case opening is handled by
  `csgo_gc` locally, independent of the game session.

### If you later want a persistent dedicated server

The Oracle VM is ARM, but the CS:GO server binary (`srcds`) is 32-bit x86, so it needs the **box64**
emulation layer on ARM. It works but is extra setup. Alternatives:

- Run `srcds` under **box64** on the same A1 VM, or
- Spin up one of Oracle's free **x86 micro** instances (native, but only ~1 GB RAM — small servers only).

For most people, **Steam P2P lobbies are the simpler and recommended path.**

---

## Cost recap

| Piece | Where | Cost | Port forwarding? |
|-------|-------|------|------------------|
| Inventory / admin server | Oracle A1 Always Free VM | Free forever | No (public IP + OCI security list) |
| Multiplayer gameplay | Steam P2P lobbies | Free | No (Steam relay) |
| Case opening | Each player's `csgo_gc` (local) | Free | N/A |
