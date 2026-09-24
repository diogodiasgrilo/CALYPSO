# Strategy H (0DTE Long Strangle) — Go-Live Scope & Audit

**Date:** 2026-09-23 · **Branch:** `hydra-ibkr-standalone` · **Playbook:** Step 10 of
[`NEW_STRATEGY_PLAYBOOK.md`](../NEW_STRATEGY_PLAYBOOK.md)
**Spec:** [`LONG_STRANGLE_STRATEGY_SPECIFICATION.md`](../LONG_STRANGLE_STRATEGY_SPECIFICATION.md)
**Scope audited:** what would have to be true to take `LongStrangleStrategy` (variant `h`,
0DTE SPX long strangle) out of its dry-run lock on the shared IBKR paper account, alongside
**live-paper variant B**, through the one `calypso-broker` OAuth session.

---

## 0. VERDICT — **NO-GO**, and the gap is larger than "it needs a live path"

**Strategy H has never executed a single tick.** Verified on the VM 2026-09-23 08:29 ET:
`hydra_variant_h` is not in `/etc/systemd/system/`, `/opt/calypso/data/variant_h` does not exist,
and `systemctl is-active hydra_variant_h` returns `inactive`. There are zero observations of this
strategy — not a degraded record, not a short one, **none**.

That single fact dominates every other consideration, and it makes H's NO-GO different in kind from
variant D's. D's audit found a strategy whose selling point was a mid-pricing artifact. H's finding
is simpler: **there is nothing yet to evaluate.**

Four reasons, each independently sufficient:

1. **There is no real-order path, deliberately.** `_execute_entry` raises `ConfigError` rather than
   inheriting the base's four-leg iron-condor placement — which would SELL two short legs H does not
   have and has never sized for. `_close_long_strangle` raises outside dry-run. `__init__` raises on
   a non-dry-run construction before `super().__init__` touches broker I/O. Three independent locks,
   all tested. Flipping `dry_run=false` does not enable trading; it raises.

2. **The edge is unvalidated and the source's claims do not survive arithmetic.** 80% winners at
   +50–100% against losers at −100% implies roughly **+40% expected per trade**, which would be the
   best documented edge in retail options. A long strangle is also the *hardest* structure to win 80%
   of the time — finishing beyond the expected move is a ~32%-probability event by construction. The
   only reconciliation is taking profits very early on small gamma moves, which the source never
   quantifies. **The strategy was built to measure this, not because it is believed** (spec §1), and
   the measurement has not started.

3. **Three Step-3 assumptions remain unverified against live data.** The straddle-as-expected-move
   proxy (which systematically *overstates* the 1-SD move, ~0.85× being the usual correction, and no
   correction is applied); the IV-percentile filter, for which **nothing in this repo stores an
   honest input** — `market_ticks` keeps VIX, a 30-day *index* vol, not the IV of the 0DTE options
   being bought; and the 35% skew tolerance, which the source never quantifies and which is
   explicitly a guess. The read-only VM probe that settles these
   (`scripts/probe_long_strangle_data.py`) has not been run.

4. **The expected-move source is a config value that selects which strategy runs.** `straddle`
   (~22pt on 2026-09-22) and `vix` (~71pt on the same session) disagree by roughly **3×**, and the
   expected move *is* the strike choice. Until the probe answers which one to use, H does not have
   one identity — it has two, and only config picks between them.

### What is genuinely strong, and should not be lost in the NO-GO

The verdict is about evidence, not construction. H is, in several respects, the **safest** strategy
in this fleet:

| | |
|---|---|
| **Max loss is bounded by construction** | The debit paid, known before the position opens. No GUARD-FLOOR, no %-of-width stop, no MKT-046 anti-spike, no buffer decay — there is nothing to stop out of. |
| **Naked-short exposure is structurally impossible** | A partial fill leaves a LONG option, never an unhedged short. **The entry-execution failure that cost variant B −$159.50 on 2026-09-22 cannot occur in this structure.** |
| **It cannot pollute the shared record** | Its own `long_strangle.db`, four `ls_*` tables, no credit column anywhere (asserted by test). The shared `backtesting.db` schema version is never bumped. |
| **Its broker load is bounded** | 7 round-trips per entry on the straddle path, 4 on the VIX path — measured, not estimated (Step 9). |

---

## 1. Risk register

Rated by what would actually go wrong, not by how alarming it sounds.

### A. Blocking — must be resolved before any GO conversation

| # | Risk | Evidence | Effort |
|---|---|---|---|
| **H-1** | **Zero observations.** The strategy has never run. | VM verified 2026-09-23: no unit, no data dir, inactive. | — (time) |
| **H-2** | **No real-order path.** Three deliberate locks. | `_execute_entry`, `_close_long_strangle`, `__init__` all raise; tested. | **L** |
| **H-3** | **Edge unproven, and implausible as claimed.** | Spec §1: the source's numbers imply ~+40%/trade. | — (data) |
| **H-4** | **The expected-move source is undecided**, and it selects which strategy runs (22pt vs 71pt). | Step 3 offline half; probe outstanding. | **S** (config) |

