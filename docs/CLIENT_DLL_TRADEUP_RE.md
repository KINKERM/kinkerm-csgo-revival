# client.dll RE — the trade-up "CanTradeUp" gate (Covert inputs)

Goal: understand why the **trade-up contract UI won't let you add Covert skins**
(e.g. AWP | Lightning Strike), and whether the client binary can be patched to allow it,
so a Covert → gold trade-up could run through the real contract screen.

> **Honesty / scope.** This analysis is built from the decompiled functions captured from
> *your* Ghidra session plus the game's own data files (`items_game.txt`). I did **not**
> have your `client.dll` in the build sandbox, so **every concrete address, offset and
> byte below must be re-confirmed in your Ghidra/x64dbg before patching** — treat the
> addresses as *labels from your dump*, not verified truth. Where I'm inferring intent
> rather than reading it directly, it's marked **(hypothesis)**.

---

## TL;DR

- The contract screen decides "can this item be added?" from a **native V8 accessor** the
  client exposes to the Panorama JS. In your dump that native is `FUN_105f0d50`
  (JS→native trampoline) → `FUN_105d4d50` (the actual logic), registered near
  `FUN_105e4dc0` where the string **`"CanTradeUp"`** is bound.
- `FUN_105d4d50` has an explicit **`if (rarity == 6)`** branch. `6 = "ancient"` which is
  the **Covert** grade for weapon skins (confirmed in `items_game.txt` rarities: ancient
  `value 6`). So Coverts take a dedicated code path that normal (blue/purple/pink) skins
  do not.
- Coverts are the **top** of the per-collection trade-up chain and have **no
  `next_rarity`** (confirmed: only common/uncommon/rare carry `next_rarity` in this file;
  mythical/legendary/ancient do not). That's the root reason the base client refuses them:
  there is no higher tier to produce.
- **Two independent gates** must both open for a real covert contract: the **client UI
  filter** (this function) *and* the **GC** (`csgo_gc`) accepting 10 coverts and emitting a
  gold. Patching only the client is not sufficient.
- **Recommendation:** the **Gold Trade-Up crate** and the **custom "Kinkerm's Case"**
  already deliver Covert→gold and gold-only rolls *without* touching `client.dll`. Binary
  patching the contract UI is high-effort, fragile across client updates, and still needs
  matching GC work. Keep it as an optional experiment, documented below.

---

## Call graph (from the dump)

```
FUN_105e4dc0                     // registers the V8 bindings for the econ-item JS object
  ├─ v8::FunctionTemplate::New(..., FUN_105f0d50)   // native callback
  ├─ v8::String::NewFromUtf8(...)                   // property name
  └─ ...("CanTradeUp", 1, 0, 2)                     // binds accessor name "CanTradeUp"

FUN_105f0d50(callbackInfo)       // JS -> native trampoline
  ├─ Isolate::Enter / HandleScope
  ├─ item = FUN_10585260(callbackInfo)              // unwrap the item arg
  └─ if (item) FUN_105d4d50(&PTR_PTR_10e0a4d4, callbackInfo)   // <-- the logic

FUN_105d4d50(accessors=&PTR_PTR_10e0a4d4, info)
  rarity = accessors[+0x10](info, 0)                // get item rarity (int)
  if (rarity == 6) {                                // 6 == ancient == COVERT
    x = accessors[+0x1c](info, 0)                   // secondary property (hypothesis: quality / "has upgrade")
    if (x != 0) {
      n   = FUN_10996470(<string>)                  // atoi/strtoll of some string
      hv  = FUN_105ced70(n)                          // wrap int -> cached V8 handle
      if (hv != 0) {
        v = FUN_105d4c10()                           // produce the value to write
        accessors[+0x30](info, v)                    // SET a property (hypothesis: CanTradeUp = v)
      }
    }
  }
```

Supporting functions (identified, not the gate itself):
- `FUN_10996470` = **string → 64-bit integer** (`atoi`/`strtoll`; handles `-`,`+`,`0x`,char
  literal, decimal via `__allmul`).
- `FUN_105ced70` = **V8 small-integer/handle cache** (returns cached handle for `0..999`,
  with `0x7fff7fff`/`0x7fff7ffe` sentinels; otherwise `v8::Integer::New`-style).
- `PTR_FUN_10c68b78` table (FUN_10585560/…80/…90/…) = the **accessor vtable** for the JS
  item object — the getters/setters indexed by `+0x10`, `+0x1c`, `+0x30` above.

---

## What the branch means

