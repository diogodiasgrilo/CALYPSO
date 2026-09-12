# CALYPSO — Next Steps (living doc)

> **This is the single, always-current "what's left" tracker — read §A first.** Update it whenever work
> lands or a new item appears. It complements (does not replace)
> [`docs/migration/PROJECT_STATUS.md`](migration/PROJECT_STATUS.md) (project-wide state) and the per-effort
> design docs.
>
> **Last updated: 2026-09-12 (Sat 03:05 ET).** Second deploy executed 02:49–02:52 ET. IBKR's
> brokerage session is DOWN (weekend maintenance) — see §A P0-bis. §A–§D below are current. **§0–§10 are the older backlog (2026-07-14 /
> 07-24 era)** — much of it is done or superseded; **verify against the code before acting on anything
> there.** Real live items still live in §5 (entry-schedule lock, E calendar-stop analyzer) and §6
> (Brandon fill-quality confirmations), which is why those sections are kept rather than deleted.

---

# §A. DO NEXT — by priority (2026-09-10)

### P0 — ✅ DEPLOYED 2026-09-11, 03:25–03:35 ET

All commits are live. VM at `ea0bd8c`, broker restarted first and healthy (connected/authenticated/not
competing), all 8 strategy units clean with zero errors, unit files installed + `daemon-reload`, timers
rescheduled (HERMES Fri 23:00 ET, HOMER Fri 23:30 ET), and `ENTRY-PRICING: deliberate rung pricing
ENABLED` confirmed in B's log. Account was flat (13 position rows, all qty 0 — expired 09-10 contracts).
Kept for the record, since the sequence is the reusable part:

1. **Wait for settlement to complete.** Measured 21:45–22:37 ET. NEVER restart with settlement pending
   (the stale-SPX bug produced a phantom −$6,036).
2. Confirm the account is flat via direct broker RPC (`get_positions`), not the state file.
3. **Restart `calypso-broker` FIRST**, verify `/health`. Three commits touch `shared/ib_client.py`, and the
   strategies forward new kwargs blindly over `/rpc` — an un-restarted broker on old code raises
   `TypeError` and blinds the bot (the 2026-06-08 modularity deploy bug).
4. Restart the strategies. **Verify `deliberate_rung_pricing: true` actually landed in B's VM config** —
   skip-worktree does NOT stop a fast-forward pull, and it does not guarantee one either.
5. **`cp` the changed unit files to `/etc/systemd/system/` + `daemon-reload`.** A `git pull` does NOT
   install unit files. After tonight's pull these will differ: `hermes.timer`, `homer.timer`,
   `hydra_variant_b.service`. Then `systemctl restart hermes.timer homer.timer`.
6. ~~**Probe `get_day_executions(days=7)`**~~ — **DONE 2026-09-11, and the accountId theory is REFUTED.**
   After the fix was deployed and the broker restarted, the endpoint still returns **ZERO** executions over
   a 7-day window containing ~24 real legs. The code-read was sound (we genuinely were not sending
   `accountId`, and 11 other call sites do), but it was not the cause. The fix is kept — it is correct and
   harmless — and the **paper-account-limitation theory now stands with one variable eliminated**.
   Consequence unchanged and already handled: `get_closed_position_price` returns None on every lookup, which
   the L-M3 guard, the open/close filter and the MKT-033 quote-estimate fallback all account for. **Do not
   re-attempt this without a new hypothesis** — next candidates are a different preflight requirement, or
   PortfolioAnalyst's transactions endpoint as an alternative source.
7. **Read today's `BROKER-RECONCILE` log line** and settle whether IBKR's `raw_ledger.USD.realizedpnl` is
   gross or net of commission. Until that is known the check logs and does not alert.

### P0-bis — SATURDAY 2026-09-12 STATE, and the two Monday gates

**Second deploy done 02:49–02:52 ET.** Broker first (broker_service OrderRequest coercion,
data_recorder v17, ib_client), then all 7 strategies. Zero errors, `ENV-ASSERT ok`.

**⚠️ IBKR's brokerage session is DOWN.** `ssodh/init` → **410 Gone** since 01:02 ET, 4 consecutive
re-auth failures. A broker restart came up clean but still `authenticated: false`. Data reads work
(positions + balance return 200); only the brokerage session is down. `410 Gone` beginning 01:02 on
a Saturday is consistent with **IBKR weekend maintenance**, not a fault here.

Consequence: the strategies are parked in a startup wait-loop (`broker not holding a session yet`,
retry ~15s). Correct, safe behaviour — they will not trade blind. **But `DataRecorder` never
initialises, so the v17 migration has NOT run** (schema still v16).

**TWO GATES BEFORE MONDAY 09:30:**
- [ ] **Session recovered?** Re-check Sunday evening. If still `410 Gone`, this becomes a real
      problem to solve before the open, not a wait-and-see.
- [ ] **v17 migration ran?** `PRAGMA table_info(trade_entries)` must show four
      `*_mid_at_decision` columns. It fires on `DataRecorder` init, which needs the session.

**Sequencing lesson, recorded so it is not repeated:** the strategies were restarted before the
broker had a confirmed session, which parked them in a wait-loop. Harmless on a Saturday. The
correct order is **broker → confirm `/health` says connected → strategies**.

### P1 — DONE Fri 2026-09-11: the first passively-priced session

**Result: B net $883.40, 3 entries, 0 stops, zero errors.** All three condors expired worthless
(shorts 28–78pt OTM). SPX range was only 25.8pt — a quiet tape.

**Passive pricing, hand-reconstructed from logs (v17 was not yet recording):**

| Entry | legs filled on attempt 1 | leg-in | vs legacy |
|---|---|---|---|
| #1 09:49 | 2 of 4 | 193s | −$35 |
| #2 10:17 | 3 of 4 | 100s | +$70 |
| #5 11:47 | **4 of 4** | 80s | +$105 |
| | | | **+$139** |

Leg-in fell 193→100→80s against a 52.9s median. **Read it lightly**: a 25-point range is the EASY
case for resting orders, and both misses on #1 came during the morning's only real drift. This is a
best-case data point, not a representative one. Tomorrow is the first day it is MEASURED rather
than reconstructed.

Also: 4 of 7 slots were skipped (credit gate), consistent with the known gating rate. Every skip
now records its strikes.

### P2 — remaining non-go-live work (small)