### B. Material — would distort the measurement H exists to produce

| # | Risk | Note |
|---|---|---|
| **H-5** | The IV-percentile filter has **no honest input**. It ships disabled and **fails closed** — enabling it without naming a series skips every entry loudly rather than passing everything silently. A VIX percentile is accepted only when named as such, and every skip reason it writes carries `NOT an option-IV percentile`. |
| **H-6** | The 35% skew tolerance is a **guess**. Too tight and it vetoes ordinary index put skew; too loose and it admits a directional position wearing two legs. |
| **H-7** | The straddle proxy **overstates** the 1-SD move, so strikes sit further OTM than a true 1-SD strangle. No correction applied — inventing a fudge factor before the probe would be fitting to nothing. |
| **H-8** | **RESOLVED 2026-09-23 — the whipsaw gate and the FOMC T+1 blackout are now OFF for H.** Both are inherited from the premium-SELLING family and both contradict a long-gamma strategy: whipsaw skips when the intraday range is wide, and a wide range is the day H exists for; T+1 skips the day after a Fed announcement, frequently a big-move day. The source specifies neither. They were briefly kept "so the gating matches the fleet and the data is comparable" — **that was wrong: comparability is worth nothing if the thing being compared has had its thesis filtered out.** The gate calls remain (logic stays single-sourced); both are one config value from returning. |
| **H-9** | **Hold-to-settlement forfeits remaining extrinsic.** Declined the EOD flatten on purpose (Step 9). At 0DTE the amount is small and SPXW is cash-settled, so the cost is variance rather than expectancy — but it is a cost, and the dry run can overturn the choice. |

### C. Closed by construction — recorded so they are not re-litigated

