# csgo_gc pity-system patch

Modified `csgo_gc` source files that add a **pity system** to case opening:

- the longer you go without a gold (knife/glove), the higher your gold odds climb,
- a gold is **guaranteed** once an open would reach **350** (tunable), and
- the counter **resets to 0** every time you hit a gold.

The counter is stored per player in `csgo_gc/pity.txt` (created automatically).

> Derivative of [`csgo_gc`](https://github.com/mikkokko/csgo_gc), licensed under the
> 2-Clause BSD License, (c) Mikko Kokko. Only `case_opening.cpp` and `case_opening.h`
> are changed; everything else is upstream.

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

## Step 4 — Apply the pity patch
Copy the two files from **this** repo's `csgo_gc-patch\` folder over the originals in
the source you just downloaded, replacing them when asked:

```
csgo_gc-patch\case_opening.cpp  ->  <source>\csgo_gc\case_opening.cpp
csgo_gc-patch\case_opening.h    ->  <source>\csgo_gc\case_opening.h
```

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
