# csgo_gc pity-system patch

Modified `csgo_gc` source files that add three revival features on top of upstream:

**1. Pity system** (case opening) — `case_opening.cpp` / `case_opening.h`
- the longer you go without a gold (knife/glove), the higher your gold odds climb,
- a gold is **guaranteed** once an open would reach **350** (tunable), and
- the counter **resets to 0** every time you hit a gold.
- The counter is stored per player in `csgo_gc/pity.txt` (created automatically).

**2. Skin quality fix** — `item_schema.cpp`
- normal (non-StatTrak) skins are created at the **Unique** quality so they're
  eligible for trade-up contracts (see the trade-up section below).

**3. Trade-up contracts** — `gc_client.cpp` / `gc_client.h`, `inventory.cpp` / `inventory.h`,
`item_schema.cpp` / `item_schema.h`
- exchange 10 same-rarity skins for one skin of the next rarity up, with the output
  float derived from the inputs (the real CS:GO formula). See the trade-up section.

> Derivative of [`csgo_gc`](https://github.com/mikkokko/csgo_gc), licensed under the
> 2-Clause BSD License, (c) Mikko Kokko. Changed files: `case_opening.*`,
> `gc_client.*`, `inventory.*`, `item_schema.*`; everything else is upstream.

## Why this needs compiling
The case-opening RNG lives inside `csgo_gc` (C++), not in the config or the revival
server. The config only holds static rarity weights — no memory. A pity system needs
a persistent counter + logic, so `csgo_gc` itself has to be rebuilt with these files.

## Tuning (top of `case_opening.cpp`)
- `PityMax` (default `350`) — opens until a guaranteed gold.
- `PityBoost` (default `40.0`) — how hard the odds ramp up on the way there.

---

# Full build guide — Windows, from scratch

This assumes you have **nothing** installed yet. Budget ~30-60 minutes, a good
internet connection, and ~10 GB of free disk (the protobuf dependency is large).

## What you're doing, in plain terms
`csgo_gc` is a C++ program. To add the pity system you change two of its source
files (already done for you here) and then **compile the whole thing into new
`csgo.exe` / `srcds.exe` files**, which you swap into your CS:GO folder just like a
normal `csgo_gc` install.

## Step 1 — Install Git  (required, even if you download the ZIP)
**Why:** when you build, CMake automatically downloads three libraries (protobuf,
Crypto++, funchook) **using Git**. No Git = the build fails immediately.
1. Go to https://git-scm.com/download/win — the download starts automatically.
2. Run the installer and click **Next** through every screen (the defaults are fine).
3. Verify: open a **new** PowerShell window and run `git --version`. You should see a
   version number. If it says "not recognized", restart your PC so PATH updates.

## Step 2 — Install the compiler + CMake (Visual Studio 2022 Community, free)
> ⚠️ **Use stable Visual Studio 2022 (version 17) — NOT a Preview/Insider build**
> (e.g. one that installs under `...\Visual Studio\18\...`). Preview builds ship
> bleeding-edge CMake/MSVC that stable CMake doesn't recognize, which cascades into
> generator errors, missing `/std:c++17`, and protobuf include failures. If you're
> hitting a chain of build errors, a preview VS is almost always why.
1. Go to https://visualstudio.microsoft.com/downloads/ and download
   **Visual Studio 2022 Community** (free).
2. Run the installer. When it shows **"Workloads"**, tick
   **"Desktop development with C++"** (top-left box). Leave the defaults checked on
   the right — that bundles the MSVC compiler **and CMake**, so you don't install
   CMake separately.
3. Click **Install** and wait (it's a few GB).

## Step 3 — Get the csgo_gc source code
Either option works (there are no git submodules, so the ZIP is complete):

- **Option A (Git):** open PowerShell and run
  `git clone https://github.com/mikkokko/csgo_gc.git`
- **Option B (no cloning):** open https://github.com/mikkokko/csgo_gc → green
  **Code** button → **Download ZIP** → extract it somewhere simple like `C:\csgo_gc`.

Either way you end up with a folder containing a `csgo_gc` subfolder, `launcher`,
`CMakeLists.txt`, etc.

## Step 4 — Apply the patch
Copy the patched files from **this** repo's `csgo_gc-patch\` folder over the originals
in the source you just downloaded, replacing them when asked:

```
csgo_gc-patch\case_opening.cpp  ->  <source>\csgo_gc\case_opening.cpp
csgo_gc-patch\case_opening.h    ->  <source>\csgo_gc\case_opening.h
csgo_gc-patch\gc_client.cpp     ->  <source>\csgo_gc\gc_client.cpp
csgo_gc-patch\gc_client.h       ->  <source>\csgo_gc\gc_client.h
csgo_gc-patch\item_schema.cpp   ->  <source>\csgo_gc\item_schema.cpp
csgo_gc-patch\item_schema.h     ->  <source>\csgo_gc\item_schema.h
csgo_gc-patch\inventory.cpp     ->  <source>\csgo_gc\inventory.cpp
csgo_gc-patch\inventory.h       ->  <source>\csgo_gc\inventory.h
```

- `case_opening.*` = the **pity system** (working).
- `item_schema.*` + `inventory.*` + `gc_client.*` = **trade-up contracts** and the
  **skin quality fix** that makes normal skins eligible (see the trade-up section
  at the bottom of this file).

No CMake changes are needed — all eight files already exist in the project, so just
overwrite and rebuild.

(You can do this in File Explorer with copy/paste, or in PowerShell with `copy`.)

## Step 5 — Build it (32-bit)
1. From the Start menu open **"x64 Native Tools Command Prompt for VS 2022"**
   (search for it). This is a terminal that already knows where the compiler is.
2. Go to the source folder, e.g.:
   ```
   cd C:\csgo_gc
   ```
   (or `cd C:\csgo_gc\csgo_gc-master` if you used the ZIP — go to the folder that
   contains `CMakeLists.txt`.)
3. Configure the project as **32-bit** (required — the CS:GO client is 32-bit):
   ```
   cmake -A Win32 -B build
   ```
   **This first step is slow** — CMake downloads protobuf/Crypto++/funchook here. Let
   it finish; it's not frozen.
4. Compile:
   ```
   cmake --build build --config Release
   ```
   This also takes a while the first time.

## Step 6 — Find your freshly built executables
Look inside the `build` folder for the produced launcher files — the same set a normal
`csgo_gc` release ships (`csgo.exe`, `srcds.exe`, etc.). They're typically under a
`Release` subfolder (e.g. `build\launcher\Release\`). If unsure, search the `build`
folder for `csgo.exe`.

## Step 7 — Install your build into CS:GO
1. In your CS:GO folder, **back up** the current `csgo.exe` (rename to `csgo.exe.bak`),
   and any other launcher exes you're replacing.
2. Copy your newly built exes over them.
3. Keep your existing `csgo_gc\config.txt` (drop odds) and `inventory.txt`.
4. Launch and open cases. A `csgo_gc\pity.txt` file appears and starts counting; hit
   350 without a gold and the next open is guaranteed gold.

---

## Troubleshooting
- **`CMP0000` / "No cmake_minimum_required command is present" / it suggests
  `cmake_minimum_required(VERSION 4.x)`** → your CMake is too new (CMake 4.x, often
  bundled with *preview* Visual Studio builds like "18"). CMake 4 dropped support for
  the old minimums that csgo_gc's dependencies still use. Fix: delete the `build`
  folder and re-configure with a compatibility flag:
  ```
  rmdir /s /q build
  cmake -A Win32 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -B build
  cmake --build build --config Release
  ```
  If it still fails, install **stable CMake 3.31** (cmake.org) or **stable Visual
  Studio 2022 v17** (not the preview) and use that instead. Always delete `build`
  between attempts — CMake caches the failed state.
- **`NMake Makefiles does not support platform specification` / `CMAKE_C_COMPILER not
  set`** → CMake picked the NMake generator (usually because you have a *preview*
  Visual Studio that CMake doesn't recognize as a VS generator, so `-A Win32` can't be
  used). Build via the compiler environment instead: open the **"x86 Native Tools
  Command Prompt for VS"** (the x86 one = 32-bit), then:
  ```
  rmdir /s /q build
  cmake -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -B build
  cmake --build build
  ```
  Note: no `-A Win32` here — the x86 prompt supplies the 32-bit compiler. The cleanest
  alternative is installing **stable Visual Studio 2022 (v17, not preview)**, after
  which the normal `cmake -A Win32 -B build` works.
- **Compile fails with `STL4038 ... available only with C++17` and/or `C1083: Cannot
  open ... google/protobuf/port_def.inc`** → your compiler is a *preview* MSVC that
  CMake can't drive correctly (it's not applying `/std:c++17` and protobuf's include
  dirs aren't propagating). Fix: install **stable Visual Studio 2022 (v17)**, open its
  *"x64 Native Tools Command Prompt for VS 2022"*, then:
  ```
  rmdir /s /q build
  cmake -G "Visual Studio 17 2022" -A Win32 -B build
  cmake --build build --config Release
  ```
- **`cmake` isn't recognized** → you didn't open the *"x64 Native Tools Command Prompt
  for VS 2022"*. Open that specific terminal (it puts CMake on PATH), or reinstall the
  C++ workload from Step 2.
- **Build stops with a Git / FetchContent / "could not find git" error** → Git isn't
  installed or isn't on PATH. Do Step 1 and open a fresh terminal.
- **Download of protobuf/cryptopp fails** → it's your internet/firewall. Re-run
  `cmake -A Win32 -B build`; it resumes.
- **"Cannot open csgo.exe" when copying in** → the game or Steam is still running.
  Fully close CS:GO and Steam first.
- **It builds but cases still open with old odds** → you copied the exes but the game
  is loading a different install; confirm you replaced the exe in the same folder
  Steam launches (Browse Local Files).

**I could not compile or test this from here** — it's source you build yourself. If a
step throws an error, copy the **exact** message and send it to me; I'll get you past it.


---

## Confirmed working build (the path that actually succeeded)

If the build fails with dependencies never downloading — no "Fetching protobuf..."
lines, no `build/_deps` folder, and configure finishing in ~10 seconds — your
top-level `CMakeLists.txt` has been edited/damaged and `FetchContent` isn't
running. The reliable fix is a **clean clone plus only the two patched files**:

1. Fresh clone into a new folder:
   ```
   git clone https://github.com/mikkokko/csgo_gc.git csgo_gc_clean
   ```
2. Copy ONLY these patched files over the originals (make no other edits):
   ```
   csgo_gc-patch\case_opening.cpp  ->  csgo_gc_clean\csgo_gc\case_opening.cpp
   csgo_gc-patch\case_opening.h    ->  csgo_gc_clean\csgo_gc\case_opening.h
   csgo_gc-patch\gc_client.cpp     ->  csgo_gc_clean\csgo_gc\gc_client.cpp
   csgo_gc-patch\gc_client.h       ->  csgo_gc_clean\csgo_gc\gc_client.h
   csgo_gc-patch\item_schema.cpp   ->  csgo_gc_clean\csgo_gc\item_schema.cpp
   csgo_gc-patch\item_schema.h     ->  csgo_gc_clean\csgo_gc\item_schema.h
   csgo_gc-patch\inventory.cpp     ->  csgo_gc_clean\csgo_gc\inventory.cpp
   csgo_gc-patch\inventory.h       ->  csgo_gc_clean\csgo_gc\inventory.h
   ```
3. Build with stable Visual Studio 2022 (v17), from its "x64 Native Tools Command
   Prompt for VS 2022":
   ```
   cd csgo_gc_clean
   cmake -G "Visual Studio 17 2022" -A Win32 -B build
   cmake --build build --config Release
   ```
   During configure you should see protobuf/cryptopp/funchook download (takes a few
   minutes). The `'pwsh.exe' is not recognized` lines during the build are harmless.

### Built files land here
- `build\launcher\Release\csgo.exe`
- `build\launcher\Release\srcds.exe`
- `build\csgo_gc\Release\csgo_gc.dll`   <- the pity patch lives in this DLL

### Install (back up the originals first)
Replace these in your CS:GO install, keeping your existing `config.txt` /
`inventory.txt`:
- `<csgo>\csgo.exe`              <- built csgo.exe
- `<csgo>\srcds.exe`             <- built srcds.exe
- `<csgo>\csgo_gc\csgo_gc.dll`   <- built csgo_gc.dll

On the first case you open, `csgo_gc\pity.txt` is created and counts up; it resets
to 0 on a gold, and forces a gold at `PityMax` (default 350).

### Store purchases (optional)
Buying from the in-game store needs the Steam overlay enabled (Steam → Settings →
In Game, and the game's Properties → Enable Steam Overlay) and the game launched
via Steam so the overlay is injected. Admin-granting cases works without any of
that and is the simplest way to feed cases in.


---

# Trade-up contracts

CS:GO trade-up contracts let you turn **10 skins of the same rarity** into **1 skin
of the next rarity up**, with the output's wear (float) derived from the inputs.
`csgo_gc` never implemented this — the game *sends* a craft request
(`k_EMsgGCCraft`, id 1002) but upstream just logged it as "unhandled" and nothing
happened. This build implements it.

**Status: implemented**, including the CS2 **5 Covert → 1 gold (knife/glove)** recipe.
Standard trade-ups go Consumer → ... → Classified → Covert; a Covert contract yields a
random knife/glove from the case's gold pool.

The float math we're targeting (same as real CS:GO), per skin:

```
normalized = (skinFloat - skinPaintMin) / (skinPaintMax - skinPaintMin)
avg        = average(normalized over all inputs)
outputFloat = avg * (outputPaintMax - outputPaintMin) + outputPaintMin
```

Output selection: pick one of the input skins' collections (weighted by how many
inputs came from it), then a random skin of the next tier up from that collection;
StatTrak in -> StatTrak out; gloves/knives are never StatTrak.

## Fix: normal skins weren't eligible for trade-ups (`item_schema.cpp`)
Symptom: in the Trade Up Contract screen, only **StatTrak** skins showed as
eligible; every normal skin was missing.

Cause: the client only accepts items of quality **Unique** (normal contract) or
**Strange** (StatTrak contract) as trade-up inputs. csgo_gc created case-opened
normal skins at quality **Normal (0)** (the item-schema default for weapon defs),
while StatTrak skins were correctly set to **Strange (9)** — so only StatTrak
appeared. Real CS:GO skins are quality **Unique (4)**.

Fix (two parts):
- `item_schema.cpp` — `CreateItemFromLootListItem` now promotes painted weapon
  skins from Normal to Unique. All **newly** opened cases produce eligible skins.
- Server `inventory.py` — when it renders `inventory.txt`, any painted skin
  (has paint-kit attribute 6) still stored at quality Normal is bumped to Unique.
  This makes your **existing** stash eligible without re-opening anything; just
  relaunch so the launcher re-syncs the inventory. Knives/gloves (Unusual) and
  StatTrak (Strange) are left untouched.

## How it works
The craft message (`k_EMsgGCCraft`, 1002) is a non-protobuf "struct" message. Its
wire format was confirmed by logging a real contract:

```
[18-byte struct header]
uint16 recipe
uint16 itemCount
uint64 itemIds[itemCount]   // low 32 bits = account id, high 32 bits = item id
```

- **`gc_client.cpp`** reads the input item ids and hands them to `Inventory::TradeUp`.
- **`item_schema.cpp` / `.h`** parse the `item_sets` (collections) block from
  `items_game.txt`, computing each skin's grade the same way the game does
  (`PaintedItemRarity`), and expose `FindCollectionForItem`.
- **`inventory.cpp` / `.h`** (`Inventory::TradeUp`) validate the inputs, compute the
  result, create the output skin, and destroy the inputs.

Validation and selection (matches real CS:GO):
- every input must be a painted skin that belongs to a known collection,
- all inputs must share the **same rarity** and the **same StatTrak state**,
- the output collection is chosen from the inputs' collections, **weighted by how
  many inputs came from each**,
- the output skin is a **uniform-random next-rarity** skin from that collection,
- **StatTrak in → StatTrak out**,
- output float uses the normalized average:
  ```
  normalized  = (skinFloat - skinPaintMin) / (skinPaintMax - skinPaintMin)
  avg         = average(normalized over all inputs)
  outputFloat = avg * (outputPaintMax - outputPaintMin) + outputPaintMin
  ```

The crafted item is delivered with the same SO-cache Create/Destroy path that case
opening uses (proven to work). We deliberately do **not** send a
`k_EMsgGCCraftResponse` — csgo_gc's outgoing struct-message header is known-broken
(noted in `gc_message.cpp`), so a bad response could misbehave. If the contract
screen ends up needing that response to close cleanly, that's the follow-up.

## How to use it
1. Copy all eight patched files (see the copy list above), rebuild, and swap the DLL
   in — exactly like the pity patch. The trade-up code lives in the same
   `csgo_gc.dll`.
2. Optional but recommended: `log_output 1` in `csgo_gc\config.txt` so you can see the
   `Craft:` / `tradeup:` log lines if anything is rejected.
3. Make sure your normal skins are eligible (the quality fix above). If you built
   your stash before that fix, relaunch once so the server re-syncs `inventory.txt`.
4. In game: open the inventory, start a **Trade Up Contract**, fill all 10 slots with
   same-rarity skins, and complete it. The 10 inputs are consumed and one next-tier
   skin appears in your inventory.

If a contract is rejected, the console prints exactly why (e.g. `tradeup: mixed
rarities`, `... not in any collection`, or the Covert→gold notice).

## 5 Covert → gold (knife/glove)
Covert inputs run the CS2 gold recipe instead of a normal next-rarity trade-up:
- On startup the schema walks every case loot list; a case list contains its
  collection's skins plus exactly one **unusual** sublist (the knife/glove pool,
  loaded from `csgo_gc/unusual_loot_lists.txt`). Each skin is mapped to that pool.
- A Covert contract picks a gold pool from one of the input collections (weighted by
  input count), then a random knife/glove from it. StatTrak carries over to knives
  that support it (gloves and some newer knives can't be StatTrak); the output float
  uses the same normalized-average formula.
- Fill the contract with Covert skins (the contract uses 5 slots for this recipe).
- If a Covert skin has no known gold pool, the contract is rejected with a log line
  (`... has no known gold pool`) and nothing is consumed.

## Known limitations
- The trade-up "reveal" animation may differ from retail since we don't send a craft
  response; the resulting item still lands in your inventory via the SO cache.

> Derivative of [`csgo_gc`](https://github.com/mikkokko/csgo_gc), 2-Clause BSD,
> (c) Mikko Kokko. Changed files: `case_opening.*` (pity), and `gc_client.*`,
> `inventory.*`, `item_schema.*` (trade-up contracts + skin quality fix).