- [x] ~~**Stale-comment sweep + unit-file drift**~~ — **DONE 2026-09-11** (`ea0bd8c` + unit install).
      All four corrected after VERIFYING each was wrong (the account really is USD — `raw_ledger["BASE"]`
      is identical to `raw_ledger["USD"]`). `entry-window-watch.timer` reinstalled, so its 14:05 E6 check
      now actually runs. Original list kept below for the reasoning:
  - `base_strategy.py` MKT-048's "a mid-limit buy fills at ≤ mid, so long_fill ≈ long_mid" — falsified
    29/34 on legacy pricing, but becomes *conditionally true* for B once deliberate rung pricing is on.
    Needs precision, not deletion.
  - `ib_client.py` "EUR for us" — the account is USD (`raw_ledger.USD`, netliq in USD).
  - `deploy/hydra*.service` "root cause unconfirmed" notes about the shutdown hang — it WAS root-caused
    (CPython finalization + grpc-core) and fixed 2026-09-05 in `3986cdf`.
  - **`/etc/systemd/system/entry-window-watch.timer` is OLDER than the repo copy** — pre-existing drift,
    not caused by today's work. It is missing the 14:05 E6 check that was added but never installed, so
    that watch has never run. Low impact (E6 is suppressed on B) but fix it during a deploy.
- [ ] **GEX veto EV — DATA-BLOCKED, not merely "optional". Re-scoped 2026-09-11 against the real tables.**

      The 2026-09-10 audit framed this as a ~2-3h read-only analysis over "83 aborts" / "43 vetoes vs 38
      placed". **Checking the actual database does not support that scoping:**

      ```
      skipped_entries — GEX accel-zone vetoes:   108 entries   (95 + 13, two reason strings)
        ...of which have outcome data recorded:    0
      gex_decisions (schema-v16 telemetry, since 2026-09-05):  40 rows — 37 KEEP, 3 SKIP
      ```

      Two blockers the audit did not surface:
      1. **The new telemetry is far too sparse.** Three SKIP events across ~5 sessions decides nothing.
      2. **The historical record has NO outcomes.** 108 entries were vetoed and not one has a recorded
         "would it have won?" — because that is exactly what
         `DataRecorder.update_skipped_entry_backtest` would write, and it has **zero callers repo-wide**
         (documented in `ea0bd8c`). **These are the same finding, not two.**

      So answering this properly means EITHER waiting months for `gex_decisions` to accumulate, OR
      reconstructing outcomes for the 108 historical vetoes from `market_ticks` — real work, not a query.

      **UNBLOCKED GOING FORWARD, 2026-09-11.** Root cause of the blockage found and fixed: the
      `theoretical_short_call/long_call/short_put/long_put` columns have existed since schema v8 and
      **nothing ever populated them** — 95 live-era GEX vetoes, 0 with strikes. It was never an analysis
      problem; the inputs were never written down. `_record_skipped_entry` now takes the proposed entry
      and records the strikes, wired at both clean-skip sites that hold one (require-both-sides — which
      is the GEX veto — and the degraded-data abort). A vetoed side records None, not 0: the adjuster
      zeroes the side it drops, and a 0 would pass a `strike > 0` filter as if present.

      **REMAINING, in order:** (a) wire `DataRecorder.update_skipped_entry_backtest` at settlement to
      compute `would_have_stopped` / `theoretical_pnl` from the day's `market_ticks` range — it still has
      zero callers; (b) THEN the EV is a query rather than a project. The historical 95 stay
      unmeasurable — their strikes were never recorded and cannot be recovered.

      **Still recommended: do not block on it.** Existing evidence (vetoed shorts got breached; placed
      ones did not) already favours KEEPING the gate, and nothing downstream depends on the number.

### P3 — the real-money combo track (the actual next phase)

Strictly ordered — each step bakes in decisions the later ones depend on.

