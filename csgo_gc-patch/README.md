# csgo_gc pity-system patch

This folder contains modified versions of two `csgo_gc` source files that add a
**pity system** to case opening:

- the longer you go without a gold (knife/glove), the higher your gold odds climb,
- a gold is **guaranteed** once an open would reach **350** (tunable), and
- the counter **resets to 0** every time you hit a gold.

The counter is stored per player in `csgo_gc/pity.txt` (created automatically).

> This is a derivative of [`csgo_gc`](https://github.com/mikkokko/csgo_gc), which is
> licensed under the 2-Clause BSD License, (c) Mikko Kokko. Only `case_opening.cpp`
> and `case_opening.h` are changed; everything else is upstream.

## Why this needs compiling
The case-opening RNG lives inside `csgo_gc` (C++), not in the config or the revival
server. The config only holds static rarity weights — it has no memory. A pity
system needs a persistent counter + logic, so `csgo_gc` itself must be rebuilt with
these files.

## Tuning
Edit the constants at the top of `case_opening.cpp`:
- `PityMax` (default `350`) — opens until a guaranteed gold.
- `PityBoost` (default `40.0`) — how hard the odds ramp up on the way there. The
  ramp is quadratic, so it stays near-normal early and spikes near `PityMax`.

## Build steps (Windows, 32-bit)
You need **Git**, **CMake 3.20+**, and a **C++17 compiler** (Visual Studio 2017 or
newer). In the VS installer, pick the **"Desktop development with C++"** workload —
that gives you MSVC + CMake.

```powershell
# 1. Get the source
git clone https://github.com/mikkokko/csgo_gc.git
cd csgo_gc

# 2. Apply the patch: copy these two files over the originals
#    (from this repo: csgo_gc-patch\case_opening.cpp and case_opening.h)
copy /y "<path>\csgo_gc-patch\case_opening.cpp" "csgo_gc\case_opening.cpp"
copy /y "<path>\csgo_gc-patch\case_opening.h"   "csgo_gc\case_opening.h"

# 3. Configure (32-bit is required for the Windows client) and build
cmake -A Win32 -B build
cmake --build build --config Release
```

If CMake complains about missing dependencies, let it fetch them (csgo_gc pulls in
Crypto++, funchook, diStorm3 and protobuf as part of its build). A first build can
take a while.

## Install your build
The build produces the same launcher executables a normal `csgo_gc` release ships
(`csgo.exe`, `srcds.exe`, etc.). Install exactly like the official release:
1. Back up the current executables in your CS:GO folder.
2. Copy your freshly built executables over them.
3. Keep your existing `csgo_gc\config.txt` (drop odds) and `inventory.txt`.
4. Launch and open cases — `pity.txt` appears and starts counting.

## Heads up
This has **not** been compiled or tested here — it's source you build yourself.
Setting up the 32-bit toolchain + dependencies is the hard part. If the build
errors out, send me the exact CMake/compiler error and I'll help you through it.
