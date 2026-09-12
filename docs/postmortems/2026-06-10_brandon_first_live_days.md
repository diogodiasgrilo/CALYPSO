# Post-mortem — Brandon variant C, first live-paper days (Jun 9–10, 2026)

**Scope:** Variant **C** (`hydra_variant_c`, `BrandonHydraStrategy`, LIVE IBKR paper, 7 contracts, narrow 5pt spreads, delta-target 8δ + GEX). Jun 9 was C's first live-paper day. Variants A (baseline HYDRA, dry-run) and B (Brandon, dry-run) ran alongside.

**One-line:** A chain of issues let C's Jun-10 Entry #1 short put ride from positive cushion to **−36% / ~max loss with no stop firing** — the visible symptom of a stop-design gap, amplified by a strike mis-placement and a mark-inflation bug. All four issues are fixed; the deeper question of whether the stop design *fits* Brandon's narrow spreads is still open (see "What we are NOT sure about").

---

## What happened (Jun 10, variant C)

- **10:48 ET** — Entry #1 opened: full IC `C 7425/7430  P 7290/7285`, credit $1,380, 7c. The 7290 short put was intended to be ~8δ but landed ~35δ (much closer to ATM than designed).
- **Afternoon** — SPX sat on 7290, then broke down. The put went ITM.
- **15:50:03 ET** — C's log: *"BRANDON-HYDRA-SHADOW E#1 put: credit+buffer stop WOULD fire — SV $4200 ≥ trigger $3130 … this is shadow only."* The stop that should have protected the position **logged but did not act**.
- **15:55–16:00 ET** — cushion went negative, bottoming at **−36%**, SV ~$4,270. No stop fired all day (0 `BRANDON-BREACH`, 0 `MKT-025/046`).
- **Settlement** — SPX closed ~7266 (below both 7290 and 7285), so the put spread expired at max width. **True settled loss ≈ −$2,120** (the dashboard's −$3,384 mark was inflated — see bug #3).

Variant A never went negative (wider strikes kept its short OTM; it expired worthless for +$122.50). Variant B (Brandon, dry-run) showed the identical shadow-stop-no-fire pattern on its own Entry #2 put. So the behavior is a property of the **Brandon stop design**, not of HYDRA's real credit+buffer stop.

---

## The four bugs (all fixed)

### 1. GEX fetch failure → strike mis-placement  *(upstream cause)*
The Polygon GEX fetch made ~80 **serial** per-contract calls (5s timeout each, **no retry**), and a single chain-pull timeout aborted the whole fetch. When it timed out, the delta-target picker used a **stale** cached profile → the "8δ" put was placed at ~35δ. This is why Entry #1's short sat so close to ATM.
**Fix (`ecb5aef`):** parallelize hydration (ThreadPoolExecutor), retry the chain pull with backoff, and a **stale-greeks guard** (`_GEX_MAX_STRIKE_AGE_S=45`) that falls back to the conservative OTM-multiplier + alerts when the live profile is stale. *(Not the Saxo→IBKR migration — Polygon is broker-independent; the GEX path is byte-identical main..HEAD and predates the cutover.)*

### 2. L-C2 — credit+buffer stop was shadow-only when GEX is armed  *(why no stop fired)*
On B/C the credit+buffer stop runs **shadow** (logs, never closes) whenever GEX is armed; the live stop is the **GEX breach-exit**, which only fires on a sustained breach of a decel-wall edge. Jun-10's only decel wall sat ~340pt below the 7290 short, so the breach-exit **could never fire** — leaving the short with **no acting stop** while the credit+buffer watched in shadow.
**Fix (`c0281d9`, deployed 16:14 ET Jun 10):** the credit+buffer now **acts as a live backstop in both GEX states** — the GEX breach gets first crack (primary), and if it doesn't close a side, the credit+buffer fires (MKT-046-confirmed). Mutually exclusive per tick → no double-stop.

### 3. L-C2b — spread_value had no structural max-width clamp  *(inflated everything)*
`spread_value` = `(short_mid − long_mid) × 100 × contracts` from two **independently-mid'd** legs. During the wide close-auction quotes it marked the put at **$4,270 — impossible on a $3,500-max (5pt × 7c) spread**. That inflated the displayed −36% cushion and the −$3,384 P&L (true ≈ −$2,120), **and** fed a noisy value into MKT-046 (oscillating across the trigger resets the 10s timer — degrading the very backstop we'd just shipped).
**Fix (`5b500c7`):** clamp call/put `spread_value` to `[0, width × 100 × contracts]`. Pure mark fix — realized P&L (settlement/fills) is untouched.

### 4. Entry #2 net-debit inversion  *(already-fixed; fired before the fix went live)*
Entry #2 legged in inverted — long put 7245 @ $9.60 vs short put 7250 @ $8.80 → put credit **−$560**, total **−$430** (a guaranteed loser). This is the SELL-floor / GUARD-INVERT defect; it fired at 11:19, before that fix (`9cdfb51`) was live on C. Protected now.

---

## What we got WRONG (assumptions overturned)

- **"C's positions are protected by the credit+buffer stop (17% cushion)."** They were not — that stop was shadow when GEX is armed. The cushion number was real, but nothing was acting on it.
- **"The GEX breach-exit is a sufficient primary stop."** It only protects when a relevant decel wall sits near the short. With no such wall, it provides **zero** protection.
- **"Entry #1's strikes / $1,380 credit are per-strategy."** The put was mis-placed (~35δ, not 8δ) off a stale GEX profile — we never actually saw the intended 8δ selection.
- **"The dashboard cushion/P&L are accurate."** They were inflated by the un-clamped spread_value.
- **"Combos/bundled spread orders might work on the paper account."** They don't — the probe produced phantom `PendingSubmit` orders that can't fill (hence C legs its spreads, with brief naked-margin windows → the 10c→7c sizing).

---

## What we are NOT sure about (open questions → see the sweep plan)

1. **Does the credit+buffer stop design even FIT Brandon's narrow 5pt spreads?** The $1,750 put buffer puts the stop at **$3,130 on a $3,500-max spread** — it only triggers at ~89% of max loss, and stopping a near-max defined-risk spread during wide quotes can be **worse than letting it settle**. These buffers were tuned for the *original* HYDRA (wider spreads, Saxo data). Strong suspicion of a mismatch.
2. **Does the GEX breach-exit ever provide timely protection?** It never fired today. The wall-relevance filter may rarely produce a fire near the short.
3. **Is stopping a 0DTE *defined-risk* spread advisable at all**, vs hold-to-expiry (HYDRA's original design)?
4. **Are the 8δ delta-target + GEX strike adjuster producing good strikes** when GEX works? Unvalidated (today's was mis-placed by the fetch bug).
5. **Is 0DTE PM-settlement P&L booked correctly on IBKR?** The `+885.6` pre-settlement pnl_history artifact needs confirming against the final settled number.
6. **The L-C2 backstop + marketable-close path have not been live-exercised** — we don't yet know they fire cleanly in practice.

---

## Data preservation
Full record of both days (3 DBs + metrics + state + journal + all 6 logs) archived to `gs://calypso-backups/buggy_days_archive_20260610T221618Z.tar.gz`. The 08:00 ET Jun-11 wipe (which removes Jun 9/10 from the live DB/metrics/logs) re-archives the finalized record to GCS first and **aborts if that archive fails**. Journal prose survives in git history.

## Fix commits
`ecb5aef` (GEX reliability + stale-greeks guard) · `9cdfb51` (SELL-floor / GUARD-INVERT) · `c0281d9` (L-C2 backstop) · `5b500c7` (L-C2b spread_value clamp).