1. [~] **Settle the combo side-field semantics (C4).** ✅ TOOL BUILT 2026-09-11 (`edb0cb2`):
       `scripts/probe_combo_whatif.py`, read-only, runs through the broker (`what_if_order` now
       allowlisted — a direct IBClient would evict the broker's session). **RUN IT DURING RTH.**
       Read-only `what_if_order` preview on the exact BAG `OrderRequest` that `place_iron_condor` builds;
       snapshot the legs + BAG first; read `initial.change` and `amount`. Short-IC ⇒ `change` ≈ width×100×qty and `amount` is a credit. **Treat an empty block as
       INCONCLUSIVE, never a pass.** Do NOT use the "place a 1-contract live combo then cancel" fallback —
       the repo's own probe records paper combos sticking in phantom `PendingSubmit` with `OrderID doesn't
       exist` on cancel.
2. [~] **Routing / atomicity — RESEARCHED 2026-09-11.** Written up as §0-bis of
       `COMBO_ENTRY_LIVE_CUTOVER_PLAN.md`. **Cannot be CLOSED without a live account.**

       CONFIRMED from IBKR's own docs (two sources): *"For combination orders that are SmartRouted, each
       leg may be executed separately to ensure best execution."* Their vocabulary is **guaranteed**
       (direct-to-exchange) vs **non-guaranteed** (SMART), set via `SmartComboRoutingParams`.

       THREE FINDINGS, worst first:
       1. **ibind 0.1.23 cannot express the choice at all** — 34 `OrderRequest` fields, no
          `SmartComboRoutingParams`, no `non_guaranteed`, no `leg_in_prio`. Only `listing_exchange`.
       2. **Nothing in the repo sets any routing** — `listing_exchange` is never assigned (every
          `exchange=` is contract QUALIFICATION), and the conidex builders emit the bare `28812380;;;`
          template with no `@CBOE`.
       3. The plan's §6 "inversion impossible by construction" **contradicted its own §2**. §2 was right;
          §6 is corrected. The plan was MORE careful than the 09-10 audit implied.

       UNRESOLVABLE BY READING: whether CP API defaults a conidex combo to SMART; whether a USD
       index-option combo needs `@CBOE`; whether a 4-leg SPX combo is permitted. All need the live
       account — paper simulates combo order types and cannot validate fills.

       CONSEQUENCE: **the partial-fill reconcile is the PRIMARY defence, not a backstop**, until a
       routing is chosen AND verified atomic live.
3. [ ] **Partial-fill-by-quantity reconcile — RE-SCOPED 2026-09-11. Half done; the rest belongs WITH
       step 6, not before it.**

       ✅ **Done (`fac138d`+):** both stop-sizing sites now read `entry.contracts` rather than
       `self.contracts_per_entry`. At 10c/5pt/0.40 a 6-of-10 fill now triggers at $1,200 = 40% of the
       real $3,000 max loss, instead of $2,000 = 67% of it.

       ⚠️ **The audit's framing was too alarming for TODAY.** Reading the code shows the leg ladder
       ALREADY handles partial fills: if a leg cannot complete after all rungs, **ORDER-010 flattens the
       partial** and reports the leg failed (→ entry unwind). A leg is all-or-nothing by construction, so
       `entry.contracts` cannot drift from a ladder partial. The "6-of-10 survives" scenario needs a
       **combo/BAG** order, and `place_and_wait_for_fill` is keyword-only `conid: int` with no BAG
       support — the path does not exist yet.

       ❌ **Still to do, WITH step 6:** when the BAG path is written, it must return the broker's
       `filledQuantity`, the entry must adopt it into `entry.contracts`, and it must fail CLOSED — if
       filled != requested and the residual cannot be cancelled, CRITICAL alert and do not enter
       monitoring at the requested size. ibind 0.1.23 has no AON/FOK field to prevent it. Writing that
       reconcile now would be building against an interface nobody has designed.
4. [x] ~~**Make the environment a real switch.**~~ **DONE 2026-09-11 (`fac138d`).**
       `resolve_environment()` reads `$CALYPSO_IBKR_ENV` (defaults paper; an unrecognised value RAISES
       rather than falling back), both call sites wired, and — the half that matters —
       `_assert_account_matches_env()` cross-checks the DISCOVERED account code inside
       `_discover_account_id`. Asymmetric by design: declared-paper-but-LIVE **raises** (real money under
       a simulation assumption); declared-live-but-paper only warns (harmless, and refusing would brick a
       go-live over a mis-set variable). The account prefix was READ from a live position row
       (`DUR`+6), not assumed, and is pinned by a test so a future tidy-up cannot fail every restart.
       Behaviour unchanged today — the var is unset, so both sites resolve to "paper" as the literals did.
5. [x] ~~**Add combo prompts to `DEFAULT_ORDER_ANSWERS`**~~ — **ALREADY DONE, verified 2026-09-11.**
       ibind 0.1.23 defines 14 `QuestionType`s; we map all 14 (plus a string key for the size-limit
       prompt). **Zero unmapped**, so `find_answer` cannot raise "Too many questions" on a combo place.
       The map is already combo-aware on purpose — `TICK_SIZE_LIMIT` cites "CBOE combo $0.05 rounding"
       and `TRIGGER_AND_FILL` cites "combos at mid". The audit listed this as ~30 min of outstanding
       work; it was not outstanding.
6. [ ] **Then the runtime path itself.** `place_iron_condor` / `place_vertical_spread` / the conidex builders
       have **zero callers under `bots/`**, are absent from `broker_service.ALLOWED_METHODS`, and
       `place_and_wait_for_fill` is keyword-only `conid: int` with no BAG support.

> **Do NOT wait for combos to fix the fill leak.** Combos are only validatable on a live account by the
> plan's own probe evidence; the leak is running today and the rung-pricing work is already deployed.

---

# §A-bis. WHERE B ACTUALLY STANDS (measured 2026-09-11) + THE TOOLS

**These are the go-live decision inputs.** Every figure is from
`scripts/variant_performance.py` over B's live-paper era (2026-07-24 → 09-10).

```
35 sessions   24 traded · 9 gated · 1 no-attempt (FOMC) · 1 closed (holiday)
  NET $6,492.25      per SESSION $185.49   per TRADED day $270.51
ON TRADED DAYS (n=24):
  win rate 17/24 = 71%      avg win $833 / avg loss -$1,096
  payoff ratio 0.76  (losses BIGGER than wins — premium-selling shape)
  Sharpe (ann.) 3.99 — on 24 days, PROVISIONAL
  max drawdown -$1,960.70   71 entries / 8 stops
```

**Read the shape, not the headline.** It wins often and loses bigger. That works
while 71% × $833 beats 29% × $1,096, and it is the profile that breaks worst in a
tail event. 24 traded days has not seen a bad day.

⚠️ **A "49% win rate" figure was produced on 2026-09-11 by an ad-hoc query and is
WRONG.** It counted the 11 no-trade days as non-wins. Use the tool, which cannot
make that error (mutation-tested).

**THREE NUMBERS FRAME THE GO-LIVE DECISION, none of them settled:**

| Unknown | Size | Resolves |
|---|---|---|
| Entry execution drag | **~$79/day vs ~$185/day net — ~30% of the edge** | today's passive-pricing result |
| Is the gating stack right? | **~$2,434 = ~38% of traded-day P&L** rides on 9 gated days | ~1 week of recorded vetoes |
| Is 71% / 0.76 stable? | unknown | more sessions |

## The tools (all read-only; run on the VM as `calypso`)

| Tool | Answers | When |
|---|---|---|
| `scripts/variant_performance.py` | the record, traded days separated from gated | any time |
| `scripts/analyze_fill_quality.py` | paid-vs-mid per leg + leg-in duration. Baseline **$1,905 / $79.38 a day / $26.83 an entry**, longs = 91% | after a close; `--compare 2026-09-11` for the pricing change |
| `scripts/analyze_skipped_entry_outcomes.py` | would the vetoed entries have been breached | needs ≥20 recorded vetoes (~1 week from 2026-09-11) |
| `scripts/probe_combo_whatif.py` | is a 4-leg combo accepted; does `@CBOE` change margin | **during RTH only** — needs live quotes |
| `scripts/verify_pnl_vs_account.py` | does the ACCOUNT agree with our claimed P&L (cumulative) | any time; needs ≥2 days of retained logs |

---

# §B. DECIDED — do not re-litigate

- **Entry slots: leave them alone.** A permutation test on B's live era puts the ENTIRE per-slot effect at
  **p=0.569**. 11:15's whole −$875 was ONE −$1,750 stop on 08-28; excluding it, those 8 entries average
  **+$109**. Detecting a $200/entry difference at 80% power needs ~72 entries per slot; there are 8–9.
  **STANDING RULE: no slot is cut or restored again until it has ≥70 live-era hedge-free entries.**
- **A2 %-of-width stop: keep B at 0.40.** Validated on 63,807 spread snapshots over 58 C sessions; beats
  credit+buffer 5-for-5 (+$2,450, sign test p=0.031). Costs $0 — B already runs it.
- **GEX sign convention: do NOT flip.** Direction confirmed but the cost is ~−$535, and the two sampled
  sessions had zero stop-losses, so the sample can measure the gate's cost and never its benefit. The
  windowed normalization is measured inert (0/217 predicates changed) and must never be proposed as a
  remedy for over-vetoing.
- **`TimeoutStopSec`: do NOT lower it.** The shutdown hang was fixed 2026-09-05; post-fix shutdowns still
  legitimately reach 71s during a 7-contract entry. A 25s timeout would SIGKILL the live seat mid-order.
- **Brandon overlay hedges: OFF on B** since 2026-09-04. Hedge debits ($1,925–$2,240) exceeded the IC-side
  loss they defended (~$1,400, already bounded by the A2 stop). `enabled` stays true on purpose to preserve
  WATCH telemetry.
- **A dry-run variant CANNOT test anything in the order path.** `_initiate_entry` routes to
  `_simulate_entry` and `_place_option_order` hard-gates on `dry_run` (SAFETY-DRY-01). **B is the only
  variant that can answer any entry-execution question.** Remember this before proposing a "shadow on C".

---

# §C. DONE — 2026-09-10 (ten commits)

**Deployed same day:** `141d267` (07:34) independent P&L check vs IBKR's own ledger — the first
non-circular reconciliation in the codebase; `56bb096` (09:19) L-M3 double-book guard + flag persistence
+ settlement exclusion + the 7-slot label.

**Pending tonight:** `52d2b5c` accountId (the trades endpoint was never dead) · `f36eda9` close-price
open/close filter · `2e3dbf6` rung-1 pricing policy (default off) · `b235df5` enable it on B · `14205f2`
unwind shorts-first + 12:45 in the slot analyzer + agent timers past settlement · `2d912ea` MKT-033
estimates instead of deleting P&L + stop pinning every IbkrClient · `0fbc22d` agents refuse to run on an
unsettled day · `8e48235` analyzers stop pooling incomparable eras.

Full detail, including the reasoning and the mutation results, is in `bots/hydra/__init__.py` version
history. Highlights worth carrying forward:

- **The entry fill leak was ~38% of B's net** ($1,575 of a $1,818 gap across 65 entries, ~$79/day) and was
  not a bias but an **arithmetic accident**: buys `ceil`'d onto the ask 100% of the time; sells were
  decided by float noise and crossed on 67% of one-tick books.
- **`/iserver/account/trades` was never dead** — we never sent `accountId`. Eleven other call sites passed
  it; the two `trades()` calls did not.
- **HERMES/HOMER ran ~3 hours before settlement, every day** — so the daily analysis and the journal
  committed to git were built on unsettled numbers.

---

# §D. OPEN QUESTIONS (carry these; do not paper over)

- Is IBKR's `raw_ledger.USD.realizedpnl` gross or net of commission, and when does it reset? Unverified —
  the reconcile reports drift against BOTH until a real trading day settles it.
- Does the `accountId` fix actually make executions appear? Proven by code-read only. **If it does, three
  currently-masked defects activate at once** — which is why the L-M3 guard and the close-price filter had
  to ship first.
- B's 2026-07-24 → 08-14 window has no log coverage (journald ~24d, `bot.log` ~7d). A silently-unbooked
  L-M3 side leaves NO row in `trade_stops`, so a clean DB over that window is not proof one didn't occur.
- Realistically recoverable share of the fill leak is an ESTIMATE ($20–34/day at 7c), not a measurement —
  the passive-fill evidence is n=11.
- Variant F is 0-for-10; unknowable whether that is the strategy or a mis-calibrated boundary, because
  nothing records the session's max excursion toward the EM boundary.
- HOMER's actual DB-mode fallback chain has never been re-verified since the 2026-07-17 migration;
  CLAUDE.md still documents the legacy Sheets-mode chain.

---

<!-- ────────────────────────────────────────────────────────────────────────
     Everything below is the 2026-07-14 / 07-24-era backlog. Much is DONE or
     superseded by §A–§C. Verify against the code before acting on it.
     Still-live items: §5 (entry-schedule lock, E calendar-stop analyzer) and
     §6 (Brandon fill-quality confirmations).
     ──────────────────────────────────────────────────────────────────────── -->

## 0. Current snapshot (2026-07-24 — STALE, see §A above)

- **Branch:** **MERGED** into `hydra-ibkr-standalone` (2026-06-16, at `e5688f0`; latest `741fc66`). The merge
  also reconciled a HOMER auto-commit that had regressed the mainline (see [[homer-vm-autocommit-gotcha]] / §6).
- **Built + tested (full suite ~1686 passed / 15 skipped; frontend `tsc -b` + `vite build` clean):**
  Strategy taxonomy → bot wiring → `CalendarStrategyBase` (D byte-identical) → **Strategy E (SPY double
  calendar, dry-run-locked)** → dashboard backend `/api/strategies` → dashboard frontend (picker + group
  tabs + **full-parity non-primary 0DTE view** + EOD auto-update) → comms (group-scoped `/compare` + display names).
- **Deployed to the VM:** the full dashboard (picker + group tabs + full-parity 0DTE view) AND **Strategy E
  running dry-run** (`hydra_variant_e` active; SPY resolves @~750.72; recorder DB created; available in the
  picker). `calypso-broker` was restarted for the new `shared/ib_client`; session healthy. VM tree clean.
- **Still dry-run-LOCKED / NOT live:** E and D place **no real orders**. Real-money/live-paper go-live for E/D
  remains gated (§2b/§3). **A/B/C status changed 2026-07-24:** the live-paper seat swapped from C to B
  (`scripts/flip_bc_swap.sh`, per the researched+executed [`BC_SWAP_PLAN.md`](migration/BC_SWAP_PLAN.md)) — **B is
  now live-paper** (7 contracts, 7-slot grid, dashboard PRIMARY), **C is now dry-run-shadow** (7 contracts),
  **A is unchanged** (dry-run-shadow, 1 contract). See CLAUDE.md's Variant Comparison table for the current
  reference and `RUNBOOKS.md` RB-9 for the swap/rollback procedure.
- **🚀 Go-live — START HERE:** [`docs/GO_LIVE_MASTER.md`](GO_LIVE_MASTER.md) — the umbrella (two levels, the
  A/B/C/D/E status matrix, the tests+pass-criteria appendix, the flip procedure). Its own build/refresh TODO is §10.

---

## 1. Finish this effort (Strategy-E + grouping)

- [ ] **Documentation consolidation** (from the 2026-06-16 doc audit — 3 agents). Fix staleness/contradictions:
  - [ ] **CLAUDE.md**: rewrite the *Variant Comparison* section around the 5-variant / 2-group taxonomy; add D
        + E to the structure/deploy/service-name/state-file lists; update the *Adding a new variant* recipe to
        start with the taxonomy + registry rows; document group-scoped `/compare`, the strategy picker, group
        tabs, EOD auto-update, and the `/api/strategies` + `/api/dc` endpoints; add doc-index rows for
        STRATEGY_GROUPING_REDESIGN + the D go-live docs.
  - [ ] **PROJECT_STATUS.md**: add a state section for the Strategy-D full build + the Strategy-E/grouping work
        (it currently stops at 2026-06-02 and is silent on both); refresh header + pointer index.
  - [ ] **Code comments/docstrings**: genericize `calendar_strategy_base.py` (its docstrings + `[DCTM-*]` log
        tags say "D" but it's shared by D **and** E); fix `double_calendar_strategy.py.__init__` (says "NOT
        implemented / stubbed" while the rest of the file says "IS implemented"); fix `registry.py` "SCAFFOLD /
        stubbed" comments for D/E; generalize `dc_status.py` ("D-native" → calendar-group); add `/compare` +
        `/calendars` to the `telegram_commands.py` header command list.
  - [ ] **STRATEGY_GROUPING_REDESIGN.md**: flip the header off "Status: DESIGN / no code written yet" →
        IMPLEMENTED (commits f9e7d2a…); resolve the §6 OPEN DECISIONS the code already answered.
  - [ ] **NEW_STRATEGY_PLAYBOOK.md**: the audit-log row records the *wrong* E identity (Theta-Profits/Ahuja);
        the shipped E is the **SPY/OptionsKit** double calendar — correct it; note `CalendarStrategyBase` is no
        longer hypothetical (it exists).
  - [ ] **D go-live docs** (`D_GOLIVE_*`): note that D's shared `_dc_*` methods now live in
        `bots/hydra/calendar_strategy_base.py` (their `double_calendar_strategy.py:NNN` line refs are stale
        post-lift).
- [ ] **Frontend visual verification** — eyeball the deployed dashboard (§4 checklist).
- [ ] **Code refinements** (safe, not go-live-gated):
  - [ ] Multi-contract ladder: `_spy_dc_partial_close` must scale `entry.net_debit` down on a partial close
        (irrelevant at the 1-contract default; required before multi-contract sizing). Verify `CalendarEntry`
        contract-scaling semantics first.
  - [ ] `__init__.py` version-history entry — **DONE** (capstone of the comms commit).
- [ ] **Merge** the feature branch into `hydra-ibkr-standalone` via PR (full suite + frontend build green).
      → reconciles the VM dashboard overlay (§4) on the next `git pull`.

---

## 2. Deploy Strategy E (SPY double calendar)

E is **dry-run-LOCKED** (un-flippable constructor lock). Two stages:

### 2a. Run E as a dry-run variant on the VM (safe — no real orders)
- [ ] On the VM (as `calypso`): create `bots/hydra/config/config_variant_e.json` (the committed file is the
      template; configs are gitignored/`skip-worktree` on the VM). **Verify `underlying_symbol: "SPY"`,
      `trading_class: "SPY"`, `exchange`, `strike_increment: 1`** — a stale value silently breaks chain/quote
      resolution.
- [ ] Probe SPY data on the VM (read-only) **before** relying on it: confirm `qualify_contract("SPY",
      sec_type="STK")` + the SPY option chain + quotes/greeks resolve through `calypso-broker`. SPY is the
      probe control instrument, so this should work — but verify the exact `exchange` (NYSE vs ARCA vs SMART).
- [ ] Install `deploy/hydra_variant_e.service` (`HYDRA_VARIANT_ID=e`, broker-proxy mode), `daemon-reload`.
- [ ] ⚠️ If `shared/` changed since the broker last restarted, **restart `calypso-broker` FIRST** (it holds
      the SPY conid pin in `ib_client.py`) — see CLAUDE.md deploy workflow.
- [ ] `systemctl start hydra_variant_e`; watch logs; confirm the dry-run lifecycle fires (entry sim →
      laddered TP → time-exit) with no real orders.
- [ ] Add `variant_e_*` paths to the VM's `dashboard/backend/config.py` (committed on the branch) so the
      dashboard picker shows E with data.

### 2b. Take E LIVE (real paper orders) — GO-LIVE GATES (multi-week, deliberate)
- [ ] **SPY American assignment + dividend handling** — SPY options are physically settled; the short near
      leg can be early-assigned if ITM (esp. around ex-div for calls). The time-exit ("never hold to expiry")
      is the first defense; a real-order path needs explicit assignment detection + cover-with-long, and
      `_read_open_positions` must include equity (STK) positions (today it filters to OPT).
- [ ] **True IV-rank entry gate** — replace the VIX-threshold proxy with a real IV-history source (the video
      enters when IV < ~1yr median).
- [ ] **Coexistence MUST-FIXes** (shared with D — land once in `CalendarStrategyBase`): scope C's STATE-004
      overnight guard + orphan sweep to per-variant conids; per-variant buying-power budget.
      **NOT the current blocker (corrected 2026-09-06)** — E, like D, has zero order-placement code, so these
      gates cannot bind yet. See the same note under Strategy D below; build the execution path first.
- [ ] **Multi-contract ladder fix** (§1) before sizing > 1 contract.
- [ ] Remove the dry-run constructor lock only via a documented manual flip + a fresh same-ET-day paper
      smoke PASS — never an auto-flip. Write E's go-live runbook (model on `D_GOLIVE_RUNBOOK.md`).

---

## 3. Deploy Strategy D (DC Time Machine)

D is **dry-run-LOCKED** and the go-live audit verdict is **NO-GO** (see
[`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md)). D already runs as a dry-run variant on
the VM (per its memory/journal). To take D live:
- [ ] Follow [`D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md) in full (DG-1..DG-11 gate).
- [ ] **BUILD THE REAL-ORDER EXECUTION PATH — this is THE blocker, and it is first.** D is *fully* simulated:
      `grep -c "place_order\|place_and_wait_for_fill"` returns **0** across `double_calendar_strategy.py`,
      `spy_double_calendar_strategy.py` AND `calendar_strategy_base.py`. The only order-placement site in the
      entire bot tree is `strategy.py:2291`, which no calendar path reaches. Both constructors additionally
      hard-refuse `dry_run=False` before any broker I/O. The work is the six `C-exec1..6` items in
      [`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md) §C (two CRITICAL: the fill model
      C-exec4 and the real transform C-exec5), whose own verdict is *"NO-GO today. This is a build, not a flip."*