`FUN_105d4d50` does **nothing for rarities < 6** — it only acts on rarity `== 6` (Covert).
That is the signature of a **Covert-specific special case**, not a general "can trade up"
computation. Reading it plainly:

- For a Covert item it checks a second property (`+0x1c`). **(hypothesis)** this is the
  item quality or a "does a valid upgrade target exist" flag.
- Only if that's non-zero does it parse a number, look it up, and **write a property back**
  onto the JS item via the setter at `+0x30` — **(hypothesis)** setting `CanTradeUp = true`
  (or storing the covert's upgrade/knife-pool id).

So the client *already contains* covert-aware logic, but it is **conditional**, and for
your AWPs the condition (`+0x1c != 0`, and/or `hv != 0`) is evaluating false, so nothing is
set and the UI leaves the item non-addable.

This is consistent with base CS:GO: Covert → knife trade-ups are a **CS2** feature; the
legacy CS:GO client shipped the scaffolding but gates it off.

---

## The two gates

**Gate A — client UI (this function).** Determines whether the contract screen lets you
drop a Covert into a slot. Controlled by `FUN_105d4d50` / the `CanTradeUp` accessor.

**Gate B — the GC (`csgo_gc`).** Even with Gate A open, submitting the contract sends a
trade-up request to the GC. `csgo_gc` must accept **10 Coverts** and return a gold. Our
revival's `Inventory::TradeUp` path is built around the standard "10 of rarity N → 1 of
rarity N+1" model; Covert has no N+1, so this needs its own branch (we already have
`SelectCovertsForTradeUp` / `UnlockCrateGoldTradeUp`, but those are wired to the **crate**
path, not the contract submit path).

**Both gates must open.** This is why earlier crate-based delivery was chosen: it sidesteps
Gate A entirely.

---

## Experimental patch plan (client, Gate A) — UNVERIFIED, try in your Ghidra

The lowest-risk experiment is to force the Covert branch to always enable the item:

1. **Confirm the mapping.** In Ghidra, set a breakpoint / xref-check that `FUN_105d4d50`'s
   `accessors[+0x10]` really returns the rarity, and that `+0x30` writes the value the JS
   reads as `CanTradeUp`. Verify `FUN_105e4dc0` binds `FUN_105f0d50` to the `"CanTradeUp"`
   name (not a neighbouring accessor).
2. **Option 1 — neutralize the sub-condition.** Patch the `if (x != 0)` (and/or `if (hv
   != 0)`) tests so the setter at `+0x30` always runs for rarity 6. Typically this is
   NOP-ing a `test/jz` after the `+0x1c` call. This makes every Covert report
   addable **(hypothesis)**.
3. **Option 2 — widen the rarity test.** If instead you want the *general* path, the
   `cmp …, 6 ; jne` guarding the whole block can be changed, but that's riskier (the block
   is written assuming Covert semantics).
4. **Rebuild the JS side if needed.** The contract Panorama scripts may *also* filter by
   rarity/next_rarity independently of `CanTradeUp`. If items still won't drop after the
   binary patch, the `.js` in `pak01`/`panorama/` is the next layer (editable without
   binary patching).

> After Gate A: you **must** also implement Gate B in `csgo_gc` — a contract-submit handler
> that accepts 10 Coverts and produces a weighted gold (reuse `SelectCovertsForTradeUp` +
> the gold-roll code already in `inventory.cpp`). Without it the contract will error or do
> nothing on submit.

### Why this is not recommended as the primary route
- **No binary here to verify** — addresses/bytes can shift with any client build.
- **Fragile** — a client update wipes the patch; must be re-derived each time.
- **Incomplete alone** — still needs matching GC work (Gate B).
- **Already solved** — the Gold Trade-Up crate (5 Covert → weighted gold) and the custom
  "Kinkerm's Case" (gold-only roll) give the same player-facing outcome today, with zero
  client patching and full control server-side.

---

## Cross-checked facts (verifiable, not hypotheses)
- `items_game.txt` rarities: `ancient` has `value 6`; `next_rarity` exists only on
  `common`→`uncommon`→`rare`. Covert(ancient) has no next tier → base trade-up excludes it.
- `csgo_gc` reads the same `items_game.txt`; knife/glove ("unusual") pools live in
  `csgo_gc/unusual_loot_lists.txt`, not in the client's `client_loot_lists` (which is why
  the reel work for "Kinkerm's Case" had to add explicit gold entries client-side).
- `Inventory::TradeUp`, `SelectCovertsForTradeUp`, `UnlockCrateGoldTradeUp`,
  `PickRandomGold`, `UnlockGoldOnlyCase` already exist in the patched `csgo_gc` sources.
