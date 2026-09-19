# CALYPSO — Next Steps (living doc)

> **This is the single, always-current "what's left" tracker — read §A first.** Update it whenever work
> lands or a new item appears. It complements (does not replace)
> [`docs/migration/PROJECT_STATUS.md`](migration/PROJECT_STATUS.md) (project-wide state) and the per-effort
> design docs.
>
> **Last updated: 2026-09-19 (Sat, 03:40 ET).** **Read §A0 first — it is the whole current state on
> one screen.** §A–§D are current. **§0–§10 are the older backlog (2026-07-14 / 07-24 era)** — much of
> it is done or superseded; **verify against the code before acting on anything there.** Real live
> items still live in §5 (entry-schedule lock, E calendar-stop analyzer) and §6 (Brandon fill-quality
> confirmations), which is why those sections are kept rather than deleted.

---

# §A0. WHERE WE ARE — 2026-09-19 (Sat), 03:40 ET

**The restart is DONE and verified.** Broker + all 7 strategies came up on the current build
(`ed229e8`) at **07:32–07:34 UTC (03:32–03:34 ET)** — market closed, account **FLAT (0 position
rows)**, `NRestarts=0` on every unit, VM tree clean and in sync, unit files already matching the
repo (no `daemon-reload` needed). Everything §A0 listed yesterday as "on disk but not running" is
now **live**: MKT-011B, the alert token-bucket fix, the account guard + `/health` identity, the
ORDER-004 margin floor, the bespoke-path changes, and D/E (which were still on Thursday's code).

**The account guard works in production.** `/health` now answers
`{"environment":"paper","account":"DUR049068",...}`, and every variant logged
`ACCOUNT-ASSERT OK: variant X declares 'paper'; broker reports 'paper'` — A, B, C, D, E, F, G,
seven for seven. The IBKR session stayed up across the restart; no repeat of the 09-12 weekend
`410 Gone`.

### 🟢 Gate 4 can start counting on Monday

The restart landed on a **non-trading Saturday**, so it interrupted no session and burned nothing
— the streak is still at 0. **Monday 2026-09-21 can be session 1 of the five**, but only if
nothing else is deployed between now and then. That is an operator choice, not a technical one:
freeze and start the clock, or keep shipping and reset it. Funding is 4–6 weeks out, so the clock
is still cheap to reset, and the real freeze belongs near cutover.

### The whatif probe: it is the BAG form that is dead, not whatif

Re-run out of hours as planned (`--expiry 2026-09-21` for Monday's contracts, all four legs
snapshot first). The results **overturn the reading we had**:

| Form | Result |
|---|---|
| 4-leg BAG, bare template | **em-dash placeholders** in every money block — no margin, no amount |
| 4-leg BAG, `@CBOE`-routed | identical em-dash placeholders |
| 2-leg call vertical (BAG) | **timed out**, and the retries opened the `ib.orders` breaker |
| **1-leg naked short call (plain)** | **full, correct block** — amount 225 USD, commission 1.63, initial.change **109,213**, position −1 |

That last row came from a direct RPC call *after* the probe, because the probe's own control was
refused by the breaker the 2-leg BAG had just opened — the control ran last. **So `what_if_order`
is healthy; it is the BAG/combo ticket IBKR will not margin on this paper account.** This is the
margin-preview counterpart to the known-broken paper combo *lifecycle*
(`combos_not_operable_on_ibkr_paper`), and it now rests on evidence rather than inference: the
placeholders appeared *with* all four legs snapshot, so they are not the missing-snapshot artifact
the script's own comment warns about.

Two consequences:

- **`what_if_naked_margin` (the S2 strangle gate) is NOT inert.** Single-leg whatif is exactly the
  shape it previews, and it prices. That had been in doubt; it no longer is.
- **S6 cannot be answered the way it was framed.** A defined-risk IC margin is only obtainable from
  IBKR through a BAG ticket, and the BAG returns nothing here. Summing per-leg naked previews is
  useless as a gate — one naked SPX short is **$109,213**, so a four-leg sum would block every
  entry. The one shape not yet tested is the one that would actually work: **whatif the SHORT leg
  while its protective LONG is already held**, which is how HYDRA legs in (long-first). That needs
  a real paper long on the books, so it is a market-hours test, not a weekend one.

Probe fixed so this cannot recur: the control now runs **first**
(`scripts/probe_combo_whatif.py`), with three regression tests that fail against the old ordering.

### ⚠️ The dashboard was a restart behind too — found and fixed 2026-09-19

§A0 recorded "Dashboard deployed + verified (domytrade.com serving the new build)" on 09-18.
That was true **when it was written** and false by the end of the day, because three more
dashboard commits landed after the deploy:

| | Deployed / running | Commits it was missing |
|---|---|---|
| **Frontend** | build of 09-18 **16:16 UTC** | `639777b` (19:52) — `dashboard/frontend/src/lib/pnlShape.ts`, the accent-fallback contrast fix |
| **Backend** | `dashboard.service` started **16:33 UTC** | `da3f5be` (17:32) and `a6a1609` (18:40), both touching `dashboard/backend` |

Rebuilt, scp'd, swapped and restarted 2026-09-19 07:55 UTC. The new bundle
(`index-BxG4G9SY.js`) differs from the one that had been served (`index-CIgQbM0e.js`), which is
the proof the fix really was absent rather than merely suspected. Site returns 200, API healthy,
`NRestarts=0`, no errors.

**The reusable part: "deployed + verified" is a claim with an expiry date.** It decays the moment
the next commit touches the same surface, and nothing in the process notices — the bots have a
restart ritual, the dashboard does not. Worth checking the built asset hash against the last
frontend commit whenever §A0 claims the dashboard is current.

### Pre-cutover documentation re-measurement — 2026-09-19

The go-live documents were re-measured claim by claim against the code and the VM, rather than
re-read. **Six defects, every one of them in an instruction that gets executed.**

| Where | What it told you to do | Consequence |
|---|---|---|
| `IBKR_CREDENTIALS_SETUP.md` | Encrypt the LIVE credentials into `/etc/calypso/ibkr/` (it is paper-only throughout, and says "use the paper-account keypair") | **Overwrites the six paper credentials B is trading on.** Broker returns authenticated to the wrong account, the guard refuses the session, live paper seat down |
| `LIVE_READINESS_CHECKLIST` Gate 5 | Update `calypso-broker.service` to the live credential paths | Moves **all seven** paper strategies onto a real-money account |
| `LIVE_READINESS_CHECKLIST` Gate 6 | `hydra.service` must be `inactive` at the flip | Wrong under the alongside model either way, but be precise about how: taken **literally** it stops variant A, a dry-run shadow, and costs nothing; taken by its **intent** (“don't run on paper during the flip”) it stops the paper fleet including B, the control the real-money seat is measured against. Not an outage — a gate that is either inert or counterproductive |
| `LIVE_READINESS_CHECKLIST` sign-off | `systemctl restart calypso-broker  # picks up the live creds` | The same mistake as a copy-pasteable command, in the block used at the moment of going live |
| `LIVE_MONEY_ARCHITECTURE` §8 | "Two brokers on paper credentials — zero live-money risk" | §7's own 🔴 correction forbids exactly this: same IBKR username, they evict each other, B goes offline |
| `RUNBOOKS.md` ×7 sites | "If unsure, stop the bot: `systemctl stop hydra`" | `hydra` is variant **A**, a dry-run shadow. **Halts no trading at all** while the live seat keeps going |

All corrected. Two more were structural rather than dangerous: the credential directory moved out
of the paper directory (`/etc/calypso/ibkr-live/`, a sibling — free to change only while nothing is
deployed), and `RUNBOOKS.md` gained a **STEP 0: which broker are you fixing**, since it had 26
references to `calypso-broker` and none to the live one.

Smaller drift fixed in passing: Gate 8 graded contracts against B (so B's 7 read as a failure when
the real-money variant `bm` already ships 1) · Gate 6 required `/api/health` to return
`"status":"healthy"`, which it has never returned · Gate 4's null-VIX query read variant A's
database · Gate 9's halt rehearsal named the wrong unit · the test baseline and a
`requirements.txt` line reference.

**The pattern, which is the real finding:** every one was true when written. They rot because the
*architecture* moved (cutover → alongside) or the *roles* moved (A → B as the live seat), and a
document that describes an action has no way to notice either. Verified as sound and needing no
change: every script the runbooks reference exists, the three unbuilt calendar scripts are
correctly marked NOT BUILT, and Gate 2's audit claims re-measure clean (0 OPEN, 0 TODO markers).

### B has taken no entry since 2026-09-15 — and a third of its sessions are like that

Surfaced by the RB-7 rehearsal (the restored DB's latest trade was 09-15, not 09-18). Three
consecutive zero-entry sessions: **09-16** (FOMC, a correct skip), **09-17** and **09-18** (MKT-048
vetoed the entries as unfillable; 09-18 also carries the −$137.20 from the MKT-011B defect).

**A VIX-threshold explanation was tested and REFUTED.** The obvious hypothesis — that B's narrow
5pt spreads stop clearing the fillability floor below some VIX level — does not survive contact
with the data. Across B's 40 live-era sessions (since the 2026-07-24 swap):

| | count | VIX-close range |
|---|---|---|
| sessions **with** entries | 27 | 14.27 – 18.73 |
| sessions with **zero** entries | 13 | 14.45 – 20.25 |

The ranges overlap almost entirely. B traded on the two **lowest**-VIX days of the era (14.27,
14.28) and took nothing at 20.25. VIX close does not discriminate — though note it is the *close*,
while entries are decided 10:15–12:45, so intraday VIX at decision time is the better variable and
has not been tested.

**What IS true, and what matters for go-live: B produces no entries in ~33% of sessions (13 of
40).** The current three-day run is not an anomaly — 07-29 through 08-03 was a four-session
drought, and 08-10/11/12 was another three. Two consequences worth holding:

- **Gate 4 can be satisfied by five dead sessions.** The gate counts sessions without manual
  intervention, not sessions that traded. Five consecutive no-entry days would close it while
  proving nothing about execution — which is the opposite of what it exists to establish.
- **A funded `bm` could sit idle for a week** and that would be normal behaviour, not a fault.
  Worth expecting, so it is not misread as a broken deployment at the worst moment.

### Still unverified in production

- **POS-003 merged-leg resolver** (deployed 09-15) — still needs a session with both a stop and an
  overlapping strike.
- **GEX veto EV** — data-blocked until ~20 vetoes carry both strikes AND credit (~2 weeks).
- **MKT-011B, the alert bucket fix, the ORDER-004 floor** — running now, but none has met a live
  entry yet. Monday is the first opportunity.

### Next actions, in order

1. **[operator decision]** Freeze, or keep shipping — see Gate 4 above. Nothing below is urgent,
   and all of it resets the streak.
2. **[a market-hours slot]** The S6 test in its corrected form: whatif the short leg while the
   protective long is already held.
3. **[~2 weeks]** GEX veto EV, once telemetry accumulates.

### Blocked on the operator — the critical path

**FUND THE LIVE ACCOUNT** (created, not funded; confirm it is a **margin** account). Then, strictly
ordered: permissions → live market-data subscriptions → a new OAuth keypair (~2wk activation).
**4–6 weeks, all calendar, none of it engineering.** Also yours: the halt thresholds and the
approval commit (Gate 9).

### Gate board

| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|
| 🟡 | 🟢 | 🟡 | 🔴 | 🟡 | ⚪ | 🟢 | 🔴 | 🟡 | 🟡 |

Gate 4 is at **0** and un-burned — Monday can be session 1. The gate counts **any** code-reason
restart as intervention, and rightly so: the point is five sessions on ONE unchanged build, not
five that happen to be quiet. Gate 8 is 🔴 only because B runs 7 contracts against a mandated 1 —
a cutover-time config change, not work.

---

# §A. DO NEXT — by priority (2026-09-10)

### P0 — ✅ DEPLOYED 2026-09-11 *(historical — see §A0 for current state)*

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

### P0-bis — Saturday 2026-09-12 state *(HISTORICAL — both Monday gates closed; see §A0)*

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

### P1 — DONE Fri 2026-09-11: the first passively-priced session *(historical)*

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

      **DONE 2026-09-12 — the writer has its caller.** `update_skipped_entry_backtest` is no longer
      dead: `scripts/analyze_skipped_entry_outcomes.py --apply` computes `would_have_stopped` /
      `theoretical_pnl` from the day's `market_ticks` range and writes them back, so results land in the
      data HOMER and the dashboard read instead of only in a script's stdout.

      **The caller is the SCRIPT, not settlement** — a deliberate reversal of the plan above. Both
      inputs are persisted, so the computation is not time-sensitive, and putting it in the trading
      process would add a failure mode to the live path for no benefit. Dry-run by default; `--apply`
      takes a WAL-safe, timestamped backup first.

      **What it writes is MODELLED, and callers must treat it that way.** The breach is measured (did
      SPX cross the proposed short after the skip time); the dollars assume B's acting A2 stop, so the
      run prints the `--pct-of-width`/`--contracts` model it used. A row with no modellable outcome is
      left NULL rather than 0 — a 0 would read as a breakeven breach and silently flatter the veto.

      **UPDATE 2026-09-12 — ran it on real rows for the first time; found a second missing half.**
      3 GEX vetoes now carry strikes (2026-09-11 onward) and the pipeline works end-to-end — but all
      three modelled **$0.00**, because the *credit* was never recorded at that site. The two halves
      were written by different skip sites and never together: the credit-gate skip recorded credits
      with NULL strikes; the require-both-sides skip (where GEX vetoes land) recorded strikes with
      NULL credits. An unbreached veto is worth the credit it would have kept, so a missing credit
      reads as "vetoing cost us nothing" instead of "unknown" — flattering the gate in exactly the
      direction being tested. Fixed in `9be763e`; vetoes from 2026-09-12 onward carry both.

      **REMAINING:** (a) wait for ~20 vetoes recorded WITH BOTH strikes and credit (n=3 today, and
      those 3 have no credit — they stay $0-modelled forever), then run `--apply` and the EV is a
      query;
      (b) the historical 95 stay unmeasurable — their strikes were never recorded and cannot be
      recovered; (c) 8 other skip sites still do not record strikes (pre-strike skips deliberately
      never will — there is nothing to record).

      **Still recommended: do not block on it.** Existing evidence (vetoed shorts got breached; placed
      ones did not) already favours KEEPING the gate, and nothing downstream depends on the number.

### P2-bis — the REAL-MONEY track (measured 2026-09-12)

**Gate-by-gate status now lives in [`GO_LIVE_MASTER.md` §2-bis](GO_LIVE_MASTER.md)**, measured rather than
asserted, with `LIVE_READINESS_CHECKLIST.md` refreshed to match (its "~1918 tests" baseline and its A-centric
framing were both stale; the live seat is B).

**The critical path is external, not engineering.** The live IBKR account is **created but NOT funded**, and
four strictly-ordered steps precede the keypair Gate 5 opens on: fund → options-spread permissions → live
market-data subscriptions (a live account inherits **zero** entitlements from paper) → keypair → activation
wait. Start that chain first; the repo work proceeds underneath it.

**The three repo blockers, in dependency order:**
1. ~~**`main` merge plan needs rewriting**~~ — **DONE 2026-09-12** (`c864cde`). Rewritten for **613
   ahead / 7 behind** and **dry-run verified end-to-end** in a throwaway clone. Decision reversed:
   **`--no-ff`, do NOT squash** — 186 SHA citations in this repo's own docs exist only on this branch
   and a squash strands every one. One predictable conflict (`HYDRA_TRADING_JOURNAL.md`), resolution
   verified lossless. **MERGED + PUSHED 2026-09-12** — `main` is at `59a1fc7` with all 613 commits and
   full history; suite green on the merge result. The VM stays on the feature branch until the
   go-live window (HOMER would otherwise auto-commit to `main` nightly) — see GO_LIVE_MASTER §2-bis.
2. ~~**`pip-audit` is RED**~~ — **DONE 2026-09-12.** `cryptography` 48.0.0 → **50.0.0** clears all four
   advisories; `pip-audit` now reports no known vulnerabilities. Needed `msal` 1.36 → 1.38 to lift a
   `cryptography<49` ceiling. **Correction:** it is *not* under the OAuth path — ibind signs with
   pycryptodome — it backs google-auth/PyJWT, i.e. Secret Manager / Sheets / Pub/Sub.
2-bis. ~~**Dashboard dependency CVEs**~~ — **DONE 2026-09-12 (`cf37e48`). The VM is now at ZERO known
   vulnerabilities**, from 44 across 9 packages this morning. Root cause was structural: the dashboard
   stack was never pinned (requirements.txt said so), and `pip-audit -r requirements.txt` can only see
   what the file lists — so the CVEs were invisible. Now pinned: `starlette` 0.52→**1.3.1** (5
   advisories), `fastapi`→0.137.1, `pydantic`→2.13.4, `pydantic-settings`→2.14.2, `click`→8.3.3,
   `msgpack`→1.2.1. The major-version starlette jump was safe because FastAPI sets no upper bound;
   validated by 142 dashboard tests plus a live check (REST 200/401, WS 403-by-auth, login 422/401,
   `domytrade.com` 200). Dashboard-only restart — verified the trading path imports none of them.
3. **A change freeze**, then Gate 4's 5 clean sessions + the **chaos test** and Gate 7's **RB-7 restore
   rehearsal** — neither has *ever* been run (0 journal records each). These cannot overlap with (1)'s deploys.

**Not yet on the evidence, either.** B is 71% wins at a **0.76 payoff ratio** over **24 traded days that have
never contained a bad day** — the profile that looks best right up to a tail event. Sharpe 3.99 is flagged
provisional and will regress. ~38% of traded-day P&L rides on 9 gated days whose counterfactual only started
being recorded on 2026-09-11. Steps 1–3 take weeks anyway; running them buys exactly the sessions that would
make the flip defensible.

---

### P2-quater — ✅ FIXED 2026-09-15: variant F never recorded a single entry

`variant_f`: **0 `trade_entries` ever, 1 `trade_stop`, lifetime −$14.80.** F had genuinely traded —
its heartbeat showed the position — but `GhauriMeanReversionStrategy._initiate_entry` bypasses
`HydraStrategy._initiate_entry` (for sound reasons) and dropped the `_record_entry_to_db` call.
**Stops were unaffected** because they run through the inherited `_execute_stop_loss`, so F's entire
recorded history was losses; it would have read as a never-wins strategy forever.

Fixed in `88ec8ed` (+ the immediate `_save_state_to_disk` the parent does). Wrapped defensively —
F's method is `try/finally` with no `except`, so a raise would have escaped past the
POSITION_OPENED alert on an already-open position. Deployed to `hydra_variant_f` only, which does
**not** break the Gate-4 streak (measured on the live seat).

**The generalisable lesson:** a self-contained override inherits the parts you didn't think about and
omits the parts you did. **Any future strategy that overrides `_initiate_entry` must be checked for
this exact omission** — the symptom is stops-without-entries, and it is invisible unless you look.

### P2-ter — ✅ FIXED 2026-09-15: POS-003 now resolves merged legs instead of giving up

**The mechanism was NOT what the first diagnosis said.** It is not two entries picking the same
strike on the same side. With 5pt-wide spreads placed 30 min apart, **one entry's protective LONG
lands on another entry's SHORT** — same conid, opposite signs. Verified against 2026-09-11:

```
strike 7710:  E#1 short_call  +  E#5 long_call     -> nets to 0
strike 7715:  E#1 long_call   +  E#2 short_call    -> nets to 0
```

They net to zero, so expected and actual agree — until the short is stopped. The broker then shows
**+7 against an expectation of 0**, the code saw "maps to 2 tracked legs", logged *ambiguous, leaving
for manual review*, and stopped. **A real 7-lot sat untracked for six hours**, firing CRITICAL alerts
nobody read. An untracked position has no stop on it.

**It was never ambiguous.** The contribution that disappeared is exactly `expected − actual`, so
identifying the vanished legs is subset-sum over a few signed numbers. Net +7 against
{short −7, long +7} has one answer. Fixed in `c1544fb` — deployed to all strategies 2026-09-15 04:09
ET (flat account, settlement complete, pre-market).

**The strike-avoidance fix originally proposed here would NOT have worked** and is abandoned:
avoiding long/short overlap with 5pt spreads forces entries ≥10pt apart, which fights the
delta-targeting that is the point of the Brandon stack — damaging the strategy to dodge a
bookkeeping problem. **No trade, strike or stop behaviour changed**; only what the bot does when its
own books look odd.

**Still refuses to guess:** only a UNIQUE solution is acted on. Two same-sign legs are NOT
interchangeable (credits differ ⇒ P&L mis-attribution), so ties still go to manual review, and the
partial-fill guard survives for free.

⚠️ **This reset the Gate-4 clean-session streak to 0** (was 2), deliberately: that streak's day 1 was
−$1,756 and Gate 4 also requires the 5 sessions to net ≥ 0, which it could not have met. Fix first,
then freeze on corrected code.

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

# §A-bis-3. ✅ DASHBOARD BROKEN FOR NON-LIVE VARIANTS — CLOSED 2026-09-17/18

> **All 8 defects closed.** Phases 0–10 shipped (`890f748` contract test → `5c6ca5c` endpoint
> scoping → `4876db6`/`5161303` the taxonomy axis → `c29bb59` per-strategy capital → `c26d51a`
> previous-session block → `5972f46` the calendar page can show E → `ebc5be4` total_trades deleted →
> `f5a026b` undefined-risk card → `ba4395b` one-sided rendering). The architectural cause — `pnl_shape`
> conflating how P&L is earned with what capital means — was split into `capital_basis` and `sides`.
>
> Kept below as the record of what was wrong and why, because the root cause is worth remembering:
> **the dashboard had been shown to investors and had never been audited** — every "no bugs" given
> before 2026-09-17 was scoped to trading deploys.


Full audit: [`DASHBOARD_VARIANT_AUDIT_2026_09_17.md`](DASHBOARD_VARIANT_AUDIT_2026_09_17.md).
Found the hard way — the dashboard was shown to investors. **I had never audited it**; every "no
bugs" I gave was scoped to trading deploys.

**There IS a shared model** (all 7 variants return an identical shape via `variant_readers`). These
are three defects *inside* it, not per-variant one-offs.

| | Bug | Root cause | Blast radius |
|---|---|---|---|
| **1** | `total_trades` permanently **0** | `base_strategy.py:5899` initialises it; **nothing ever increments it** | **Every** variant incl. B — breaks win-rate-per-trade, avg-per-trade |
| **2** | G has **no** return-on-capital / avg-capital-per-day | `_calculate_capital_deployed` does `if entry.spread_width <= 0: continue`; **G is a naked strangle with no wings** → 0 `daily_returns` rows ever | G (and any future undefined-risk strategy). **Also makes G's Sortino meaningless** |
| **3** | Only **B** shows the previous day | `/api/hydra/summary` + `/api/metrics/cumulative` take **no `strategy_id`** — canonical/live-seat only, while `/api/hydra/entries` + `/api/metrics/daily` were scoped in July (`4b3d6a0`). **That fix was partial.** | Every non-live variant shows the live seat's data or nothing |

**Note on "empty charts pre-market":** partly expected. The per-strategy snapshot has **no
previous-day concept for ANY variant, B included** — pre-market it returns the freshly-reset day, so
`entries`/`ohlc`/`spx_open` are legitimately empty for everyone. B only *looks* right because the
main page reads the unscoped endpoints above.

**SUPERSEDED by the full audit — see [`DASHBOARD_REBUILD_PLAN.md`](DASHBOARD_REBUILD_PLAN.md).**
The exhaustive pass found **8 defects, not 3**, and one architectural cause behind most of them:
`pnl_shape` conflates *how P&L is earned* (credit/debit) with *what capital means* (spread width /
net debit / broker margin). G earns a credit, so it is tagged `credit` and inherits the iron-condor
renderer **and its capital model** — which is why ROC is missing rather than merely blank.
Also newly found: **`/api/dc/status` is hardcoded to `variant_d`, so E can never show its own data**,
and **F is classified `iron_condor` but trades one-sided**, so it renders a side it never has.
The plan adds `capital_basis` + `sides` to the taxonomy and gives G margin/tail cards instead of
return-on-width. 7 phases, each independently shippable; Phase 0 is a contract test that must FAIL
on today's code.

**None of this touches trading.** The dashboard is a separate service; restarting it cannot affect
the bots or the clean-session streak.

---

# §A-bis-2. OPEN FINDINGS from the 2026-09-16 review

### ✅ F's daily summary books MORE than the trade could earn — DIAGNOSED + FIXED 2026-09-18

**Three bugs, one commit (`9906839`).** Deployed, historical rows corrected, F restarted.

1. **The P&L was booked twice.** `_close_entry_early` books the side itself via
   `_book_early_close_side_pnl`, *unconditionally*. In dry-run there is no fill, so
   `side_close_cost` arrives as 0 and it books the FULL credit as if the position closed free.
   Brandon corrects for this by booking `-close_cost` afterwards
   (`brandon/strategy.py:1313`, `:1330`); Ghauri booked `credit - close_cost` — a second full
   booking. `2 × 127.50 − 60.00 = 195.00`, exactly what was stored. Ghauri's docstring claimed
   "the live booking path inside `_close_entry_early` doesn't fire in dry-run" — false on both
   counts.
2. **`entries_completed` was never incremented**, so `entries_placed` was 0 on every F trading
   day and cumulative `total_entries` stayed 0. **Every traded-day analysis filters on
   `entries_placed > 0`** (`complementarity.py`, `variant_performance.py`), so F was silently
   excluded from all of them while still accumulating P&L.
3. **The opening commission was never charged** — only the close side landed (from
   `strategy.py:3709`), hence $2.30 for what is a $4.60 round trip.

Root cause of 2 and 3 is the same as the 2026-09-15 entry-recording bug, in the same method:
F's `_initiate_entry` deliberately bypasses `HydraStrategy._initiate_entry` and re-implements
only the parts someone noticed. An AST sweep now enforces the invariant fleet-wide — F was the
lone outlier; A/B/C, D, E and G all did it correctly.

| date | gross | commission | net | entries |
|---|---|---|---|---|
| 2026-09-14 | −12.50 (unchanged) | 2.30 → **4.60** | −14.80 → **−17.10** | 0 → **1** |
| 2026-09-15 | 195.00 → **67.50** | 2.30 → **4.60** | 192.70 → **62.90** | 0 → **1** |

**F's lifetime: $177.90 → $45.80.** The repair script recovered the close cost algebraically
(`cost = 2·credit − stored_gross`) and landed on **exactly $60.00** — the value in the log line
`TP fired: SV $60.00` — an independent confirmation the model of the bug was right.

Checked and NOT affected: G never calls `_close_entry_early` or `_book_realized_pnl`; Brandon's
four sites all book `-close_cost`; B is live so its `if self.dry_run:` branch never fires.

⚠️ **An existing test was asserting the bug.** `test_take_profit_fires_and_closes` stubbed
`_close_entry_early` so it booked *nothing*, then asserted the handler books `credit − cost`.
The stub did not behave like the collaborator it replaced, so it encoded the same
misunderstanding as the docstring and the two held the double-count in place for the life of the
strategy. Worth remembering as a pattern: **a stub that is wrong about its collaborator can
pin a bug in place indefinitely.**

### 🔴 *(original text, kept for the record)* F's daily summary books MORE than the trade could earn

```
trade_entries    2026-09-15 e#1  put 7550/7540  total_credit $127.50
trade_stops      2026-09-15 e#1  put  early_close  debit $0.00  ->  +$127.50   (correct: keeps the credit)
daily_summaries  2026-09-15      entries_placed=0  gross $195.00  net $192.70
```

**Two disagreements, same row.** Gross is **$67.50 more than the entire credit** — a short spread
cannot earn more than it collected (CLAUDE.md lesson #14). And `entries_placed=0` contradicts the
entry that is demonstrably recorded. The stop row and the entry row agree with each other; the daily
summary agrees with neither, so the fault is in F's own daily-state accounting rather than in the
recording fix. **Dry-run only, so no money — but the numbers are wrong, and F's lifetime P&L is built
from them.** Not yet diagnosed.

### ⚠️ The UNDEFINED-RISK strategy is the one trading FOMC days

| variant | `fomc_announcement_skip` | traded 2026-09-16 (FOMC) |
|---|---|---|
| A, B, C (defined risk) | **True** | no |
| F | False | — |
| **G (naked strangle, undefined risk)** | **False** | **yes — 2 entries** |

G sold ~$1,480 and ~$1,605 of event premium (vs ~$220 on a normal day — the credit is real, it is the
announcement being priced), had both call sides stopped, and went into the 14:00 announcement holding
**two naked short puts**. G is dry-run, so this is a research observation, not an incident — but it
is backwards: the one strategy with unbounded loss is the only one taking Fed-announcement risk,
while every defined-risk strategy sits out.

**DECIDED 2026-09-17 — leave it ON in dry-run, gate it at promotion.** Switching the skip on today
would cost the scarce event-day data (~8 FOMC days/yr) and protect nothing, because nothing is at
risk in simulation. The danger was never "G trades FOMC in dry-run" — it is G being promoted while
carrying that default. Recorded as blockers **G-1/G-2/G-3** in
[`GO_LIVE_MASTER.md` §2-ter](GO_LIVE_MASTER.md), which the Level-I flip section now points at.

**How 2026-09-16 actually ended:** G sold $3,085 of event premium, was whipsawed on BOTH sides
(4 stops: calls at 11:00/11:47 as SPX ran to 7626, puts at 12:28/13:04 as it reversed to 7510) and
netted **−$708.80** on a **1.53% range** day — the largest in the dataset, and an independent
confirmation of the complementarity finding that RANGE is what kills these strategies. A/B/C skipped
and lost nothing. **G's stops worked** — it was flat before the announcement — but that is path luck:
stops protect against a move, not a gap. n=1; revisit after ~3 more FOMC days.

### ✅ CLAUDE.md is stale on FOMC — FIXED

> Corrected in CLAUDE.md, which now reads "**ENABLED on A/B/C**" with the 2026-09-16 live evidence
> and an explicit warning that **F and G have it FALSE and DO trade announcement days** — including
> G, the undefined-risk naked strangle. This note is retained only so the correction is traceable.


It states *"FOMC Announcement Skip: DISABLED. Bot trades normally on FOMC days."* The live configs
say `fomc_announcement_skip=True` on A/B/C, and B demonstrably skipped 2026-09-16. The memory
`fomc_trading_policy` ("0DTE A/B/C SKIP FOMC T+0") is the accurate one.

---

# §A-ter. SESSION LOG — the live seat, most recent first

Keep this short: date, what happened, what it proved. Detail belongs in the linked commits.

| Date | B (live) | What it proved |
|---|---|---|
| **2026-09-18** (Fri) | **−$137.20** · 0e/0s | **Zero entries, and that was right.** At VIX 15.4 the 8δ 5pt spreads were worth ~$0.05 with a *fillable* price of $0.03/$0.02/−$0.00; MKT-048 vetoed six of seven and require-both-sides skipped them. The loss is entirely **entry #7**, which got through only because `_check_credit_gate` read a measured credit of *exactly zero* as "estimation failed" and took the laxer MKT-010 fallback — bypassing MKT-029 and MKT-048. It bought both longs, could not sell either short, and GUARD-FLOOR unwound it. **The safety net held end to end** (flat, HIGH alert fired). Fixed `019f188`, not yet deployed. Dates to v1.5.0 — not a regression. |
| **2026-09-16** (Wed) | **no trades — FOMC** | B/A/C correctly skipped the announcement day. G (undefined risk) did NOT and is the outlier — see §A-bis-2. |
| **2026-09-15** (Tue) | **+$1,344.30** · 6e/0s | Recovered most of Monday's −$1,756. Zero stops. **F recorded its first entry ever** (fix verified). POS-003 resolver still untested — no stops means nothing vanished — but the day showed **8 strikes carrying multiple legs**, one with FOUR, so the overlap is far more common than Sep-11 suggested. |
| **2026-09-14** (Mon) | **−$1,756** · 4e/2s | **First real test of the halt criteria — they held** (H1 −$2,800 limit, H3 3-stop limit; actual −$1,756 / 2 stops). A call-side trend day: SPX 7592→7647.93→close 7619.40, and *every* stop on *every* variant was call-side. B's stopped entries kept their surviving PUT-side credit, which is why the booked figure beat the −$1,890 projection. C lost MORE (−$2,791.60) on half the entries — first clean evidence **B's wider strikes beat C's tighter ones on a trend day**. The merged 7555 puts settled cleanly. **GEX veto cost money a 2nd time** (vetoed short call 7650 vs day high 7647.93 — survived by 2.07pt). |
| **2026-09-11** (Fri) | **+$883.40** · 3e/0s | First passively-priced session; all expired worthless. A 25.8pt range is the EASY case for resting orders — treat as best-case. Also the day the POS-003 collision left a 7-lot untracked for 6h (found 09-12). |

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