- [ ] Ship the **coexistence MUST-FIXes** (shared with E — STATE-004 / orphan sweep scoping + per-variant BP).
      **CORRECTED 2026-09-06 — these are NOT what blocks D/E today, and should NOT be built first.** The
      framing "STATE-004 is the live blocker keeping D/E dry-run-locked" propagated through several docs (and
      into both constructors' own error messages) and is false: scoping a halt that guards against unexpected
      *open positions* is a guard in front of a door that does not exist, because D/E cannot open a position
      at all. These are genuine **go-live gates that bind only once execution exists** — sequence them after
      the C-exec work, not before it. Two design attempts at the scoping were adversarially refuted
      (2026-09-06) for reachable fail-opens; do not restart that work until it is actually on the critical path.
      **Do not confuse this with the STATE-004 *restart-gap* backstop, which DID ship 2026-09-06** (pre-market
      hook + persisted `overnight_check_date`; see `bots/hydra/__init__.py`). That one changed *when* the check
      runs; this one is about *what conids it scopes to*. They are independent.
- [ ] Validate the edge (backtest/soak) and confirm the risk-free transform invariant survives real fills.
- [x] **Edge-read instrument BUILT (2026-06-23).** `scripts/analyze_calendar_edge.py` (logic in the unit-tested
      `bots/hydra/dc_edge.py`, 2 adversarial audit passes) answers the MVL-D "V1 — edge sanity" question from
      D/E's dry-run record: commission-net, transform-segmented (transformed = excluded), Student-t CI,
      sample-size-honest. **The MVL-D build is now data-gated, not a guess.** Current verdict: **INSUFFICIENT_DATA
      (D n=1, E n=0)** — keep recording; re-run as outcomes accrue. Decision rule: only EDGE_POSITIVE on the
      trustworthy (non-transformed) segment justifies SCOPING Micro-MVL-D; EDGE_NEGATIVE/INCONCLUSIVE → don't build.
- [ ] Consider the **MVL-D** first phase (drop the transformer; defined-risk debit calendar + stop) per
      [`D_MVL_PHASE1_PLAN.md`](migration/D_MVL_PHASE1_PLAN.md) — **only if** the edge reader crosses to
      EDGE_POSITIVE (see §7; E may answer the same question first).
- [ ] NOTE: D's go-live docs predate the `CalendarStrategyBase` extraction — their file:line refs to
      `double_calendar_strategy.py` are stale for the shared `_dc_*` methods (now in the base). Update them (§1).

---

## 4. Dashboard deploy (done — reconcile on merge)

- **State:** the VM dashboard runs the feature-branch backend (`dashboard/backend` + `shared/strategy_taxonomy.py`
  overlaid via `git checkout FETCH_HEAD -- …`) + the new frontend `dist`. Bot code is untouched on
  `hydra-ibkr-standalone` HEAD. A `dist.bak.predeploy.*` backup exists.
- [ ] **Visual verification checklist:** picker switches the main view + the whole header follows it; selecting
      D shows the debit calendar layout (no credit/buffer, no SPX/VIX chips); group tabs (`ic_0dte` A/B/C panels,
      `calendar_multiday` debit comparison); legacy `/comparison` + `/dc` redirect; no cross-strategy stop-toasts;
      cumulative + "Last Trading Day"/"Week in Review" cards refresh at ~4 PM ET without reload.
- [ ] **Reconcile on merge:** after the branch merges to `hydra-ibkr-standalone`, on the VM
      `git checkout -- dashboard/backend shared/strategy_taxonomy.py` (discard the overlay) then `git pull`
      (brings the same code via the merge), clear cache, restart dashboard. The overlay and the merged code are
      identical, so this is a no-op refresh that restores a clean git tree.

---

## 5. Backlog / nice-to-haves (from the audit + design)

- [ ] **🎯 Entry-Schedule Lock review — RETARGETED after the 2026-07-24 B↔C live-seat swap.** The original
      ~mid-August 2026 target and its premise ("C accumulates the real-fill sample") no longer hold: **C's
      real-fill dataset is now FROZEN** at its 2026-05-05→2026-07-24 history (~29-30 live-paper days) — still
      usable for historical analysis, but it stops growing. **B is the live-paper source of real fills going
      forward, starting from ZERO real-fill history on 2026-07-24** (B's prior ~14+ days were dry-run
      simulation, not real fills — see [[bc_live_seat_swap]] / [[per_slot_edge_and_realized_pnl]] memories).
      Reaching a comparable sample on B will take a similar order of elapsed time to what it took C to reach
      ~22 days by 07-14 (~10 weeks) — **do not trust a calendar-guessed date here; recompute the actual target
      once B's real trading-day count is known** (`bots/hydra/slot_edge.py --variant b` reports its own sample
      size). At that review: (a) re-run `bots/hydra/slot_edge.py` against **B's** DB (C's frozen dataset stays
      available as a historical comparison, not a moving target); (b) run the real-fill
      credit-vs-(commission+measured-slippage) filter per **B** slot (B now captures fills+mids at 100% since
      it's the live variant); (c) LOCK the go-live entry schedule (which slots / how many / start size) on
      **economics + tail-risk + regime-robustness**, NOT p-values (per-slot statistical significance takes
      months–years given ~$884 per-entry std — do not wait for it). The old "B's extra slots need separate
      proof on a separate paper account" contingency is now **MOOT** — B's 7-slot grid IS the live account's
      real-fill data; the schedule-lock decision is now about which of B's 7 slots to keep, not whether to add
      B's slots to C. This is the last strategy-side gate before the operational go-live gates (§2b /
      LIVE_READINESS_CHECKLIST / RB-9). Data hygiene note (applies to C's frozen historical dataset if it's used as
      a comparison, NOT to B's forward real-fill window which starts 2026-07-24, well after these dates):
      exclude 07-06 + 07-07 from per-entry analysis (settlement-bug contaminated: B 07-07 gross 392 vs entries
      +2925; C 07-06 gross 105 vs entries −2224), or correct them; keep 07-02/07-13 (real down-days).

- [x] **A2-SHADOW %-of-width stop decision — RESOLVED 2026-07-14: do NOT flip C.** Ran `bots/hydra/stop_shadow.py`
      over both live variants' full history (2026-05-05 → 07-14). A tighter %-of-width stop is **net-negative at
      every threshold** — premature stops of recoverers (whipsaw) dominate the tail-capping of real disasters. C
      (credit+buffer): switching to %-width costs −$2,340 (25%) to +$545–705 (50%, only 1–2 fires = noise). B
      (already 40%-of-width): naive %-width net-negative at every threshold, and shows B's settlement-hold guard
      earns its keep. Keep C's credit+buffer stop. Re-run the replay periodically; revisit only if the sign flips.
- [ ] **E calendar-stop analyzer (PARKED — blocked on data).** To decide whether E (SPY double calendar) needs a
      max-loss floor at go-live, build a `stop_shadow`-style replay over E's `dc_calendar_snapshots.unrealized_pnl`
      (a hypothetical −X% stop). No new live logging needed (snapshots already record per-tick unrealized P&L).
      Blocked: E has **0 completed trades** (`dc_outcomes=0`, 1 open calendar) and pre-2026-07-11 snapshots are
      arb-contaminated (worst −108% of debit = pre-`a1678ce` crossed-quote artifact — filter to clean marks). D is
      the live reference (−20% CAL-STOP, 15 completed trades). Build once E accrues a handful of completed calendars.
- [x] **Per-strategy History/Analytics (item 3, 2026-06-22).** Both tabs now follow the strategy picker:
      `/api/metrics/{daily,entries,stops,comparisons,performance}` take an optional `strategy_id` and read that
      variant's own DB (today-from-live-state gated to the canonical id); the picker shows on /history + /analytics.
- [x] **WebSocket per-strategy subscribe — DECIDED 2026-06-22: keep polling.** The non-primary main view already
      works via 2s polling; a true WS multiplex (per-connection strategy channels) was judged not worth the
      live-dashboard risk (cross-strategy message bleed) for the marginal latency gain. Polling is the accepted
      non-primary path. Revisit only if real-time non-primary becomes a felt need.
- [x] **IC `ConfigDelta` control column (item 5, 2026-06-22).** No longer hardcodes `variants["A"]` — derives the
      baseline from `variantIds[0]` (the group's first member, matching the backend's `members[0]`).
- [x] **Per-command Telegram name selectors (item 6, 2026-06-22).** `/status [variant]`, `/snapshot [variant]`,
      `/stops [variant]` render a named NON-primary variant from its own state file via one unified view (reuses
      `_load_variant_state` + `_build_variant_summary`). Pragmatic scope: D/E point at `/calendars`; A falls
      through to the full view.
- [x] **Genericize the `[DCTM-*]` log tags (item 2, 2026-06-22).** The shared `CalendarStrategyBase` now logs a
      neutral `[CAL-*]` (D's own file keeps its legitimate `[DCTM-*]`), so E's calendar plumbing is no longer
      mis-attributed to D.

## 6. Brandon viability + fill-quality (from the 2026-06-22 review)

Two fixes shipped 2026-06-22 (live on B/C): **MKT-048** (entry fillability gate — don't open a side whose
`short_bid − long_mid` can't clear the net-credit floor) and **MKT-049** (exit net-of-cost TP gate — don't
take profit on a mid mark when the real `short_ask − long_bid` + commission gives the gain back; defer to
expiry instead). See `bots/hydra/__init__.py` version history.

- [ ] **CONFIRM LIVE (P2.5):** verify MKT-048 eliminates the leg-3 entry bleed and MKT-049 cuts the close drag
      on the **live variant** — this was tracked against C's logs through 2026-07-24; **since the B↔C
      live-seat swap, confirm against B's logs going forward** (`MKT-048: … vetoing` and `MKT-049 … DEFERRED`).
      C's fixes remain live too (both features are config-driven on B/C alike, not variant-gated) but C is no
      longer the real-fill confirmation source.
- [ ] **Commission ratio (P2):** commission/gross is **invariant to contract count** (both scale with size);
      it's driven by **credit-per-spread**. Over C's live period (through 2026-07-24) ~45% of gross credit was
      lost to commission + close slippage. Biggest recovered chunk = holding thin spreads to expiry (MKT-049).
      Keep measuring — now against B's real fills as the live source.
- [ ] **FUTURE TEST (parked — Lever 2, do NOT start yet):** test a **thicker-credit-per-spread** strike config
      (wider / closer-to-money shorts → more credit per leg → lower commission ratio). Originally scoped as
      "test on B (dry-run shadow), compare vs C, then bring to C" — that no longer maps cleanly post-swap since
      **B is now the live variant and C is dry-run-shadow**; if revisited, test on **C (now dry-run-shadow)**,
      compare commission ratio + win rate vs **B's live data**, then decide whether to bring it to B. This is a
      risk/reward change to Brandon's 8δ delta-target — parked until explicitly requested.

## 7. MVL-D vs Strategy E (2026-06-22 — IMPORTANT realization)

**Strategy E already IS the "transformer-less managed double calendar" that an MVL-D would be** — just on SPY
(not SPX) and with *better* management (laddered partial profit-take + time-exit before short expiry, no hard
stop) than D's crude `−20% stop + EOD-close`. D's transformer is the only thing E lacks, and the transformer
is exactly the unproven/illusory part (see `docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md`). So:

- [ ] **DECIDE before building MVL-D:** do we even need an SPX plain-calendar test when E already runs the SPY
      one? E answers "is a plain managed double calendar +EV?" today. MVL-D's only *unique* value is (a) SPX
      underlying (cash-settled, no assignment, bigger notional, different liquidity/tax) and (b) hard-stop vs
      E's ladder as a management comparison.
- [ ] **If we do build it:** don't re-derive "D minus transform with a crude stop." Instead scope MVL-D as
      **"E-style management ported onto an SPX double calendar"** (reuse E's proven ladder + time-exit; swap
      underlying SPX↔SPY in `CalendarStrategyBase`). Cheaper and strictly better-managed than gutting D.
- [ ] Either way: **gate behind E proving +EV first** in dry-run. Building a second unproven calendar before
      the first one shows an edge is premature.

## 8. Dashboard — make take-profits visible on the Intraday P&L line

The Intraday P&L line plots `realized − commission + Σ(live mark-to-market of open positions)` (a mark-to-market
equity curve), so a take-profit is a near-no-op on the curve (the gain was already "in" as unrealized before
the close). It's NOT a bug — B's "smooth" line and C's "stepped" look are the same code at different P&L scales.
To actually *show* TPs:

- [ ] **Preferred — TP/close event markers:** backend exposes each entry's close events (timestamp + reason +
      realized); frontend overlays dots/annotations on `PnLCurve.tsx` at those timestamps. Keeps the live
      mark-to-market line AND shows where TPs fired (works at any scale). Read-only dashboard change.
- [ ] **Alternative — realized-only line:** plot only `realized − commission` (no open-position mark), so the
      line is flat between closes and steps up at each TP. Crisp TP steps, but you lose the intraday
      mark-to-market "how are open positions doing right now" view. Design choice; would apply to all variants.

## 9. Repo hygiene (2026-06-22)

- [x] **`.gitignore` hardened against stray `git add -A`.** The VM accumulated ~28 untracked artifacts not
      covered by ignore rules; now `*.bak*` (was `*.bak`), `dist_old/`, `intel/homer/`, and the `.cache/`
      `.config/` `.gsutil/` tool-cache dirs are ignored. Verified the VM `git status` is now clean. HOMER is
      already path-scoped, so this defends mainly against a human/script `add -A`.
- [ ] **Optional — untrack the legacy `intel/homer/2026-05-*.md`.** Those dated reports are TRACKED (swept in
      by the pre-2026-06-16 `git add -A` HOMER bug); the dir is now ignored so new ones won't be added, but the
      old tracked ones remain (cosmetic "tracked-but-ignored"). `git rm --cached intel/homer/2026-05-*.md`
      cleans it up (keeps the files on disk). Not done automatically — it removes them from the repo.
- [ ] **Optional — delete stale disk clutter on the VM** (old `dist.bak.*` from April, ancient config `.bak`s).
      Pure disk cleanup; left alone since some config backups may be intentional safety copies.

## 10. Go-Live documentation consolidation — `GO_LIVE_MASTER.md` + checklist refresh (2026-07-14)

> **Why:** go-live knowledge is FRAGMENTED across ~6 docs + 3 scripts; there is **no single master doc** covering
> all strategies × both go-live levels × all tests. `docs/migration/LIVE_READINESS_CHECKLIST.md` reads like a
> master but is **stale (2026-05-24), variant-A-only, 0DTE-shaped, pre-broker**. Most content already EXISTS —
> this is a **consolidating umbrella + a currency refresh, NOT net-new go-live thinking** (except the build-gated
> D/E ops runbooks). Do this before any real go-live push. Full inventory: memory `golive_readiness_and_fill_lever`.
>
> **✅ STATUS (2026-07-14): Groups A + B + C DONE.** `docs/GO_LIVE_MASTER.md` created (A1–A6); wired as the
> entry point in CLAUDE.md doc index + NEXT_STEPS §0 (A7); `LIVE_READINESS_CHECKLIST.md` refreshed —
> real-money relabel + scope banner, ~1918 test count, broker-era cred model, broker-first restart order (B1–B5);
> **C1 stale D-doc line refs freshened** (6 drifted refs corrected, all cross-checked == current `def` line).
> **Remaining: only the §8 build-gated artifacts** (E runbook, `flip_d/e_live.sh`, `broker_dc_smoke.py`,
> the D/E flip/flatten runbooks), which are blocked on the D/E real-order builds.
>
> **✅ B↔C live-paper swap EXECUTED 2026-07-24.** Researched + audited in
> [`BC_SWAP_PLAN.md`](migration/BC_SWAP_PLAN.md) (2026-07-21), run via `scripts/flip_bc_swap.sh` — B is now the
> live-paper seat (dashboard PRIMARY), C is now dry-run-shadow, A unchanged. `RUNBOOKS.md` RB-9 documents the
> swap/rollback procedure. Docs reconciliation (CLAUDE.md, `PROJECT_STATUS.md`, this file, `BC_SWAP_PLAN.md`'s
> own status banner) done 2026-07-24; `GO_LIVE_MASTER.md`'s A2 status matrix + any other B/C-status references
> still need a pass to reflect the new seat (tracked in A2 above).

### A. Create `GO_LIVE_MASTER.md` (top-level umbrella — thin, links out, does NOT duplicate content)
- [ ] **A1. Two-levels map + boundary.** Level (i) dry-run→live-PAPER (real paper orders) vs level (ii)
      live-paper→REAL MONEY (new live-OAuth keypair + approval — deliberately unwired on this branch). Make the
      boundary explicit; state up front that this branch is paper-only.
- [ ] **A2. A/B/C/D/E status matrix** — columns: current mode · next gate · flip script · smoke test · edge/readiness
      verdict. Source: CLAUDE.md Variant Comparison + §2b/§3/§5 + the D trio. (as of 2026-07-24: B = live-paper;
      A/C = dry-run-shadow; D = NO-GO build; E = dry-run-locked.)
- [ ] **A3. Consolidated tests + pass-criteria appendix** — one surface listing each check + WHAT it verifies +
      PASS criteria + how to run: `broker_paper_smoke.py` (paper-only + 6509='R' on SPX/VIX/leg + 1c round trip
      → ET sentinel); pytest suite (~1918 pass) + `tests/integration/test_ib_paper_smoke.py`; `pip-audit`
      (0 High/Crit); edge readers (`slot_edge.py`, `stop_shadow.py`, `dc_edge.py`/`analyze_calendar_edge.py`);
      D's STATE-004 matrix.
- [ ] **A4. Links-out index** — RB-8 (`RUNBOOKS.md`), `flip_ac_live.sh`/`flip_a_live.sh`, `broker_paper_smoke.py`,
      the D trio (`D_GOLIVE_RUNBOOK.md` / `_SCOPE_AND_AUDIT.md` / `_MVL_PHASE1_PLAN.md`), `NEXT_STEPS.md`
      §2b/§3/§5, `LIVE_READINESS_CHECKLIST.md`, `IBKR_CREDENTIALS_SETUP.md`, `NEW_STRATEGY_PLAYBOOK.md` Step 10.
- [ ] **A5. Build-gated pending slots** — mark NOT-YET-BUILT + blocker: E go-live runbook, `flip_d_live.sh`,
      `flip_e_live.sh`, `broker_dc_smoke.py`, RB-9 (flip D) / RB-10 (flatten D). All gated on the D/E real-order builds.
- [ ] **A6. Go-live line items carried in the master** — (a) split-spread/midpoint ENTRY-pricing fill-quality lever
      (targets the ~31% sim-vs-live credit gap; entry-only; validate on real money); (b) the mid-August
      Entry-Schedule Lock (§5); (c) per-strategy edge-validation status.
- [ ] **A7. Wire it as the entry point** — pointer row in CLAUDE.md's doc index + a "start here for go-live" line
      in `PROJECT_STATUS.md` and NEXT_STEPS §0, pointing at `GO_LIVE_MASTER.md`.

### B. Refresh `docs/migration/LIVE_READINESS_CHECKLIST.md` (so it stops masquerading as the master)
- [ ] **B1. Broker-era cred model** — Gate 5 says `bots/hydra/main.py` calls `load_credentials("live")` +
      `systemctl restart hydra`; update to: OAuth identity lives in `calypso-broker` (creds via systemd-creds per
      `IBKR_CREDENTIALS_SETUP.md`); a session/auth fault is fixed at `calypso-broker`, not the hydra units.
- [ ] **B2. Current test counts** — "≥885 pass" → ~1918 pass / 15 skipped; refresh smoke/integration references.
- [ ] **B3. Multi-variant rescope** — written for HYDRA=A only. Either broaden to A/B/C (0DTE credit group) or
      relabel "real-money gate — 0DTE IC variants" + note the calendar group (D/E) needs a SEPARATE real-money
      gate (Gates 4/7/8/9 don't transfer to calendars, per `D_GOLIVE_SCOPE_AND_AUDIT.md` §5).
- [ ] **B4. Reconcile ops/restart steps** with broker mode (restart order: broker first; hydra units degrade
      gracefully through a broker restart).
- [ ] **B5. Path note** — lives at `docs/migration/`, not `docs/`; add a top-of-file note + link from the master.

### C. Verify + ship
- [x] **C1. Freshen stale file:line refs** in the D docs — DONE 2026-07-14. Fixed 6 drifted refs:
      `_initiate_entry` `double_calendar_strategy.py:803→469` + `main.py:526→513` (SCOPE_AND_AUDIT); and in
      RUNBOOK `_reset_for_new_day` `~10979→~11774`, `_read_open_positions` `~1902→~1937`,
      `_recon_detect_orphans` `~10796→~11553`, `_reconcile_orphan_sweep` `~10936→~11729`,
      `_check_buying_power` `~6078→~6240`. (The `_dc_*` methods that moved to `calendar_strategy_base.py` in
      the extraction were only referenced *by name* in the D docs — no stale file:line — and the RUNBOOK §11
      Appendix already documents the lift. Every ref cross-checked == current `def` line.)
- [ ] **C2. Commit clean** (docs-only, HOMER-safe: keep VM `git status` clean; no VM deploy needed for docs).
- [ ] **C3. Check these off here** as they land; move detail into `GO_LIVE_MASTER.md`.