| # | Risk | Why it is closed |
|---|---|---|
| **H-10** | Naked short after a partial fill | Structurally impossible — both legs are long. |
| **H-11** | Stop mis-fires / stop never fires | There is no stop. Both side stops are set unreachable, deliberately, and `_validate_pnl_sanity` validates the LONG legs only (G's S-CRIT-1, mirrored). |
| **H-12** | Debit recorded as a credit in shared tables | Isolated DB; `_record_entry_to_db` overridden so no inherited call site can leak one in. |
| **H-13** | Shared-broker latency harming live B | Bounded to 7/4 round-trips per entry, measured (Step 9). |
| **H-14** | A worthless expiry booked as break-even | `_settlement_booked_pnl` overridden to `intrinsic − debit` per leg (Step 5). |
| **H-15** | A restart losing the cost basis | Recovered from H's own `ls_entries`; a missing row is CRITICAL and unconverted, never guessed (Step 5). |

---

## 2. The next step is NOT a flip — it is to run the dry run at all

This is the most important sentence in the document, and it is where H's plan legitimately departs
from the playbook's Step 10 template.

The playbook asks Step 10 for three artifacts: a scope+audit (this document), an **MVL plan** for a
first live phase with the riskiest mechanic stripped, and a **go-live runbook**. For variant D those
made sense — D had been running dry-run for months and the question was genuinely *how* to go live.

**H has zero observations.** An MVL plan written today would be fiction: there is no measured
behaviour to strip a mechanic from, no fill data to phase, and no basis for choosing a first live
size. A go-live runbook written today would document a flip nobody can responsibly plan, and its
existence would imply a readiness that does not exist. Both are therefore **deferred with stated
preconditions** rather than written as placeholders — see §4.

### The actual next phase: install H in dry-run and let it observe

H's stated purpose is not profit. It is to **measure whether long gamma hedges the short-gamma
book's bad days** — a question the fleet cannot currently answer and which matters more as real money
approaches. Two live sessions frame it:

| session | SPX | B (short gamma) | a long strangle would have |
|---|---|---|---|
| **2026-09-21** | +1.1% | **−$441/contract**, worst live-era session | profited on the move |
| **2026-09-22** | 20pt range, VIX 14.4 | +$7.61/contract | lost its debit |

That deliverable needs H **running**, not live. Running it costs nothing: it places no orders, writes
only to its own database, and its broker load is bounded.

**Preconditions to install (all offline, all currently satisfiable):**

1. The market-hours probe has run and its answers are written into the config —
   specifically `expected_move_source` (H-4) and whether the IV filter has an input (H-5).
2. `deploy/hydra_variant_h.service` installed with `HYDRA_VARIANT_ID=h`; config ships
   `dry_run: true` (it does).
3. `alerts.enabled: false` (it is) so H cannot pollute the live seat's alert channel.
4. `api_pacing_multiplier: 2.5` (it is) — the highest in the fleet, on purpose.

---

## 3. Go-live readiness gate — H-specific

Replaces the 0DTE-credit-shaped checklist, which asks questions H cannot answer (credit received,
spread width, stop level). **All must be objectively true before the dry-run lock comes off.**

- [ ] **HG-1 — It has run.** ≥ 20 traded sessions of dry-run record in `long_strangle.db`, with the
      `em_source` constant across them (a mid-window config change splits the sample into two
      strategies).
- [ ] **HG-2 — The edge is measured, not assumed.** A win rate and a payoff ratio computed from
      `ls_exits`, stated with n and its date range, and compared against the source's claim rather
      than to itself.
- [ ] **HG-3 — The peak-versus-exit gap is quantified.** `peak_minus_exit_pct` across the sample. If
      positions routinely reach +50% and exit well below it, the exit rule is wrong and no amount of
      live sizing fixes that.
- [ ] **HG-4 — The hedge thesis has an answer.** H's daily P&L correlated against B's over the same
      sessions. **This is the deliverable.** A negative correlation is the result that would justify
      H existing; a positive one is a valid and useful NO.
- [ ] **HG-5 — A real-order path exists and is smoke-tested** open→close on paper (H-2).
- [ ] **HG-6 — The three Step-3 assumptions are settled** with recorded answers (H-4, H-5, H-6).
- [ ] **HG-7 — Fill realism.** The dry-run record re-priced at bid/ask rather than mid. A long
      strangle pays the ask on both legs at entry and receives the bid on both at exit — **four
      spread crossings**, against a debit of a few hundred dollars. This is the single most likely
      reason a mid-priced edge evaporates, and B's own entry-fill leak (~38% of its net) is the
      precedent.
- [ ] **HG-8 — Coexistence re-verified** with H actually running beside live B: per-variant orphan
      sweep, per-variant buying-power budget, and no measurable added latency on the shared broker.
- [ ] **HG-9 — A documented manual flip** (`scripts/flip_h_live.sh`), gated on broker `/health` + a
      same-ET-day smoke PASS. **Never an auto-flip.**
- [ ] **HG-10 — Docs current:** version history, CLAUDE.md operator section, `RUNBOOKS.md` flip +
      flatten entries.
- [ ] **HG-11 — SPY ASSIGNMENT IS HANDLED.** Added 2026-09-24 with the SPX→SPY switch, and it is
      the one genuinely new risk that switch introduces. **SPY options are AMERICAN and PHYSICALLY
      settled**: a leg that finishes ITM is auto-exercised into **100 shares per contract**, so at
      ~$765/share a single ITM contract becomes a **~$76,500** equity position, and at the ~10
      contracts the source's sizing rule now buys, **~$765,000** — appearing overnight, in an
      account that never chose to hold stock.
      **This does not affect the dry run**: `intrinsic − debit` remains the correct economic value
      of an ITM option at expiry, so every number H records today is right. It affects the day H
      places a real order, which is why it is a gate and not a bug.
      Closing it needs a decision, not just code — **close any ITM leg before the cash close**
      (the source never states this rule because his platform or his own hand does it, and it is a
      practical necessity of the instrument he actually uses), or explicitly accept assignment and
      size for it. The existing `_check_eod_flatten()` returns None **by design** for cash-settled
      SPX and is the natural hook. Until this is closed, **H must not be flipped**, and the
      SPX-era reasoning that "there is nothing to stop out of" no longer fully holds — there is
      nothing to *stop out of*, but there is now something to *be assigned*.

---

## 4. Deferred artifacts, and what unblocks each

| Artifact | Deferred because | Write it when |
|---|---|---|
| **MVL plan** | There is no measured behaviour to strip a mechanic from, no fill data to phase, no basis for a first live size. | HG-1 through HG-4 are satisfied. |
| **Go-live runbook** | Would document a flip nobody can responsibly plan, and its existence would imply readiness. | HG-5 exists (there is a real path to run) and HG-7 has a number. |

Until then, the code points **here**: the `ConfigError` lock messages, the module and class
docstrings, and the systemd unit's `Description=` all name this document rather than a runbook that
does not exist.

---

## 5. Halt / kill criteria for a (future) live H

Deliberately short, because most of the usual criteria do not apply — there is no stop to fail, no
naked short to run away, and the maximum loss is known before entry.

| # | Trigger | Action |
|---|---|---|
| **HH-1** | Cumulative debit deployed in a day exceeds `sizing_for_zero_max_loss` | Halt entries. The limit is the whole point of sizing-for-zero. |
| **HH-2** | An entry books with `total_debit == 0` | Halt and investigate. A free position is a pricing failure, not a gift. |
| **HH-3** | `em_source` changes without an operator decision recorded | Halt. The sample has silently become two strategies. |
| **HH-4** | Any `short_*` leg becomes non-zero on an H entry | Halt immediately. H sells nothing; a short leg means the wrong code path ran. |
| **HH-5** | Added latency on `calypso-broker` attributable to H | Stop `hydra_variant_h`. Live B has priority over a measurement variant, always. |

---

## Appendix — method

Single-reviewer adversarial pass rather than a multi-agent fan-out, per the operator's standing
preference. Every load-bearing claim in §0 was verified rather than asserted: the VM state by SSH
(2026-09-23 08:29 ET), the lock behaviour by test, the round-trip counts by counting mock calls on
both expected-move paths, and the arithmetic in reason 2 from the spec's own §1.

**A NO-GO here is a successful outcome, not a failed build.** The playbook says so explicitly, and
D's audit returned the same verdict correctly. H is finished as a *measurement instrument* and
unproven as a *strategy*, and those are different claims — conflating them is what this document
exists to prevent.
