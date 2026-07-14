"""
HYDRA 0DTE Trading Bot — IBKR Web API

Multi-Entry Iron Condors (SPX 0DTE) with credit gates, progressive OTM
tightening, and hold-to-expiry. Based on Tammy Chambless's MEIC strategy.

Broker: Interactive Brokers Web API (ibind OAuth 1.0a, no gateway).
Account: paper only on this branch. Saxo Bank has been removed end-to-end
— see Version History 2.0.0-rc.1 and `docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md`.

Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars.
The EMA signal (BULLISH/BEARISH/NEUTRAL) is logged and stored for analysis
but is informational only — base entries are full iron condors or put-only via MKT-011.

Credit Gate (MKT-011): Before placing orders, estimates credit from quotes.
MKT-029 graduated fallback for BOTH sides: -$0.05, -$0.10 (call floor $0.75, put floor $2.00).
MKT-035/MKT-038 call-only entries also use MKT-029 call floor ($0.75).
- Both sides viable: Proceed with full iron condor
- Call non-viable, put viable, VIX < 15.0: Place put-only entry (MKT-032/MKT-039 VIX gate)
- Call non-viable, put viable, VIX >= 15.0: Skip entry (no call hedge in volatile conditions)
- Put non-viable, call viable: Retry with tighter put strikes (5pt closer, max 2 retries), then call-only entry (MKT-040, v1.15.1)
- Both non-viable: Skip entry entirely

Conditional Entry Trigger (MKT-035 / Upday-035 / Downday-035):
- Base-entry down-day call-only DISABLED (2026-04-19, base_entry_downday_callonly_pct=null — negative EV in A/B sweep)
- Conditional entry E6 (14:00): fires as put-only when SPX rises >= 0.25% above session open (Upday-035)
  OR call-only when SPX drops >= 0.25% below session open (Downday-035, deployed 2026-04-19)
  Stop = credit + side_stop_buffer (see Option B per-VIX-regime values below)
- E7: DISABLED

Stop Buffers (Option B per-VIX-regime, deployed 2026-04-27):
- Global fallback: call_stop_buffer $0.75, put_stop_buffer $1.75
- Zone 0 (VIX<18) and Zone 3 (VIX>=28): null — fall back to global values
- Zone 1 (VIX 18-22): call $1.50, put $2.50 (wider both — calm regime)
- Zone 2 (VIX 22-28): call $1.00, put $1.50 (wider call, TIGHTER put — stress regime)
- See docs/HYDRA_BUFFER_OPTIMIZATION.md for the 28-day Saxo study + forward-looking review triggers

Version History:
- Data-integrity + dashboard-plumbing fixes (2026-07-14, from the 07-13 audit).
  (1) `_record_stop_to_db` treated a RESOLVED close-cost of 0.0 (a worthless close
  = full credit kept) as "missing" via a falsy-0 guard, booking the trigger-level
  placeholder -(stop-credit) into `trade_stops.net_pnl` instead of +credit
  (variant B's 07-13 E3 recorded -1500, not +500). Now only None (a fill never
  captured) falls back to the placeholder; the two stop callers map their unknown
  0.0 → None. RECORD-ONLY — realized_pnl / cumulative P&L were always correct
  (this is a reporting column). +4 tests (test_early_close_pnl_signs). (2) HOMER's
  Sheets→DB back-fill fabricated PHANTOM trade_entries/trade_stops rows in variant
  A's MAIN backtesting.db for slots A SKIPPED — the Sheets reflect the LIVE variant
  post-2026-06-02 pivot, and INSERT OR IGNORE can't dedup a skipped slot A never
  wrote. `db_manager` now drops any back-fill row whose (date,entry_number) is in
  the target DB's skipped_entries (no-op on the live variant's own DB). +5 tests
  (test_homer_skip_contamination). (3) Strategy E's SPY underlying isn't read via
  the index (sec_type=IND) path, so its vestigial IC daily_summaries had
  spx_open/high=0.0; E now backfills that row's OHLC from its own recorded SPY
  market_ticks (new read-only DataRecorder.get_spx_ohlc_for_date + an E-only
  `_record_daily_summary_to_db` override — strict no-op for A/B/C/D). +6 tests
  (test_e_daily_summary_ohlc). Companion dashboard fix (commit 4b3d6a0): History
  day-detail entries/stops scope to the picked variant via a shared reader_for()
  instead of always reading primary-C. Full suite: 1918 passed.
- Stale-SPX settlement guard (2026-07-07). A post-close RESTART can re-fetch a
  stale, non-zero current_price — variant C on 07-06 held 7420.22 (a PRIOR-day
  value, ~1.6% below the real 7537.86 recorded close). The daily-summary +
  Brandon-overlay settlement then booked against it → a phantom -$6,037 loss
  (the overlays all settled as total losses vs the wrong low SPX; the RECONCILE
  guard flagged the drift). _resolve_spx_close() only guarded the after-hours-
  decay-to-0 case, not a non-zero-but-stale value. Fix: it now cross-checks the
  clean on-disk recorded intraday close (market_ticks survives a restart) and,
  when live current_price diverges >1%, trusts the recorded close; the Brandon
  overlay settlement (log_daily_summary) routes through it instead of raw
  current_price. Operational: don't restart C/B while settlement is still
  pending. +5 tests (test_aud5_fixes).
- Brandon hedge-legs variant isolation (2026-07-07). ROOT of the 07-06 phantom:
  _brandon_resolve_hedge_state_path used _PROJECT_DATA_DIR (the SHARED base data/
  dir), so Brandon variants B and C both wrote data/brandon_hedge_legs.json and
  clobbered each other. On 07-06 variant C restarted and loaded variant B's 6
  overlays from that shared file (the date matched), settling them onto C's day
  total — the "no matching daily_state entry" E3/E4/E6/E7 orphans that, with the
  stale-SPX settle, produced C's phantom -$6,037. Fix: use the variant-aware
  DATA_DIR (data/variant_<id>/) so each Brandon variant is isolated (still safe
  pre-super() — DATA_DIR is an env-derived module constant). +2 tests
  (test_brandon_strategy_integration); full suite 1898 passed. Deploy: at EOD
  after B/C settle today's overlays, restart B/C (they start fresh under the new
  per-variant path) + delete the now-orphaned shared data/brandon_hedge_legs.json.
- MKT-047 PER-SIDE OTM-skip + calendar per-entry P&L booking (2026-07-06). (1) The
  EOD-flatten OTM-skip was PER-ENTRY all-or-nothing — it only skipped an entry when
  EVERY alive short was >= the cushion, so one at-risk side force-closed the whole
  entry including a far-OTM sibling side. On 2026-07-06 variant C's short calls were
  ~18pt OTM (< cushion) while the short puts were 72-77pt OTM: both entries flattened
  in full, buying the worthless puts back for a needless debit + close commission and
  giving back ~$215 of a ~$215.60 would-be day (booked $105 realized − $104.65 comm =
  $0.35 net). Fix: `_eod_flatten_can_skip_side(entry, side)` gates each side
  independently and `_close_entry_early(entry, skip_sides=...)` leaves the safe
  side(s) UNTOUCHED to ride to free worthless expiry, flattening only the at-risk
  side — the pre-expiry tail protection is preserved where it matters. The
  whole-entry `_eod_flatten_can_skip` fast-path is retained. Threshold default
  lowered 25 → 20pt to match the 84-day final-10-min max move (18.4pt). Shared by
  A/B/C (one strategy.py path). (2) Calendar per-entry attribution: `_dc_close_calendar`
  (CAL-STOP + CAL-EOD-CLOSE), `_dc_settle_entry` (D), and `_spy_dc_partial_close` +
  `_dc_settle_entry` (E) booked realized P&L straight to `daily_state.total_realized_pnl`,
  leaving `entry.realized_pnl` at 0.0 and tripping the settlement RECONCILE drift guard
  (2026-07-06 D: sum $0 != total −$340). All four now route through
  `_book_realized_pnl(entry=...)`; aggregate unchanged, per-entry populated (unblocks
  slot_edge on the calendars). +14 tests (per-side gate/exec/leg-exclusion + calendar
  per-entry); full suite 1888 passed.
- MKT-047 OTM-skip + dashboard EOD-flatten labeling (2026-06-25). (1) The EOD
  safety flatten now LEAVES a 0DTE entry whose every alive short is >= 25pt OTM
  (config eod_flatten.skip_otm_pts) to cash-settle worthless for FREE, instead of
  paying to buy it back in the un-closable window. Data-derived: over 84 days SPX
  never moved >= 20pt in the final 10 min (max 18.4), so a >= 25pt-OTM SPXW short
  (cash-settled, no assignment) has ~0% settlement risk while closing it burns the
  close cost + commission — 2026-06-25 variant C E#2 gave back ~$43 of a $210
  credit closing a 53pt-OTM put. A skipped entry stays fully monitored (the stop
  net is unchanged) and is booked solely by settlement, which books the rare ITM
  tail as a real loss (audited PASS). An entry with ANY alive short within the
  cushion is still fully closed; skip_otm_pts=0 restores the always-close behavior.
  (2) Dashboard: an EOD-flatten was mislabeled as "Expired"/a red stop dot (the
  early-close reuses *_side_expired); now rendered as a distinct "Flattened"
  status with stop markers/realized-P&L gated on early_closed, and the backend
  stamps close_reason="EOD_FLATTEN". DISPLAY-only. +12 tests (eod-flatten 26 incl.
  10 OTM-skip; dashboard); full suite 1847 passed.
- Hold-if-safe cushion 25 → 50pt (2026-06-23, Brandon B/C). Data-derived: 84
  trading days of SPX 1-min paths show a short held to expiry from the final hour
  is TOUCHED 26.5% of the time at a 25pt cushion but only ~4-5% at 50pt. Riding
  to expiry instead of taking the ~80% TP gains only ~20% of a thin credit
  (~$12/contract) against a near-max-loss stop on a breach, so it is +EV only
  below a ~3-7% reversal rate — making the old 25pt decisively −EV and 50pt the
  break-even threshold. Code default raised (B/C leave the knob unset); the
  time-window widening, a shadow logger, and VIX-scaling were all considered and
  rejected (see docs/HYDRA_HOLD_IF_SAFE_ANALYSIS.md). +1 regression-guard test.
- MKT-049 fail-CLOSED + POS-003 recent-close orphan suppression (2026-06-23).
  Two live-C fixes after a market-hours review. (1) MKT-049 take-profit gate
  SUPERSEDES its 2026-06-22 fail-OPEN behavior: it now FAILS CLOSED. The
  net-of-cost gate (brandon/strategy.py _brandon_real_close_capture) returns None
  ONLY when a SHORT leg (the cost driver) is unquoted/crossed; a worthless /
  unquoted / crossed LONG leg is priced as $0 recovery and the side is still
  computed from the short — so one dead long leg can't blind the gate to a
  still-wide short side. And when real_capture is None the gate now HOLDS the TP
  (protected by the GEX breach-exit + credit+buffer stop + expiry) instead of
  closing on the optimistic mid. Why: on 2026-06-23 C E#1/E#2 fired TPs logging
  ~88% MID capture but only ~46-50% REAL (E#2's call side closed at a −$30 loss)
  because a worthless long_call went unquoted near expiry → the old fail-OPEN let
  the mid TP through. The B/C-shared Brandon class means both variants get this.
  (2) POS-003 hourly reconciliation no longer false-CRITICALs on the bot's OWN
  just-closed legs while IBKR's positions feed lags them out: every successful
  real close records its conid (_note_recent_close in base_strategy's
  _close_position_with_retry chokepoint), and the orphan sweep
  (_recon_suppress_recent_closes) suppresses orphans at conids closed within
  RECON_CLOSE_SETTLE_GRACE_S (300s) — a close that PERSISTS past the grace (the
  close didn't take) is still surfaced. The settle-confirm window was also bumped
  30→60s. Dry-run variants never populate the recent-close set (the dry-run
  early-return precedes the hook) and never reconcile. 2026-06-23: a TP-closed
  leg lingered 83s, past the old 30s window → spurious CRITICAL + Telegram on C.
  +13 tests (test_mkt049_tp_net_of_cost.py 17, test_reconciliation_settle_delay.py
  +5); full suite 1817 passed.
- Calendar (D/E) dry-run EDGE reader + settle-commission fix (2026-06-23). Built
  the tool that answers the MVL-D audit's gating question ("V1 — edge sanity")
  from D/E's forward dry-run record alone, so the build-or-don't-build decision
  for MVL-D becomes data-gated instead of a guess: is a debit double calendar +
  20%-debit stop NON-NEGATIVE EV, NET of commission, over a meaningful window?
  New pure-stdlib module bots/hydra/dc_edge.py (+ thin CLI
  scripts/analyze_calendar_edge.py) reads a variant's isolated dc_calendar.db
  (dc_outcomes) and renders a verdict. Audit-hardened (2 independent adversarial
  passes): (1) TRANSFORM SEGMENTATION — transformed outcomes are excluded from
  the verdict (their dry-run P&L is a mid-pricing artifact that won't survive
  real fills, audit §0.3); detection keys on per-outcome transform_credit>0, NOT
  a join to dc_transformations (whose `date` is the transform day, not the entry
  day, and would mis-match a multi-day transform). (2) COMMISSION-NET — the
  verdict gates on realized_pnl − 2×close_commission (realized_pnl is booked
  gross; the round trip is 2× the recorded close side). (3) Student-t (not z) CI,
  gated on the usable return-series size, with a degenerate-sample guard + a
  non-independence caveat (overlapping multi-day trades). (4) "DB not found" is
  distinguished from "0 outcomes." Verdict tiers: INSUFFICIENT_DATA (<10
  trustworthy trades — TODAY's state: n=1) → PRELIMINARY_* (<30) → EDGE_POSITIVE
  / EDGE_NEGATIVE / INCONCLUSIVE / DEGENERATE_SAMPLE. The settle-path fix: D's +
  E's `_dc_settle_entry` leftover-close backstop now stamps entry.close_commission
  (4 legs, mirroring _dc_close_calendar) so that rare path records commission
  instead of $0 — otherwise the reader would over-state the edge on it. DRY-RUN
  ONLY (D/E remain dry-run-locked); recording-only change, no trading behavior
  altered. +30 tests (tests/test_dc_edge.py); full calendar/dc set 188 passed.
- One-sided dry-run mark fix (2026-06-23). Put-only / call-only DRY-RUN entries
  showed a deep-negative P&L even as SPX moved AWAY from the short (so they
  should have been profitable — the reported variant-B bug). Root cause:
  _simulate_put_spread_only / _simulate_call_spread_only never populated the leg
  conids, so the heartbeat couldn't fetch real quotes and fell back to
  _simulate_hydra_entry_prices — a moneyness-BLIND model whose units bug
  (credit/100 instead of credit/(70×contracts)) started the spread VALUE at ~7×
  the credit, i.e. an instant deep-negative P&L that then only time-decayed
  (nothing to do with SPX direction). Fix 1: the one-sided sims now populate the
  active side's conids + the REAL estimated credit (mirror _simulate_entry), so
  the heartbeat marks from real quotes exactly like a full IC. Fix 2: the
  fallback's initial leg prices now make the spread value start at the credit
  (not 7×) — for the one-sided AND the full-IC branch (the same units bug lived
  in `total_credit/200`; it surfaced as a ~−$1378 phantom on a full IC whose
  conids were dropped by a restart-recovery). Fix 3 (the proper one): the
  heartbeat now RE-RESOLVES missing leg conids from the persisted strikes
  (`_repopulate_dry_conids`), so a dry-run entry that came back conid-less after
  a restart marks from REAL quotes again instead of the crude fallback — the
  fallback is now only a genuine last resort. Fix 4 (the shared-account leak):
  the JOURNAL per-entry P&L (`_get_broker_pnl_for_entry`) summed BROKER positions
  whose conid matched the entry's legs — but a dry-run bot holds NO real
  positions; the SHARED IBKR account carries the LIVE variant's (C's) positions.
  Once Fix 3 re-resolved B's conids to strikes overlapping C's, B's journal P&L
  picked up C's real LOSING positions (Entry #3 read −$1452 against a $0 spread
  value). Dry-run now returns the SIMULATED mark (`entry.unrealized_pnl`) and
  never touches the shared account; live (C) keeps the real broker lookup.
  Verified live post-deploy: B's four entries read +$125 / +$450 / +$400 / +$125,
  each satisfying P&L = credit − (call_SV + put_SV). DRY-RUN ONLY — variant C
  (live) places real orders with real conids and was never affected; the bug only
  polluted B's dry-run head-to-head display. Tests:
  tests/test_one_sided_dryrun_mark.py (12).
- Backlog polish batch (2026-06-22). (item 2) CalendarStrategyBase now logs a
  neutral [CAL-*] prefix instead of [DCTM-*] — the base runs for BOTH D and E, so
  the DCTM (DC Time Machine = D) tag mis-attributed E's calendar plumbing to D;
  D's own double_calendar_strategy.py keeps its legitimate [DCTM-*] tags, nothing
  parses the tags. (item 6) Per-command Telegram variant selectors: /status,
  /snapshot and /stops accept an optional variant token (e.g. `/status c`) and
  render that NON-primary variant from its own state file via one unified view
  (reuses _load_variant_state + _build_variant_summary); D/E point at /calendars,
  A falls through to the full view. The dashboard-side items from the same review
  (per-strategy History/Analytics, ConfigDelta baseline) and the decision to keep
  the non-primary WebSocket on polling are tracked in docs/NEXT_STEPS.md §5.
  Tests: tests/test_telegram_variant_selector.py (10).
- MKT-049 net-of-cost take-profit gate (Brandon, 2026-06-22). The exit-side
  mirror of MKT-048. Brandon's take-profit fires on the MID mark
  (entry.*_spread_value), but a thin 0DTE credit spread CLOSES at
  short_ask − long_bid, which can be many times the mid. On 2026-06-22 variant-C
  E#2 the mid said "SV $17.50 → 87.5% captured", but the real close cost was
  $105 (25% captured); after commission the $140 credit netted only +$2.80 — the
  TP gave back ~75% to slippage it never saw, when holding the comfortably-OTM
  put to expiry would have kept ~$140. Fix: _brandon_real_close_capture recomputes
  the REAL net capture from live bid/ask (buy the short at ask, sell the long at
  bid) minus close commission, and _brandon_check_take_profit DEFERS the close
  when it's below brandon_tp_min_net_capture (default = the TP threshold, 0.80) —
  the short then rides to expiry (100%, no close cost), still backstopped by the
  GEX breach-exit + credit-buffer stop. FAIL-OPEN: a missing / crossed quote (or
  any data error) returns None → falls back to the mid decision, so a flaky quote
  never blocks a legitimate close. Gated by brandon_tp_net_of_cost_gate_enabled
  (default true). Tests: tests/test_mkt049_tp_net_of_cost.py (13).
- MKT-048 credit-gate fillability veto (2026-06-22). The 2026-06-22 variant-C
  Entry#1 failed all 3 retries at leg 3 ("Short Call order failed", a HIGH
  entry-window watchdog alert) while variant B "worked" — but only because B is
  dry_run_shadow (simulated fills never test fillability). Root cause: MKT-011
  decides side viability on MID prices, yet an IC side fills with the short at
  its BID and the protective long at ~its MID (its buy-limit starts at mid), so
  a side can clear the mid threshold while its fillable credit
  (short_bid − long_mid) is a debit. The placement net-credit floor
  (_sell_credit_floor_price) then correctly refuses to leg into a debit — but
  only AFTER buying the protective long, so C bled the longs across 3 retries
  and ended put-only anyway. Fix: _estimate_entry_credit_ib now stashes each
  side's per-share fillable credit on the entry (in the quote read it already
  does — no extra IBKR calls), and _check_credit_gate vetoes a still-viable but
  unfillable side UP FRONT so the existing one-sided routing books it cleanly
  (put-only / call-only / skip) with zero retries and no false alarm. FAIL-OPEN
  (vetoes only on a debit the data CONFIRMS; a missing/crossed quote never
  vetoes), gated by mkt011_fillability_gate_enabled (default true). Hardened by
  an independent adversarial review: predict the long cost with its MID not its
  ASK (long fills ≤ mid, so ASK over-vetoed fillable spreads — F1); _sane_bid
  drops crossed books so a nonsense quote can't veto (F2); the per-share stash
  is penny-rounded so a float-subtraction artefact can't trip `< floor` (F3);
  and a tightened-put call-only retry re-checks fillability before committing
  to a full IC (F4). Correct for A/B/C — only ever triggers on a side that
  would have failed at leg 3/4 anyway. Tests:
  tests/test_mkt048_fillability_gate.py (17).
- E Friday-only expiries — the ACTUAL root cause of E never entering (2026-06-18).
  A live broker probe (post strike-snap) showed E STILL skipped: get_option_chain
  returns a generic, non-expiry-validated strike list for ANY weekday, so the old
  prefer_friday=False picked an UNLISTED Monday/Wednesday short (e.g. 07-20) whose
  conids never resolve (only Friday/monthly SPY expiries exist at 30+ DTE). Fix:
  _pick_expiries filters candidates to Fridays (weekday==FRIDAY; the monthly 3rd
  Friday is included) + prefer_friday=True, so both legs resolve. The strike-snap
  below is kept as a secondary guard for monthly($5)-vs-weekly($1) grid mismatches.
  Lesson (again): a LIVE entry-path probe is mandatory — unit tests with mocked
  chains can't see that get_option_chain lists strikes the conid lookup won't qualify.
- E strike-snap to the short∩long grid + shared-SPX dashboard chart (2026-06-18).
  (1) Strategy E never entered because it rounded the ±EM target to a fixed $1 grid
  then required that exact strike on BOTH expiries — but SPY monthlies use a $5
  far-OTM grid vs the weeklies' $1, so it was unlisted on one leg and skipped every
  day. Fix: new CalendarStrategyBase._dc_strikes_for_expiry lists each expiry's
  strikes; E._calculate_strikes now snaps each EM target to the NEAREST strike in
  the short∩long INTERSECTION (a listed-grid strike near ±EM beats a perfect-EM
  strike that doesn't exist). (2) Per-strategy SPX candle charts were sparse/dotted
  (esp. on no-trade days) because they read each variant's THROTTLED DB (only A
  derives market_ohlc_1min; B/C ticks are api_pacing-thinned). Fix: the dashboard
  snapshot now reads SPX bars from the SHARED main market-data DB (SPX is the same
  index for all), so every view gets the dense series; entry markers stay
  per-strategy. 4 E-strike tests + 2 snapshot-mirror tests updated; suite 1726.
- MKT-047 gate — never EOD-flatten the multi-day calendars (2026-06-18). MKT-047
  (the 0DTE expiry-window EOD flatten) is inherited by the calendar variants D/E
  via the shared `_handle_monitoring`, and on 06-18 it WRONGLY force-closed D's
  multi-day calendar at 15:50 ($-9.20) and left its sidecar stale. On a day D
  transforms, it would flatten the risk-free IC the same afternoon and destroy the
  multi-day hold. Fix: `_check_eod_flatten` returns None when
  `requires_protective_wings` is False (the calendars set it False; the 0DTE ICs
  keep it True) — D/E manage their own EOD via `_dc_manage_calendar`. 1 new test
  (test_eod_flatten_safety.py::test_calendar_strategy_never_flattens); suite 1724.
- Calendar dry-run REALISM — spread-crossing fill model for D + E (2026-06-17).
  The calendar sim priced EVERY simulated fill at the bid/ask MID (entry debit,
  transform credit, mark-to-market, liquidation), which made D's transform look
  risk-free far cheaper + faster than crossing 4–6 real spreads allows (the
  "+$845 locked in 47 min" artifact). New CalendarStrategyBase._dc_fill_price
  crosses the spread — BUY toward ask, SELL toward bid — scaled by
  config strategy.dry_run_fill_model.aggressiveness (default 1.0 = full touch,
  the honest worst case for a marketable order; 0.0 = old mid; + an optional
  extra_slippage_per_leg). Applied at: entry debit (_dc_simulate_entry, longs@ask
  / shorts@bid → HIGHER debit), the live mark + every value-driven decision
  (_dc_refresh_marks now marks at the LIQUIDATION fill — longs@bid / shorts@ask →
  honest unrealized_pnl, profit-take ladder, %-stop, transform trigger, and the
  daily close P&L), and D's transform credit (_dc_attempt_transform sells longs@bid
  / buys wings@ask → LOWER credit, so the risk-free gate is honest and can now
  DEFER when the spread eats the cushion). Covers D AND E (shared base). 11 new
  tests (tests/test_calendar_fill_model.py); suite 1723 passed/15 skipped. Note:
  E still can't ENTER until its EM-strike-on-both-expiries selection is fixed.
- IBKR-audit #5b — settlement SPX fallback so an ITM-settled short isn't mis-booked
  as worthless on a post-close restart (2026-06-17). Sibling bug to MKT-047: after C's
  stop-close FAILED (leaving the ITM put unbooked), a post-close RESTART ran settlement
  before the live SPX snapshot warmed up → _settlement_spx_level returned None →
  _settlement_booked_pnl assumed worthless → the ~$3.1k max-loss put was booked as a
  +$336.70 PROFIT (dashboard showed C as a winner). Fix: (1) persist last_spx_price in
  the state file + restore it into self.spx_price on recovery; (2) _settlement_spx_level
  now falls back to the last-known SPX (self.spx_price / current_price) BEFORE assuming
  worthless — only assumes worthless with no reference at all. Latent for every live IC
  (A/B unaffected today: A booked via stops on OTM-ending puts, B via dry-run stops; only
  C, the live broker, hit the stop-close-fail + restart combo). 9 new tests
  (tests/test_settlement_itm_spx_fallback.py); suite 1712 passed/15 skipped.
- MKT-047 — EOD safety flatten + near-expiry MARKET escalation (2026-06-17).
  Trading-safety fix for all 0DTE IC variants (A/B/C). Root cause: on the 06-17
  FOMC selloff, variant C's E#1 put breached its credit+buffer stop at 15:57 but
  EMERGENCY-001's marketable-limit closes "did not fill" and the 0DTE options
  then expired ("Order is already expired" ×5) → full put-spread max loss (~$3.1k).
  Two coupled fixes: (1) _check_eod_flatten/_execute_eod_flatten force-close every
  open 0DTE short at a cutoff (default 15:50 ET, 15:40 on FOMC announcement days)
  BEFORE the un-closable final-minutes window — a SAFETY exit, not profit-gated
  like MKT-018, reusing the MKT-018 leg-closer (books real P&L, B2 naked-short
  guard, Fix #81 worthless-long skip); idempotent per day; does NOT force
  DAILY_COMPLETE so a failed leg falls back to normal monitoring/expiry. (2)
  base_strategy._place_marketable_close escalates straight to a true MARKET order
  when within eod_flatten_market_minutes (default 6) of the actual close (handles
  early-close days via get_market_close_time) — a crossing limit chases-and-misses
  a fast tape near expiry. Config: strategy.eod_flatten {enabled(true), time_et,
  time_fomc_et, market_order_minutes}. Defaults ON for A/B/C; D/E (calendars)
  unaffected (no 0DTE expiry, getattr default 0 → no escalation). 17 new tests
  (tests/test_eod_flatten_safety.py); full suite 1703 passed/15 skipped.
- Strategy D — live mark for TRANSFORMED positions (2026-06-17). Observability-only;
  A/B/C byte-unchanged, no trading-decision change. A transformed (risk-free, held-to-
  expiry) calendar was never re-marked — _check_stop_losses only managed CALENDAR-phase
  entries — so its MTM (CalendarEntry.unrealized_pnl) stayed pinned at the transform-time
  leg prices (e.g. frozen at +$310 for 44+ min while SPX moved). Now _check_stop_losses
  refreshes the legs of TRANSFORMED entries each tick (no management action) and persists
  the sidecar, so the heartbeat + Telegram /calendars + dashboard DC card show a LIVE mark
  converging toward the locked floor (transform_credit − net_debit). Sidecar now carries
  unrealized_pnl + pnl_pct (output-only; deserialize ignores them); both readers
  (bots.hydra.dc_status + dashboard dc_reader) expose them; the DC card renders a "Live MTM"
  row. Costs 4 quote reads + 1 small sidecar write per tick while a transformed calendar is
  held (D's api_pacing_multiplier governs cadence). E unaffected (E never transforms).
- Strategy E (SPY double calendar) + strategy-grouping/naming redesign (2026-06-16).
  All dry-run / additive — A/B/C/D byte-unchanged. Feature branch.
  - New strategy E: SpyDoubleCalendarStrategy (registry "spy_double_calendar", "SPY Double
    Calendar"), a MANAGED SPY double calendar (net-debit call calendar above spot + put
    calendar below), expected-move strikes, low-IV entry gate (VIX proxy), laddered
    profit-taking, NO hard stop, never held to expiry. Dry-run-LOCKED. Sibling of D via a
    new shared base CalendarStrategyBase (D's calendar plumbing lifted verbatim — D stays
    byte-identical; its 135 tests pass with zero edits). Go-live gates (in the class
    docstring): SPY American early-assignment/dividend handling; multi-contract ladder
    net_debit scaling; true IV-rank gate; the shared-account coexistence MUST-FIXes.
  - Strategy taxonomy (shared/strategy_taxonomy.py): single source of truth keyed by the
    unchanged variant letter → display_name + comparability group (ic_0dte credit {a,b,c}
    / calendar_multiday debit {d,e}). main.py banner is taxonomy-driven and FAIL-STOPS if
    the runtime-resolved strategy class disagrees with the table. The alert-wire bot_name,
    the anti-spam fingerprint, and HOMER's Sheets anchors stay FROZEN.
  - Comms: alerts/Telegram/email show display names + group labels (additive payload
    fields; Cloud Function renders with bot_name fallback); /compare is group-scoped (bare
    = the 0DTE-IC group; /compare calendars = the calendar group via a calendar-native,
    no-IC-field renderer); /help regrouped by group; poller stays variant-A-only.
  - Dashboard (read-only): group-aware /api/strategies API + a debit-native DCDBReader
    (no IC-field leakage); a main-page strategy picker (header re-binds to the selection,
    no cross-strategy toasts), group comparison tabs (credit IC and debit calendar never
    share a P&L axis), and EOD auto-update (cumulative + end-of-day cards refresh at close,
    no manual reload). Full suite 1680 passed / 15 skipped.
- Strategy D hardening + dashboard put-only/metrics fixes (2026-06-16). D is dry-run
  (no real money). Triggered by C's GEX-routed put-only day + D's premature stop.
  Strategy D (double_calendar_strategy / DoubleCalendarStrategy), bot-side:
  - Premature-stop fix: _dc_close_calendar no longer RE-refreshes marks (it booked a
    different value than the noisy tick that triggered the close — a -20% trigger
    booked at -6.3%); the 20%-debit stop now requires the breach to PERSIST
    dc_stop_confirm_seconds (default 20s) before closing, clearing on recovery
    (MKT-046 analogue). Optional real-time quote gate (dc_require_realtime_quotes,
    default OFF — the persistence window is the primary defense).
  - Calendar-native observability (the inherited IC heartbeat showed 'Credit $0 /
    -469% cushion / SV ignoring the debit'): get_detailed_position_status override
    (phase, Kc/Kp, both expiries, debit, value, P&L %-of-debit);
    _calculate_capital_deployed override (sum of OPEN net_debit, was the $500 wing
    notional < the $1035 debit); _calculate_max_loss_with_stops/_catastrophic
    overrides (stop_pct×debit / full debit, not IC stop math).
  - Lifecycle/BP: concurrent-calendar cap (dc_max_concurrent, default 1) + per-
    variant BP budget (dc_max_deployed_debit, default $5000 — MUST-FIX #3, caps D's
    footprint on the shared account); calendar-aware get_monitoring_mode (vigilant
    near the stop/transform triggers and during a stop-confirm, vs the inherited
    always-normal ~12.5s). Multi-day realized-P&L was already correct (cost basis on
    the carried entry; realized booked at settlement) — the stale TODO was corrected
    to document it. Tested 15/15.
  Dashboard (read-only): EntryCard shows strikes for put-only/call-only entries (the
  short_call_strike>0 gate hid them); PerformanceMetrics gates annualized ratios
  behind >=20 trading days (a tiny sample made Sharpe=111 and Sortino/Calmar/PF/WL=INF);
  PositionHeatmap axis includes the live SPX (far-OTM put-only had spot off-chart).
  NOTE: C's tiny put-only credit on 06-16 ($70 vs $455 on 06-15) is NOT a bug — the
  GEX strike-adjuster skipped the call side (call short within 25pt of a negative-GEX
  accel-zone peak at 7565) → put-only; 06-15 had no accel wall near the call → full
  ICs. Side effect: put-only credit barely clears commission (~$16-18) — a strategy-
  tuning concern (consider a min-credit floor), not an error.
- Variant-C TP / reconciliation / alert hardening + dashboard observability (2026-06-15).
  Bot-side behavior changes (live on A/B/C after this restart):
  (1) Variant-aware alerts — AlertService bot_name is now HYDRA / HYDRA_B / HYDRA_C
      (was a generic "HYDRA" for all three), so every email/Telegram alert says WHICH
      variant fired and each variant gets its own dedup namespace (base_strategy reads
      the HYDRA_VARIANT_ID suffix). Cloud Function uses bot_name only as a label, so
      delivery is unaffected.
  (2) DataRecorder schema v11 — trade_stops gains exit_reason (stop_loss / take_profit /
      gex_breach / early_close); HydraStrategy._record_stop_to_db threads it (Brandon
      TP/breach tagged from close_reason). Additive + nullable migration. The dashboard
      Stops/Survival analytics now exclude profitable Brandon take-profit exits from
      "stops" (exit_reason, with a net_pnl<0 fallback for pre-v11 rows).
  (3) POS-003 reconciliation confirm-before-alarm — IBKR's positions endpoint lags fills
      ~20-40s, so a reconciliation racing a just-executed close read STALE quantities and
      fired a false CRITICAL "orphan" + HIGH "mismatch" (2026-06-15 variant C, 25s after
      E#2's TP). The FIRST detection now schedules a 30s settle re-check (non-blocking,
      _recon_recheck_at) and only alerts/acts on what persists — also stops
      _handle_position_discrepancies from marking a live leg stopped off a stale qty=0.
      Merge attribution was already correct (_expected_position_quantities sums per conid),
      so same-strike multi-entry positions reconcile by net quantity.
  (4) Brandon take-profit — worthless-leg fix + near-expiry hold-if-safe. The old guard
      skipped TP whenever a side's spread_value==0 (assumed "stale quote"); a genuinely
      worthless leg is also $0, so a worthless-leg IC could NEVER take profit (C E#1's
      7450 put rode to expiry instead). Now a $0 is trusted when the short is >=
      worthless_otm_pts OTM (default 20); and in the final hold_to_expiry_minutes
      (default 60) a comfortably-OTM IC (every live short >= hold_safe_cushion_pts,
      default 25) is held to expiry (keeps 100%, zero close cost) over an 80% TP that
      pays slippage + commission — the credit+buffer stop still backstops a reversal.
      Knobs under strategy.brandon.take_profit; pure helpers _tp_value_trustworthy /
      _tp_hold_to_expiry, tested.
  Read-only dashboard (no bot behavior): comparison-page per-side stop visibility +
  "cost"/"cushion" relabel + SIM vs PAPER-$ badge + distance-to-stop + per-contract
  leaderboard; main/history/analytics correctness (conditional PUT/CALL-ONLY badge,
  one-sided strikes, Brandon TP no longer shown as a red loss, cumulative-P&L color);
  chart-tick accessibility contrast/size.
- Strategy D — strike-selection latency + both-expiry SHOWSTOPPER fix (2026-06-15,
  dry-run-only, A/B/C byte-identical): a LIVE entry-path exercise on the VM broker
  (read-only, market hours — the audit that code review + unit tests could not do)
  found D's real entry took ~4 MINUTES and then SKIPPED anyway. Root causes: (1)
  _dc_delta_target_strike scanned greeks for up to ~40 COLD conids/side (each
  snapshot warmup ~2-4s) ≈ 95s/side; (2) _dc_pick_expiries' day-granular listed-
  filter added ~50s of per-candidate broker calls; (3) it picked the ~Δ strike on
  the SHORT expiry without checking the LONG lists it, so the long leg failed to
  resolve and the whole entry was discarded. A ~4-min broker burst would also
  saturate the ONE shared calypso-broker session and add latency to LIVE C. FIX
  (bounded delta-scan, user-chosen): _dc_pick_expiries is now PURE/fast (returns
  (short, [long candidates]) — no broker calls). _calculate_strikes centers a small
  strike window on the VIX-expected-move estimate (em_1sd × delta_otm_fraction ≈ 35Δ
  OTM distance), fetches each expiry's chain ONCE (one _read_option_chain per expiry
  returns both call+put maps over a combined window) and hands the maps to
  _dc_pick_delta_strike, which SEEDS at the both-expiry strike nearest the estimate
  and STEP-SEARCHES toward target Δ — |delta| is monotonic in OTM distance, so it
  reads ~1-4 cold greeks/side (capped at delta_max_reads, default 6) instead of the
  ~40-cold-scan (~95s/side) or the first bounded pass that still read 10/side (~67s).
  _calculate_strikes iterates the long candidates so a thin long expiry falls back to
  the next instead of skipping. The both-expiry intersection (short_map ∩ long_map)
  is the real gap-day guard (a strike unlisted on the long is structurally excluded),
  making the prior month-granular/ATM-only filter unnecessary (removed). New config
  knobs: delta_otm_fraction (0.40), delta_window (8), delta_max_reads (6). Expected
  broker work per long candidate: 2 chain reads + ~1-4 cold greeks/side (down from
  ~4 chain + ~10-40 greeks). Tests rewritten for the new signatures (seeded step-
  search, both-expiry intersection, long-candidate fallback); full suite 1553 passed.
  D remains dry-run-LOCKED + STOPPED/undeployed pending the live VM entry-path re-test
  (must confirm seconds-not-minutes + all 4 legs resolve + net_debit>0). Lesson: a
  live entry-path exercise is mandatory before trusting D — latency + real strike/
  expiry listing can't be seen statically.
- Strategy D — post-audit re-review fixes (2026-06-15, dry-run-only, A/B/C
  byte-identical): a 14-agent independently-verified re-review of the 3 post-audit
  commits found 0 critical / 0 high / 2 medium / 8 low (deduped to 3 real issues).
  Fixed: (MEDIUM) the expiry-listed filter was a NO-OP — get_option_chain is
  MONTH-granular (JUN26 covers all June expiries) so a gap weekday (Jun 29) still
  looked listed; _dc_expiry_is_listed is now DAY-granular (resolves one ATM conid
  at the exact expiry via _get_option_uic → qualify_option_strikes' maturityDate
  filter, the same path the legs resolve through), so gap days are genuinely
  excluded (the misleading get_option_chain-mock test was rewritten). (LOW)
  _dc_close_calendar now calls _save_state_to_disk (crash-window guard) so a stop/
  EOD close is persisted immediately — a crash before the next heartbeat can no
  longer re-adopt a CLOSED calendar as open from a stale sidecar (mirrors
  _dc_settle_due / _initiate_entry). (LOW) refreshed stale 'backtesting.db'
  docstrings (dc_recorder, dc_reader) + clarified that variant_d_backtesting_db is
  the market-tick DB while the calendar tables live in dc_calendar.db. The
  prior fail-safe (a gap-day pick already failing cleanly at leg resolution) meant
  none of these were trading-correctness or A/B/C risks. +1 test, full suite 1549.
- Strategy D — close the 3 deferred-LOW review findings (2026-06-15, dry-run-only,
  A/B/C byte-identical): (1) get_daily_summary OVERRIDDEN in D to zero the
  IC-credit-vertical breakdown (expired_credits/stop_loss_debits) and report
  realized P&L from total_realized_pnl — a settled CalendarEntry's transform-time
  call/put_spread_credit can no longer surface as a bogus expired-credit. (2)
  _dc_settlement_spx now uses HYDRA's _resolve_spx_close (current_price, else the
  day's last recorded SPX tick) so a LATE settlement doesn't mark against a
  post-close current_price decayed to 0 (still the recorded close, not the
  official SPXW SOQ — a documented dry-run fidelity limit). (3) D's calendar DB
  tables moved to their OWN file (data/variant_d/dc_calendar.db), separate from
  the shared backtesting.db the base DataRecorder uses — eliminates the
  two-connections-on-one-file concern entirely (readers/Telegram/dashboard
  repointed). +3 tests; full suite 1549 passed. D still dry-run-LOCKED, undeployed.
- Strategy D — live two-expiry probe + expiry-gap fix (2026-06-15, market-hours
  VM probe): the gating market-hours check (two simultaneous SPXW expirations via
  the live calypso-broker session, read-only — no deploy, no A/B/C restart)
  CONFIRMED the capability: both a Fri 11-DTE short and a Tue +4 long return full
  chains (714 strikes), conids, quotes, and delta/vega/theta, with the long leg
  correctly dearer. TWO findings: (1) IBKR's snapshot returns delta/gamma/vega/
  theta but NOT implied_vol (field 7633) for SPXW even after warmup — D's CRITICAL
  path (delta-target strikes, mid-based debit, credit-gated transform) does NOT
  use IV, only the informational _dc_front_back_iv signal does (and it already
  degrades to 'no signal'), so D operates fully; the term-structure signal is just
  unobservable on this feed (the offline backtest with real IV is the edge gate).
  (2) BUG FIXED: SPXW has expiry GAPS (Jun 29 is a weekday but NOT a listed
  expiry; Jun 30 is) — generate_candidate_expiries assumed every weekday is
  listed, so D could pick a non-existent long and fail to resolve. _dc_pick_expiries
  now filters generated candidates to ACTUALLY-listed chains (_dc_expiry_is_listed
  via a cheap get_option_chain check) before selecting, so D skips gap days. +1
  test; full suite 1546 passed. D still dry-run-LOCKED, undeployed.
- Strategy D — adversarial-review fixes (2026-06-14, dry-run-only, A/B/C
  byte-identical): a 26-agent independently-verified review of the full Phases 0-7
  build found 0 critical / 2 high / 1 medium / 14 low (all D-internal or dry-run-
  fidelity; A/B/C confirmed untouched). Fixed: (HIGH) sidecar clobber on startup —
  _dc_save_sidecar is now guarded by a _dc_loaded flag (set before super().__init__)
  so the base recovery/reset can't overwrite the real sidecar with an empty list
  before _dc_load_sidecar reads it; (HIGH) _reset_for_new_day duplicated carried
  calendars when the base reset early-returns (broker outage/STATE-004) — now
  identity-deduped; (MEDIUM) _dc_simulate_entry rejects a net-credit/zero
  "calendar" (inverted term structure) instead of opening an unmanaged position;
  (LOW) wing_width stamped at OPEN (fixes spread_width/capital_deployed=0 in the
  CALENDAR phase); (LOW) _dc_refresh_marks returns freshness and _dc_manage_calendar
  acts on transform/stop ONLY when all leg marks are fresh this tick (EOD close
  stays time-based); (LOW) leg conids nulled on close/settle (no spurious base
  reconcile pass); (LOW) settlement persists base state + sidecar (not just
  sidecar); (LOW) pick_calendar_expiries backtracks when the preferred Friday
  short has no long in the gap; (LOW) dc_status opens the DB read-only (matches the
  dashboard reader); (LOW) refreshed stale "SCAFFOLD/STUBBED" docstrings + config
  comments. DEFERRED (documented, contained): the IC-credit-vertical daily-summary
  leak (LOW — every D output sink is disabled, fix before enabling D sheets/
  alerts), settlement marking against the intraday SPX vs the official PM/SOQ
  close (dry-run fidelity), and two DataRecorder connections on one DB (fine under
  WAL, single-threaded). The Telegram-poller-gated-to-A change is INTENDED (it
  fixes the live multi-poller race), not a regression. +5 tests; full suite 1545.
- Strategy D Phase 7 — D-native Telegram + dashboard observability (2026-06-14,
  dry-run-only, completes the dry-run build): D is surfaced through its OWN view,
  NOT folded into the 0DTE iron-condor /compare or dashboard _VARIANT_IDS
  (credit/Sharpe head-to-head is apples-to-oranges for a multi-day net-DEBIT
  calendar — the Phase-0 exclusions stay, by design). (1) bots/hydra/dc_status.py
  (pure): reads D's sidecar (open calendars) + dc_outcomes and renders them;
  build_telegram_calendars (HydraStrategy) + a new /calendars Telegram command
  (variant A's poller renders D cross-variant — only A polls per Phase 0).
  (2) Dashboard: dashboard/backend/services/dc_reader.py (dashboard-owned pure
  reader, no bot import) + GET /api/dc/status (routers/dc.py) returning D's open
  calendars + outcomes + summary; registered in dashboard main. A React DC panel
  consuming /api/dc/status is the remaining UI polish (needs a frontend build +
  browser to verify — not doable offline). +10 tests; full suite 1540 passed.
  This completes the dry-run build (Phases 0-7). REMAINING before any go-live
  consideration: run _dc_probe_two_expiry_data on the VM (live non-0DTE
  entitlement/warmup, the one offline-unverifiable assumption) and, ideally, an
  offline historical backtest go/no-go. D dry-run-LOCKED, undeployed.
- Strategy D Phase 6 — calendar DB schema (2026-06-14, dry-run-only, shared
  DataRecorder UNTOUCHED, A/B/C byte-identical): D records its trades truthfully
  in its OWN isolated DB (data/variant_d/backtesting.db). New bots/hydra/
  dc_recorder.py:DCDataRecorder owns calendar-shaped tables — dc_calendar_entries
  (debit, 2 expiries, DTEs, conids), dc_transformations (transform_credit,
  is_risk_free, wing strikes), dc_outcomes (terminal_state + realized_pnl with
  BOTH entry_date and close_date so P&L attributes to the entry date), and
  dc_calendar_snapshots — all CREATE IF NOT EXISTS with their own dc_schema_info,
  so NO shared SCHEMA_VERSION bump (A/B/C DBs unmigrated). The shared DataRecorder
  is left exactly as-is. Wired into D: record_calendar_entry in _initiate_entry,
  record_transformation on a fired transformer, record_outcome on stop/EOD/
  settlement; and _record_heartbeat_to_db is OVERRIDDEN to write the generic
  market tick + dc_calendar_snapshots instead of the IC-shaped spread_snapshots
  (call/put_spread_value in IC-named columns would mis-describe a debit calendar).
  All writes fire-and-forget (never block trading); a bad DB path degrades to a
  no-op. +6 tests; full suite 1530 passed. STILL: Telegram/dashboard Phase 7; sim
  fidelity rests on live two-expiry mids (run _dc_probe_two_expiry_data on the
  VM). D dry-run-LOCKED, undeployed.
- Strategy D Phase 5 — multi-day persistence + per-expiry settlement (2026-06-14,
  dry-run-only, ZERO base edits, A/B/C byte-identical): D survives restarts and
  books P&L on the right date. Persistence is a SIDECAR (data/variant_d/
  dc_open_trades.json — the Brandon-hedge precedent) so the fix-scarred 0DTE base
  save/load is NOT touched: _save_state_to_disk calls super() (base file
  unchanged) then writes the sidecar with the multi-day fields the fixed IC
  schema can't hold (dc_phase, per-leg expiry, net_debit, transform_credit,
  wing_width, is_risk_free, flags); _recover_positions_from_saxo calls super()
  (base today-only recovery) then _dc_load_sidecar re-adopts open calendars —
  including ones opened on a PRIOR day, which the base date!=today guard drops —
  replacing any base-loaded IC-shaped version by strategy_id. Settlement:
  check_after_hours_settlement now calls _dc_settle_due — any position whose SHORT
  expiry has arrived settles at the SPX close: a TRANSFORMED IC books
  transform_credit - net_debit - IC-intrinsic (each side capped at the wing; >= 0
  when the risk-free gate held), a leftover CALENDAR liquidates at mark; both mark
  CLOSED + side-expired so active_entries drops them. +8 tests (serialize/load
  round-trip, dedup, CLOSED-exclusion, OTM/ITM settlement, settle-due past/future/
  spx-defer). Full suite 1524 passed. STILL: DB Phase 6, Telegram/dashboard Phase
  7; sim fidelity rests on live two-expiry mids (run _dc_probe_two_expiry_data on
  the VM). D dry-run-LOCKED, undeployed.
- Strategy D Phase 4 — transformer + risk controls (2026-06-14, dry-run-only, NO
  running-bot behavior change while undeployed): _check_stop_losses is now real —
  the DC Time Machine's defining mechanic. Per open calendar each tick
  (_dc_manage_calendar, priority order): (1) at >= dc_profit_trigger_pct profit,
  attempt the TRANSFORMER (_dc_attempt_transform) — sell the 2 back-dated longs +
  buy wings at Kc+wing / Kp-wing on the SHORT expiry, and FIRE ONLY IF the
  realized transform credit >= net_debit + wing_width*100*contracts (structurally
  risk-free); on fire the long legs become the wings (calendar -> same-expiry
  iron condor), dc_phase -> TRANSFORMED, is_risk_free set, [DCTM-TRANSFORM] +
  [DCTM-RISKFREE] logged; if the gate fails it HOLDS (no non-risk-free transform).
  (2) else at <= -dc_pre_transform_stop_pct (default 20% of debit) -> hard close
  ([DCTM-STOP]). (3) else past the EOD cutoff (dc_eod_cutoff_et, default 15:55) ->
  EOD-day-1 close ([DCTM-EOD-CLOSE]). A closed calendar books realized P&L from
  the mark, marks CLOSED, and sets side-done flags so active_entries drops it; a
  TRANSFORMED position holds to expiry (settled in Phase 5). +9 tests; full suite
  1516 passed. STILL: cross-restart persistence Phase 5, DB Phase 6, Telegram/
  dashboard Phase 7; sim fidelity rests on live two-expiry mids (offline-
  unverified — run _dc_probe_two_expiry_data on the VM). D dry-run-LOCKED, undeployed.
- Strategy D Phase 3 — entry + dry-run simulation (2026-06-14, dry-run-only, NO
  running-bot behavior change while undeployed): D's actual entry path. (1)
  _dc_delta_target_strike — scans on-grid OTM strikes outward from spot, reading
  per-strike delta from the broker greeks, and picks the strike closest to
  dc_target_delta within dc_delta_band (the 30-40delta short selection). (2)
  _calculate_strikes — picks the two expiries + the call/put short strikes and
  stamps them on a CalendarEntry (short+long of a side share the STRIKE, differ
  in EXPIRY). (3) _dc_simulate_entry — opens the net-DEBIT double calendar from
  REAL mids (no broker order; net_debit = buy-longs - sell-shorts; synthetic DRY
  ids + per-leg fills). (4) _initiate_entry — orchestrates gates -> strikes ->
  simulate -> book (debit, 4-leg commission, state save). (5)
  _min_buying_power_per_unit overridden to a debit-based floor
  (min_buying_power_per_calendar, default $2000/contract) since a calendar's
  defined risk is the debit, not the IC floor. D stays dry-run-LOCKED — no real
  order ever reaches the broker. +12 tests; full suite 1507 passed. KNOWN GAPS
  (by design): _check_stop_losses is still a Phase-4 stub (an opened calendar
  HOLDS with no transformer/stop/EOD-close yet); DB recording is Phase 6;
  cross-restart persistence of dc_phase/expiry is Phase 5; and the simulation's
  fidelity depends on the live two-expiry mids whose entitlement/warmup is still
  offline-unverified (run _dc_probe_two_expiry_data on the VM). D is NOT deployed.
- Strategy D Phase 2 — two-expiry data layer (2026-06-14, dry-run plumbing, NO
  running-bot behavior change): the broker-data plumbing to pick + read BOTH
  expirations. (1) New bots/hydra/calendar_chain.py (pure, broker-free):
  generate_candidate_expiries (trading-day SPXW candidates, holiday-aware) +
  pick_calendar_expiries (short = in-window expiry, prefer the following-week
  Friday; long = smallest gap in [long_extra_min, long_extra_max] after it). (2)
  DoubleCalendarStrategy gains thin wrappers over the EXISTING broker-data
  methods called with explicit non-0DTE expiries: _dc_pick_expiries,
  _dc_resolve_calendar_legs (4 conids across 2 expiries via _get_option_uic),
  _dc_read_iv / _dc_front_back_iv (per-expiry IV for the term-structure signal;
  None — not 0 — on a flaky read), _dc_read_leg_quotes, and _dc_probe_two_expiry_data
  (a LIVE on-VM diagnostic that verifies non-0DTE SPXW entitlement + snapshot
  warmup — the one Phase-2 item that can't be checked offline). +25 tests; full
  suite 1495 passed. STILL OFFLINE-UNVERIFIED: the live two-expiry entitlement/
  warmup probe must be run on the VM in market hours before Phase 3 relies on it.
- Strategy D Phase 1 — CalendarEntry foundation (2026-06-14, model-only, NO
  running-bot behavior change): the two-expiration / net-DEBIT position model
  every other D surface depends on. (1) Leg gains an additive optional `expiry`
  field (bots/hydra/leg.py) — None on the 0DTE iron-condor family (byte-identical),
  set per-leg on a calendar where the short/long of a side share a strike but
  differ in expiry. (2) New bots/hydra/calendar_entry.py: DCPhase (moved here) +
  CalendarEntry(IronCondorEntry). It SUBCLASSES IronCondorEntry so it reuses the
  Leg bridge, active_entries recognition, state save/load and (conid,quantity)
  reconciliation UNCHANGED — a double calendar's 4 legs map onto the canonical
  leg names, and post-transform the longs move to wing strikes so it becomes a
  genuine same-expiry IC — and OVERRIDES every economic property (total_credit /
  spread_width / call+put_spread_value / unrealized_pnl) with phase-aware,
  debit-rooted math so the IC credit-vertical formulas (which degenerate at
  width=0 on same-strike legs) NEVER run for a calendar. Adds net_debit /
  transform_credit / wing_width / is_risk_free + the risk-free invariant
  (transform_credit >= net_debit + wing*100*contracts). double_calendar_strategy
  now imports DCPhase/CalendarEntry from calendar_entry. A/B/C construct no
  CalendarEntry, so they are unaffected. +18 tests; full suite 1478 passed.
  (Phase 2+: two-expiry chain/quote, entry+sim, transformer, multi-day
  lifecycle/state, DB, Telegram, dashboard — still to come.)
- Strategy D "DC Time Machine" SCAFFOLD + Phase-0 coexistence safety (2026-06-14):
  Adds Strategy D — a multi-day SPX double calendar that transforms into a
  risk-free iron condor (Steve Burnich, video JtGW1wNFNIY) — as a dry-run-LOCKED
  4th variant SCAFFOLD. New DoubleCalendarStrategy(HydraStrategy)
  (bots/hydra/double_calendar_strategy.py): BOT_NAME="DCTM",
  requires_protective_wings=False, a __init__ dry-run LOCK (ConfigError unless
  dry_run=True, BEFORE super() — mirrors StrangleStrategy), a DCPhase enum
  (CALENDAR/TRANSFORMED/CLOSED), multi-day lifecycle overrides (_reset_for_new_day
  carries open positions across the daily reset instead of wiping; check_after_
  hours_settlement treats a held position as normal, not "pending forever"), and
  the three abstract hooks OVERRIDDEN to inert stubs so D never runs HYDRA's IC
  logic. D opens NOTHING (stubbed entry) and cannot place a real order (lock +
  stub). Registered in registry.py ("double_calendar"); config_variant_d.json +
  deploy/hydra_variant_d.service added (HYDRA_VARIANT_ID=d, dry_run, alerts+sheets
  OFF). Phase-0 safety so D can't disturb the live variants: (1) the Telegram
  command poller is now gated to variant A ONLY (main.py) — getUpdates allows one
  consumer per token, so B/C/D no longer each spin a poller; this also FIXES a
  latent live B/C race. (2) print_banner is variant-aware (D gets a DCTM banner,
  not "0DTE Iron Condors"). (3) /compare (strategy.py _discover_variant_ids)
  excludes D — interim, until Phase 7 structure-aware rendering renders its
  net-debit correctly instead of as a credit. (4) dashboard config pre-staged
  (variant_d_* paths + accent) but 'd' deliberately NOT in _VARIANT_IDS yet. (5)
  DCTM-* greppable log tags. Full build (entry/transformer/debit-P&L/two-expiry
  sim/DB/dashboard) is planned (Phases 1-7, dry-run only) — NOT in this commit.
  +14 tests (test_double_calendar_strategy.py + registry); full suite 1460 passed.
- Alert anti-spam gate + Brandon strike-veto + %-of-width shadow (2026-06-11):
  Three changes after a variant-C retry-loop flooded the inbox and the same day's
  Entry#2 mis-placed a short. (1) AlertService gained a single anti-spam GATE at
  the send_alert chokepoint (shared/alert_service.py): content-dedup per priority
  window + per-type token bucket + global email ceiling, fail-open, with a
  _NEVER_SUPPRESS set for halt/naked/breaker/emergency. _should_send_email was
  reordered so _TELEGRAM_ONLY beats the CRITICAL/HIGH bypass (LOW = Telegram-only).
  The Brandon orphan-close alert was re-typed EMERGENCY_CLOSE/HIGH + a 90s
  per-(entry,side) cooldown so a doomed 0-leg close stops re-firing every tick;
  the "IBKR session lost (will restart)" notice was demoted to LOW (self-healing
  in broker mode); ARGUS got file-based cross-run dedup. (2) Brandon delta-target
  PRICE VETO (brandon/strategy.py _calculate_strikes): the 0DTE delta the picker
  keys off under-states moneyness ~2x, so the max_delta clamp passed a ~30delta
  short selected as "8delta" (Entry#2 7250 put). After selection we estimate the
  spread credit and fall back to the conservative OTM-multiplier when a side's
  credit exceeds max_credit_pct_of_width of its width (default 0.20); fail-safe on
  a flaky/0 estimate. (3) %-of-width stop SHADOW (narrow_spread_stop.shadow): logs
  the would-fire trigger vs the acting credit+buffer without acting, for a
  zero-risk head-to-head on live C before flipping the decoupled stop on. +26
  tests; full suite 1425 passed.
- L-C2 Brandon credit+buffer BACKSTOP (2026-06-10): on the Brandon variants
  (B/C), HYDRA's credit+buffer stop now ACTS as a live backstop in BOTH GEX
  states, not just when GEX is fully down (L-C1). Previously, when GEX was armed
  the credit+buffer ran shadow-only and the GEX breach exit was the sole acting
  stop — but the breach exit only fires when spot breaches a decel-wall EDGE,
  which can sit far from the short (a wide/low wall, or a strike placed off a
  stale-greeks profile). The 2026-06-10 variant-C Entry#1 put hit this gap: deep
  ITM at ~16% cushion with the only decel wall 340pt below the 7290 short, so the
  breach exit could never fire and the shadowed credit+buffer never acted — the
  short rode unstopped toward max loss. Fix (brandon/strategy.py _check_stop_losses
  step 3): the GEX breach still gets first crack (PRIMARY, returns early when it
  fires); if it does not close a side, super()._check_stop_losses() runs as the
  MKT-046-confirmed backstop. Mutually exclusive per tick → no double-stop. The
  shadow check is retained as a ~10s early-warning (renamed BRANDON-HYDRA-BACKSTOP).
  Pairs with the same-day stale-greeks guard (which prevents the mis-placement);
  the backstop guarantees protection even if a short ends up far from the wall.
  +1 test rewritten, +1 added (TestGexFallbackStop). Polygon-independent (the
  credit+buffer reads broker quotes, not Polygon) — no subscription change needed.
- 2.0.0-rc.1 modularity refactor — instrument parameterization (2026-06-08, NO
  behavior change): the hardcoded SPX / VIX / SPXW / CBOE / 5pt-grid / 0DTE-expiry
  literals are now config-driven via _load_instrument_params (underlying_symbol /
  volatility_symbol / trading_class / exchange / strike_increment / target_dte),
  each defaulting to today's literal so an absent config is byte-identical. A
  startup assertion (ConfigError, subclass of ValueError) fails fast on an
  unset/invalid value; class-level fallbacks on MEICStrategy keep __new__-built
  objects safe. Threaded through: the option-chain path (qualify_contract /
  qualify_option_strikes / get_option_chain gained an `exchange` param + the live
  caller passes trading_class/exchange), the SPX + VIX index reads, the strike grid
  (HydraStrategy._snap_to_grid + the MKT-020/022 + MKT-040 step sites + Brandon's
  AdjusterConfig.strike_increment), and _get_todays_expiry (target_dte, weekday-aware;
  exchange-holiday-aware expiry deferred). Item 2 of docs/MODULARITY_AUDIT.md /
  docs/PR_SCOPE_LEG_INSTRUMENT.md. +29 tests (test_instrument_params); full suite
  1189 passing.
- 2.0.0-rc.1 modularity refactor — Leg/LegSet substrate (2026-06-08, NO behavior
  change): IronCondorEntry's 24 flat leg fields (short_call_strike … *_mid_at_fill)
  are now backward-compat @property bridges over a `legs` dict of first-class `Leg`
  objects (bots/hydra/leg.py + bind_leg_bridge). Reads, writes, and dynamic
  getattr/setattr all resolve to the legs; the on-disk state schema, serialization,
  and all ~1,011 flat leg references are unchanged. Substrate for future
  non-iron-condor strategies (strangle/butterfly/ratio/…) — item 1 of
  docs/MODULARITY_AUDIT.md / docs/PR_SCOPE_LEG_INSTRUMENT.md. Pinned by 79 new
  tests (test_leg, test_state_serialization_roundtrip, test_entry_leg_accessors);
  full suite 1160 passing.
- 2.0.0-rc.1 post-AUD5 go-live hardening (2026-06-03 → 06-04): the first real
  live-paper fills exposed a cluster of IBKR-path bugs the dry-run masked.
  • Phantom daily-summary (`4dd0a41`): the bot booted after the open into stale
    prior-day state and wrote a daily summary from it (the duplicate Jun-3 row,
    SPX=0). `_daily_summary_is_stale` now vetoes both the DB recorder and the
    main.py after-hours write when the state's date ≠ today or close is 0.
  • One-sided live entry (`57be626`): the Brandon GEX adjuster routes ~86% of
    entries put-only, but the live `_execute_entry` placed all four legs
    unconditionally → a $0.0-strike Long Call leg → "Entry failed at leg 1".
    It now places ONLY the active side(s), mirroring the dry-run path.
  • C1-sweep misses (`46b93c2`): the Saxo→IBKR migration's C1 fix (gate action
    paths on the conid `*_uic`, since IBKR has no per-leg position id so
    `*_position_id` is ALWAYS None live) fixed `_execute_stop_loss` but MISSED
    its siblings. A 10-finder audit found `_close_entry_early` — the close
    method behind BOTH Brandon take-profit AND GEX breach exit — still gated on
    `*_position_id`, so the Jun-4 13:12 TP "closed 0 legs", orphaned the live
    position, and booked $0 (the "+$285 → −$23" cliff). Fixed `_close_entry_early`,
    `_execute_pivot_side_close`, `_count_active_position_legs`, and
    `active_entries.has_any_position` to gate on `*_uic`. Brandon TP/breach exit
    made fail-closed (mark a side stopped ONLY if a leg actually closed; else
    CRITICAL log + orphan-close Telegram). Settlement `_process_expired_credits`
    re-books a side a 0-leg TP/BREACH spuriously marked stopped (forward-safe;
    genuine HYDRA stops are not double-booked). Recovery restores `is_complete`
    and detects active entries conid-aware.
  • Daily-summary Sheet idempotency (this commit): `log_daily_summary` removed
    a day's existing row(s) before appending, so a legitimate same-day re-write
    (restart / multi-pass settlement / manual correction) REPLACES rather than
    appends a second row (the duplicate Jun-2 Sheet row). Mirrors the DB path
    where `daily_summaries.date` is the PRIMARY KEY; self-heals pre-existing dups.
  Tests: tests/test_aud5_fixes.py (+TestLiveCloseGating, TestRecoveryActiveEntries,
  TestSettlementRebook, TestDailySummarySheetIdempotent),
  tests/test_brandon_strategy_integration.py fail-closed updates — 1075 passing.

- 2.0.0-rc.1 go-live hardening + AUD5 (2026-06-02): post-cutover fixes atop
  rc.1. Context: the Saxo→IBKR cutover executed 2026-05-29 (A on IBKR paper;
  B/C dry-run via the shared `calypso-broker` session service); commission
  modelled at IBKR ~$1.15/leg (was Saxo $2.50, `078049f`); the 30-agent AUD4
  migration audit fixed 79 findings (`38ac9d6`). The **AUD5** go-live audit
  (20 domain agents + 3 meta-auditors + 16 backfill agents over `38ac9d6..
  d83d50b`; see docs/migration/AUD5_FINDINGS.md) then fixed:
  • GL-2: ORDER-004 buying-power gate no longer fails open on the IBKR path —
    `margin_pct` is None there, and three f-strings formatted it with `.1f%`
    (TypeError, swallowed by the broad except → gate silently skipped on every
    live entry). Now rendered "n/a" via the existing `_util_str`.
  • C-1: completed the real-time market-data gate that 651a5cc only half-built.
    Index `current_price`/`current_vix` now refuse a quote whose 6509 flag is
    Z/Y/N (frozen / frozen-delayed / not-subscribed), consistent with
    MarketData.update_spx; `_check_market_halt` treats Y/N like Z; a new
    `_option_quote_is_realtime()` gate (None/absent flag passes; explicit
    non-'R' blocks) is wired into MKT-020/022 strike tightening and the MKT-033
    salvage path, and `_read_option_quotes_batch` now surfaces `availability`.
  • C-2: an empty position snapshot shrinks the Sheets Positions tab to
    header-only instead of leaving the prior snapshot's stale rows.
  • C-3: the post-settlement `log_performance_metrics` call uses a
    throttle-exempt period ("End of Day") so the settled-P&L write isn't
    dropped by the intraday Sheets-write throttle.
  • GL-1: flip_a_live.sh / flip_ac_live.sh / broker_paper_smoke.py pin the
    smoke PASS sentinel + the flip's freshness check to the Eastern (market)
    day; flip_ac_live.sh's false "scheduled via systemd timer" comment
    corrected — it is operator-run (manual); see RUNBOOKS RB-8.
  Regression tests: tests/test_aud5_fixes.py. Doc sweep: PROJECT_STATUS,
  strategy spec commission note, CALYPSO_IBKR_MAX_RPS comments (8→5),
  data_recorder docstring, this history.

- 2.0.0-rc.1 (2026-05-22, branch: hydra-ibkr-standalone): IBKR-standalone
  migration. Saxo Bank → Interactive Brokers Web API (ibind OAuth 1.0a,
  no gateway, no local container). All seven phases F1–F7 are
  code-complete (see docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md):
  F1 auth + LST handshake, F2 contract qualification, F3 option chain
  via probed secdef behavior, F4 conid-quantity reconciliation (IBKR
  has no per-leg position id), F5 closedpositions + activities for
  fill-price authority, F6 order write-path with cOID dedup safety,
  F7 broker-agnostic strategy read helpers + account balance. All
  five P1–P7 cleanups: P1 imports + module purges, P2 dead Saxo
  helper removal, P3 method ranges audit, P4 broker abstraction
  flattening, P5 streaming subsystem, P6 retry + per-family circuit
  breakers, P7 go-live (re-auth gate, systemd LoadCredentialEncrypted=
  credentials, multi-agent code audit). The P7 audit found and fixed
  4 Critical + 13 High + 17 Medium + 15 Low/Nit issues across the
  branch — see docs/migration/P7_AUDIT_FINDINGS.md. This branch trades
  IBKR paper only — there is no live-money path. The legacy `--live`
  CLI flag is retained as a no-op for back-compat. Saxo client +
  token_keeper service are kept on the main branch unchanged; HYDRA
  uses neither on this branch.

- 1.27.2 (2026-05-05 PM): Hedge position tracking for the defensive overlay.
  Closes the dry-run gap where overlay placements were logged but not journaled.
  New `bots/hydra/brandon/hedge_position.py` module: HedgeLeg dataclass,
  Black-Scholes call/put pricers, per-leg P&L at expiry (long: +(intrinsic −
  fill); short: +(fill − intrinsic), × 100 × quantity), aggregated debit-spread
  and butterfly payoff math. `BrandonHydraStrategy._brandon_place_overlay` now
  creates HedgeLeg objects with synthetic DRY_OVERLAY_* position ids and
  Black-Scholes-estimated fill prices (default IV 0.18 for SPX 0DTE) and stores
  them on `self._brandon_hedge_legs[entry_number]`. New
  `_brandon_settle_hedges(spx_settle)` runs at log_daily_summary time, computes
  per-hedge net P&L vs SPX_close, fires per-hedge `BRANDON-OVERLAY-SETTLED`
  Telegram + a day-aggregate `BRANDON-OVERLAY-DAY` Telegram. Idempotent within
  the day. New override `log_daily_summary` calls settlement before parent
  super(). Reset in `_reset_for_new_day`. Tests: 166 passing (was 138). Added:
  `tests/test_brandon_hedge_position.py` (23 tests covering BS pricing,
  per-leg P&L, debit-spread payoff at all three regions, butterfly payoff
  including symmetric +/-100pt sanity check, settle_hedge aggregation),
  `TestOverlayHedgeTracking` integration class (5 tests covering placement
  → HedgeLeg creation, idempotent placement, settlement returns settlements,
  settlement is idempotent within day, reset clears hedge state).

- 1.27.1 (2026-05-05): Brandon Trojan Horse stack — promoted to FULLY LIVE in
  variants B/C. Removes the "shadow" framing on GEX strike adjuster, GEX
  breach exit, and defensive overlay — all three now act in dry-run mode
  (and would act in live mode when dry_run is flipped off). The ONLY
  shadow-only behavior is HYDRA's existing credit+buffer stop, which runs
  in parallel as a counterfactual and Telegrams when it would have fired,
  but never closes a position in B/C — Brandon's GEX breach is the live
  stop. (a) GEX strike adjuster mutates `entry.short_*_strike` and
  `entry.long_*_strike` BEFORE _execute_entry / _simulate_entry runs;
  SKIP routes through HYDRA's existing one-sided entry path by setting
  `*_side_skipped` and `*_only` flags. (b) GEX breach exit closes the IC
  via `_close_entry_early` on a confirmed 90s sustained breach, marking
  sides via `*_side_pivot_closed` (same disposition as a directional-pivot
  close). Filter is anchored to the entry's short strike, not current
  spot, so a wall stays "relevant" even after spot has moved past it.
  (c) Defensive overlay places the proposed debit-spread / butterfly legs
  via `_place_option_order` in live mode; in dry-run mode logs the legs
  to journal + Telegram and skips the Saxo round-trip (per
  SAFETY-DRY-01). (d) GEX cache TTL: 1-day → 15-min refresh, with 60s
  failure cooldown. Polygon Starter is unlimited so cost is zero. (e) New
  config knobs promoted from hard-coded defaults to per-variant tuning:
  `gex.{decel_min_pct, accel_min_pct, max_shift_pts, shift_buffer_pts}`,
  `defensive_overlay.{butterfly_cutoff_hour, butterfly_cutoff_minute}`,
  `hydra_stop_shadow.enabled`. (f) Tests: 138 passing (was 131 in 1.27.0).
  Added: TestStrikeAdjusterLive (4 tests), TestBreachExitLive (2 tests),
  TestHydraShadowStop (3 tests). Removed obsolete shadow-only tests.

- 1.27.0 (2026-05-04): Brandon Trojan Horse stack — new variants B/C replace the
  retired pivot experiments (old B = stressed_only, old C = both_sides, both at
  75pt). New variants share Brandon's three additions on top of HYDRA: (a) flat
  80% take-profit closes the IC when its mark decays to 20% of credit received
  (LIVE in B/C); (b) GEX-aware strike adjustment via Polygon SPX option chain —
  shifts wings to enclose positive-gamma deceleration walls or skips a side
  inside negative-gamma acceleration zones (SHADOW for first 4 weeks); (c) GEX
  breach exit signal on sustained 90s breach of the outermost decel wall on the
  threatened side (SHADOW); plus defensive overlay (debit spread before 12:30 ET
  / butterfly after) when SPX threatens a short strike + GEX confirms accel
  (SHADOW). Variant B uses HYDRA's MKT-027 dynamic widths (75pt cap, matches A);
  variant C uses Brandon's narrow 5/10pt rule (5pt at VIX<22, 10pt otherwise) via
  the new `narrow_spread` module — strategy.brandon.narrow_spread.enabled=true
  overrides _get_vix_adjusted_spread_width. Implementation lives in
  bots/hydra/brandon/ as a `BrandonHydraStrategy(HydraStrategy)` subclass loaded
  by main.py when `strategy.brandon.enabled: true`. Variant A's path is
  untouched (zero Brandon references in strategy.py). Polygon dependency: set
  POLYGON_API_KEY env var on the VM; absent key disables GEX features
  gracefully (no crashes). All three variants run dry-run on the VM —
  promoting B/C to live is one config flip per file. directional_pivot is
  disabled across all variants in v1.27 (Brandon's GEX-breach exit plays
  the same role) but the pivot logic remains togglable. 131 new tests +
  79 pre-existing = 210 passing.

- 1.26.0 (2026-05-01 PM): Directional pivot strategy + spx_open 9:30 anchor + 75pt × 2c
  baseline. (a) `MarketData.update_spx` / `update_vix` now gate intraday-OHLC capture
  (spx_open, spx_high, spx_low, vix_open, vix_high, vix_low) to >= 9:30 ET via new
  `_is_regular_session_or_later` static — pre-market Saxo extended-hours ticks no
  longer pollute the "% from open" reference used by Upday-035, Downday-035, whipsaw
  filter, ROC gate, and the new pivot. (b) `_restore_market_ohlc_from_state_file_unconditional`
  is called once at end of HYDRA __init__ so mid-day restarts in dry-run still
  preserve the actual 9:30 anchor (the existing recovery path short-circuits in dry
  mode before reaching `_load_state_file_history`'s OHLC restore). (c) New directional
  pivot strategy gated by `directional_pivot.enabled` (variant B/C only): pre-entry
  defer-and-watch (skip up to `pre_entry_defer_minutes`, default 15) when SPX is
  already breached at entry time, plus a continuous breach monitor (any-time SPX
  ±0.25% from session open closes open base entries via configured `close_mode`:
  `stressed_only` = close the side facing the move; `both_sides` = close all 4 legs).
  Conditional E#3 at 14:00 (Upday-035 / Downday-035) is unaffected. Idempotent within
  the day; cascade-skips deferred entries on fire. New `IronCondorEntry.{call,put}_side_pivot_closed`
  flags + 4 new `MEICDailyState` fields persisted/restored. Stops take precedence over
  pivot (Option 1). Main-loop hook in `bots/hydra/main.py` wrapped in try/except. New
  Telegram alerts: LOW "Entry Deferred" + MEDIUM "Directional Pivot Fired". Dashboard
  variant labels updated. VM: all 3 → 75pt × 2c; B/C `entry_times` = ["10:15","10:45"]
  with pivot block enabled (B: stressed_only, C: both_sides); A unchanged (control).

- 1.25.1 (2026-05-01): N-way variant comparison + variant C scaffolding. Backend `dashboard/backend/
  routers/variants.py` refactored from hardcoded a/b ternaries to a `_VARIANTS` registry (add a
  variant by appending to `_VARIANT_IDS` + adding 5 `variant_<id>_*` settings fields). Frontend
  `Comparison.tsx` renders N variants from `/api/variants/health`; 3+ variant grouped-bar daily
  delta chart added. Bot `_discover_variant_ids()` globs `data/variant_*/` so Telegram `/compare`
  and end-of-day `VARIANT_COMPARISON_DAILY` auto-discover all running non-A variants. New
  `api_pacing_multiplier` strategy config (default 1.0 = variant A no-op) scales monitoring
  loop + heartbeat intervals — variant B at 1.5×, C at 2.0× — to keep combined Saxo API rate
  under ~60 req/min. Vigilant mode (stop detection) is intentionally NOT scaled (safety-critical).
  Variant C config: max_spread_width=25, dry_run, alerts off, sheets off. `deploy/hydra_variant_c.
  service` mirrors variant B's unit with HYDRA_VARIANT_ID=c.

- 1.25.0 (2026-04-30): Path-B dry-mode bookkeeping + MKT-024 multiplier tuning + 1v1 variant
  comparison + 4 SAFETY-DRY defense-in-depth gates. (a) `_process_expired_credits` now treats
  `DRY_*` synthetic IDs as settled — fixes Apr 28-29 false net_pnl=-$20 (commission-only) on
  winning days; DB + metrics file backfilled. (b) MKT-024: call_starting_otm_multiplier 3.5→2.5,
  put_starting_otm_multiplier 4.0→2.75, upper clamp 240→180pt. ~40-50% less wasted scan. (c) New
  `dry_run_force_normal_day` flag bypasses FOMC date-based skips in dry mode; live untouched.
  (d) Variant B = parallel HYDRA process (HYDRA_VARIANT_ID=b env var) running in dry mode with
  max_spread_width=110 (vs A's 50). Isolated data/variant_b/* paths, hydra_variant_b.service.
  Dashboard /comparison page (gated by DASHBOARD_COMPARISON_MODE_ENABLED) + 16th Telegram
  command /compare + end-of-day VARIANT_COMPARISON_DAILY alert (idempotent). (e) Defense-in-depth
  `if self.dry_run` gates at _place_option_order, _handle_naked_short, _unwind_partial_entry,
  _close_position_with_retry — every Saxo-order entry point self-protects.

- 1.24.0 (2026-04-21): scale-to-2-contracts support + non-HYDRA bots kill-switched at code level.
  Phase 1: All stop-level math (call/put buffers, theoretical put, MIN_STOP_LEVEL floor, MKT-042 buffer
  decay) scales via self.contracts_per_entry (live path) or entry.contracts (recovery path). All
  commission sites and close-order amounts scale via entry.contracts so mid-day config flips close the
  right quantity on legacy entries. ORDER-004 margin gate scales MIN_BUYING_POWER_PER_IC. MKT-033 long
  salvage threshold scales. DB schema v8: per-row `contracts` column on trade_entries, trade_stops,
  spread_snapshots, shadow_entries, and `contracts_per_entry` on daily_summaries — transactional
  migration with rollback. Metrics file daily_returns record gains contracts_per_entry for HERMES
  per-contract normalization. State file now persists contracts_per_entry at both top-level and
  per-entry levels (null-safe restoration with `.get() or fallback` pattern handles JSON null from
  crash-mid-write scenarios).
  Phase 2 telemetry: AlertService auto-prefixes `[Nc]` on title + enriches details dict — 14 HYDRA call
  sites updated to pass `contracts=entry.contracts`. Dashboard `/api/hydra/summary`, `/api/hydra/bot-config`,
  `/api/widget` expose contracts_per_entry with 3-level fallback. Google Sheets Daily Summary adds
  Contracts column (append-only). HERMES cheat_sheet gets net_pnl_per_contract + per-contract averages;
  CLIO prompt has mixed-week normalization rule; HOMER Section 2 gains Contracts row; all four agent
  system prompts (HERMES/CLIO/HOMER/APOLLO) + hydra_strategy_context.md updated. 8 Telegram builders
  route through `_with_contracts_footer` helper; `/config` banner warns prominently when contracts>1.
  Cosmetics: STOP-DETAIL diagnostic ask_sv/bid_sv scale with entry.contracts for apples-to-apples log
  comparison. Startup banner includes `Contracts per entry: N` line with warning icon when >1.
  Effective entry numbering rename: live code emits `Entry #1 = 10:45, #2 = 11:15, #3 = 14:00` (post-VIX
  regime) instead of canonical `E#1 = 10:15`. `_effective_total_entry_count()` helper used in all
  user-facing displays (heartbeat, /snapshot, /status, startup log). Pre-2026-04-17 records use
  canonical numbering — agents guided to use entry_time as authoritative slot ID. Kill-switches on
  bots/delta_neutral/main.py, bots/iron_fly_0dte/main.py, bots/rolling_put_diagonal/main.py, bots/meic/main.py
  (DISABLED_FOR_SAFETY=True + _check_disabled_kill_switch() exit before any side effects). MEIC
  strategy.py module remains importable as HYDRA's parent class. 55/55 new regression tests pass.
- 1.22.3 (2026-04-09): Fix #86 — Clear position IDs and UICs on entry object after stop loss.
  Without this, POS-003 hourly reconciliation finds closed positions as "missing from Saxo" and
  fires false "Position Mismatch Detected" HIGH alerts on Telegram after every stop. Base MEIC
  path clears both legs (both closed). MKT-025 path clears only short ID/UIC (long stays for
  settlement/MKT-033 salvage).
- 1.22.2 (2026-04-06): Full codebase audit. Fixed VIX regime credit gate ×100 bug, dangerous code defaults
  (spread widths 60→25, smart_entry True→False, conditional entries True→False). Heartbeat and Telegram
  cushion display now uses MKT-042 effective stop level (was showing base level, not decayed). Buffer decay
  logged at entry time and shown as [decay→$X] tag in Telegram /snapshot. Config template synced to VM values.
  Dashboard: DailyPnLCard /5→/{baseCount}, backtest config put_only_max_vix 25→15, theo put $2.50→$2.60,
  upday_threshold_pct 0.004→0.0025. All docs updated.
- 1.22.0 (2026-04-02): MKT-042 Buffer Decay + MKT-043 Calm Entry. MKT-042: time-decaying stop buffer —
  starts at buffer_decay_start_mult × normal buffer (default 2.10×), linearly decays to 1× over
  buffer_decay_hours (default 2.0h). Wider stops early when premium is rich, normal later. MKT-043:
  calm entry filter — delays entry up to calm_entry_max_delay_min (default 5 min) when SPX moved
  > calm_entry_threshold_pts (default 15.0 pts) in last calm_entry_lookback_min (default 3 min).
  MKT-041 Cushion Recovery DISABLED (buffer+cushion interfere; cushion_nearstop_pct/cushion_recovery_pct
  set to null on VM).
- 1.21.0 (2026-04-01): MKT-041 Cushion Recovery Exit. Closes individual IC sides when they nearly hit
  their stop (>= 96% of stop level) then recover (<= 67% of stop level). Backtest: Sharpe 2.182 vs 2.094
  baseline over 938 days, fires on ~101 days (10.8%). Config: cushion_nearstop_pct (default null/disabled),
  cushion_recovery_pct (default null/disabled). Both in strategy section.
- 1.19.0 (2026-03-29): Walk-forward backtest convergence. 3 base entries (was 5) at 10:15, 10:45, 11:15
  (E4/E5 dropped — negative EV in backtest). E6 upday put-only ENABLED at 14:00 (threshold 0.25%).
  E7 DISABLED. Spread width: VIX x 6.0, floor 25pt, cap 110pt. Credit gates: call $2.00, put $2.75,
  call_floor $0.75, put_floor $2.00. Stop buffers: call_stop_buffer $0.35 (renamed from stop_buffer),
  put_stop_buffer $1.55. FOMC skip FALSE (fomc_announcement_skip=false), T+1 call-only TRUE.
  Downday threshold 0.57%, theo put $2.60. Upday threshold 0.25%. Max spread width 110pt.
  NEW: whipsaw filter (whipsaw_filter.enabled=true, threshold 1.75x EM) — skips entries when
  intraday range exceeds 1.75x expected move (high whipsaw = bad for iron condors).
  put_only_max_vix lowered to 15.0.
- 1.17.0 (2026-03-23): Upday-035 conditional up-day put-only entries. Mirror of MKT-035 for bullish days:
  when SPX rises >= upday_threshold_pct (default 0.25%) above session open, conditional slots E6/E7 fire as
  put-only instead of being skipped. Stop = put_credit + put_stop_buffer. Configurable via
  conditional_upday_e6_enabled / conditional_upday_e7_enabled / upday_threshold_pct / upday_reference.
  DISABLED on VM by default. Dashboard EntryCard shows "Upday-035" label for override_reason="upday-035".
  Backtest support added (backtest/engine.py is_upday_conditional, backtest/config.py, backtest/optimize.py).
  APOLLO scout.py system prompt updated to reflect correct MKT-035 reference price (session open, not high)
  and to document Upday-035.
- 1.16.1 (2026-03-19): MKT-029 graduated call fallback in credit gate. Previously only puts had MKT-029 fallback (-$0.05, -$0.10) in _check_credit_gate(); calls used hard $0.60 minimum. Now both sides use graduated fallback: call $0.60→$0.55→$0.50, put $2.50→$2.45→$2.40. MKT-035/MKT-038 call-only skip checks also lowered from $0.60 to $0.50 floor. Fixed stale comments referencing $0.75 calls and $1.75 puts. All agent prompts updated.
- 1.16.0 (2026-03-16): Skip alerts + dashboard improvements. Telegram ENTRY_SKIPPED alerts at all 8 skip paths in _initiate_entry() with detailed reasons (MKT-011 both non-viable, MKT-032 VIX gate, MKT-035 not triggered, MKT-038 call non-viable, MKT-010 illiquidity, margin). Skipped entries now persisted in state file with skip_reason field for dashboard display. entry_schedule (base + conditional times) added to state file. Dashboard: mobile-responsive header, pending entry cards show scheduled times, skipped entry cards show reason. HERMES can see entry_schedule + skip_reason in trimmed state.
- 1.15.1 (2026-03-16): MKT-040 call-only entries when put non-viable. When put credit below minimum but call viable, place call-only instead of skipping. Data: 89% WR for low-credit call-only, +$46 EV per entry. Stop = call + theo $2.50 put + buffer (unified with MKT-035/038). Override reason: "mkt-040".
- 1.15.0 (2026-03-16): MKT-039 put-only stop tightening + MKT-032 VIX gate raise. Put-only stop changed from 2×credit+buffer to credit+buffer — $5.00 put buffer already prevents 91% false stops, 2× was redundant (max loss $750→$500). MKT-032 VIX gate raised 18→25 (tighter stop makes put-only viable at moderate VIX). Call-only later unified to call + theo $2.50 put + buffer. All agent SYSTEM_PROMPTs updated to v1.15.0.
- 1.14.0 (2026-03-15): MKT-038 FOMC T+1 call-only mode. Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile. Stop = call_credit + theoretical $2.50 put + buffer. MKT-036 stop confirmation timer documented as DISABLED (code preserved, $5.00 put buffer is the chosen solution). All agent SYSTEM_PROMPTs updated to v1.13.0.
- 1.13.0 (2026-03-13): Stop timestamps in state file (call_stop_time/put_stop_time on IronCondorEntry). Dashboard SPX chart now shows red circle stop markers at actual stop time + white price lines for active entry strikes. Negative P&L bar fill fix for stopped entries. MKT-035 scoped to conditional entries only — base entries E1-E5 always attempt full ICs regardless of down-day status ($5.00 put buffer provides sufficient protection). Conditional entries E6/E7 still fire as call-only on down days (SPX < open -0.3%). Threshold reverted to 0.3%.
- 1.12.1 (2026-03-12): MKT-036 asymmetric put stop buffer ($5.00 put vs $0.10 call). 21-day backtest: $5.00 put buffer avoids 91% of false put stops (+$6,885 NET). Configurable via put_stop_buffer (falls back to call_stop_buffer if not set). Telegram /set put_stop_buffer support. Full IC alert shows asymmetric stops. Sheets retry logic (3 attempts with 2s delay). HOMER stop matching fix for same-strike entries. Code audit: all docs, agents, config updated.
- 1.12.0 (2026-03-11): MKT-036 stop confirmation timer code deployed. Subsequently DISABLED on VM — $5.00 put buffer chosen as the solution instead. Code preserved, configurable via stop_confirmation_enabled.
- 1.11.0 (2026-03-11): MKT-035 call-only on down days. When SPX < open -0.3%, place call spread only (no puts). Stop uses theoretical $2.50 put credit instead of 2× call credit. 20-day data: 71% put stop rate on down days vs 7% call stop rate, +$920 improvement. Two conditional entry times (12:45, 13:15) that only fire when MKT-035 triggers. Configurable via downday_callonly_enabled, downday_threshold_pct, downday_theoretical_put_credit, conditional_entry_times.
- 1.10.4 (2026-03-11): Raise put credit minimum $1.75→$2.50, lower call credit minimum $0.75→$0.60. 20-day data analysis: $2.50-$3.49 put credit bucket = 66.7% survival, +$159 avg EV (best); $2.00-$2.49 = 33.3% survival, -$8 EV (worst); $1.50-$1.99 = 48.3%, +$23 EV. Higher put min forces MKT-022 to scan closer to ATM, landing in Week 1 sweet spot (42-65pt OTM). Lower call min = less MKT-020 tightening = calls stay further OTM. Disable MKT-031 smart entry — enter at scheduled times only.
- 1.10.3 (2026-03-11): Disable MKT-034 VIX time shifting + remove VIX entry cutoff (max_vix_entry=999). Neither Tammy nor Sandvand use VIX cutoffs. Entry times revert to 10:15 AM start. Spread widths reverted to 50pt. MKT-034 remains configurable.
- 1.10.2 (2026-03-10): Replace MEIC+ stop formula with credit+buffer (Brian's approach): stop = total_credit + $0.10. Per-side stop level validation fix. Telegram /set updated: call_stop_buffer replaces meic_plus.
- 1.10.1 (2026-03-09): Fix #83: Emergency close improvements for "limit orders only" failures. Fix #83a: Skip closing worthless long legs (bid=$0) during stop loss — prevents cascading 409 errors when Saxo restricts market orders on illiquid deep OTM options. Fix #83b: $0.05 minimum tick fallback in place_emergency_order() when quote returns no valid price. Fix #83c: Cancel zombie pending orders after 409 Conflict before retry. Fix #83d: Removed narrow is_limit_only_period time check (Saxo can restrict at any time, not just 3:45+ PM). Commission tracking now counts only actually-closed legs. Fix #84: Dashboard P&L history updated after settlement (was showing stale pre-settlement snapshot). Strike-not-found log level changed from ERROR to WARNING.
- 1.10.0 (2026-03-08): MKT-034 VIX-scaled entry time shifting. Entry times shifted to :14:30/:44:30 (30s before :15/:45 marks for execution precision). VIX gate checks at :14:00/:44:00 — blocks E#1 if VIX >= threshold (20/23), shifts schedule to later slots. Floor at 12:14:30 (always enters). MKT-031 early entry integrates with VIX gate. Early close cutoff raised from 12:00 to 12:30 PM. Configurable via `vix_time_shift` config section.
- 1.9.4 (2026-03-08): Configurable stop close mode via `long_salvage.short_only_stop` (default: false = close both legs). MKT-025/MKT-033 preserved but gated behind flag. Added /clio Telegram command (15 total). Updated all agent prompts (CLIO, APOLLO, HERMES, HOMER) to v1.9.3 parameters.
- 1.9.3 (2026-03-07): Actual stop debit tracking for per-entry P&L accuracy. Added actual_call_stop_debit/actual_put_stop_debit fields — records real market order cost (including slippage) instead of theoretical trigger level. Dashboard uses actual when available, falls back to theoretical when 0. Fixed pre-existing gap: MKT-033 long salvage flags were missing from preserved_stopped_entries restoration path. Fixed SPXChart price lines for active entries (is_complete → entry_time).
- 1.9.2 (2026-03-05): MKT-033 long leg salvage (requires `short_only_stop: true`). After MKT-025 closes short, sells surviving long if appreciated >= $10. Two trigger points: immediate post-stop + periodic heartbeat check. Tracked in state, Sheets, heartbeat display.
- 1.9.1 (2026-03-05): MKT-032 VIX gate for put-only entries. Put-only only allowed when VIX < 18 (80% WR in calm markets). At VIX >= 18, skip instead of put-only (2× stop with no hedge = 50% WR, unacceptable risk). Configurable via put_only_max_vix. Telegram /set support.
- 1.9.0 (2026-03-05): Telegram commands expanded to 14: /set (edit config), /restart, /stop (with position warning). Message splitting replaces truncation for HERMES/APOLLO reports. Atomic config writes with file locking.
- 1.8.1 (2026-03-05): Entry times shifted to :15/:45 offset (11:15-13:15). 19-day MAE analysis: :15/:45 has 10% lower 30-min adverse excursion vs :05/:35 (12.39pt vs 13.76pt) with better tail risk (P90: 21.71pt vs 23.84pt). Early close day keeps 11:15/11:45.
- 1.8.0 (2026-03-04): Entry schedule shifted +1hr (11:05-13:05 — journal data: 10:05 -$695, 10:35 -$510 vs 11:05+ all positive). MKT-031 smart entry windows (10min pre-entry scouting, 2-parameter scoring: post-spike ATR calm 0-70pts + momentum pause 0-30pts, threshold 65 triggers early entry). Early close day cutoff raised to 12:00 PM (keeps 11:05/11:35 viable).
- 1.7.2 (2026-03-03): Lower call minimum from $1.00 to $0.75 (credit cushion analysis: 68.1% call cushion vs 61.5% — crosses 65% safety threshold from Week 1 data). Less MKT-020 tightening = calls stay further OTM = safer.
- 1.7.1 (2026-03-03): Re-enable MKT-011 put-only entries (data: 87.5% WR, +$870 net from 6 entries). Strict $1.00 call min (remove MKT-029 call fallbacks). Call-only remains disabled.
- 1.7.0 (2026-03-03): 8 new Telegram commands (/status, /hermes, /apollo, /week, /entry, /stops, /config, /help)
- 1.6.2 (2026-03-03): MKT-029 graduated credit fallback thresholds (calls $1.00→$0.95→$0.90, puts $1.75→$1.70→$1.65 — prevents skipping entries barely below minimum)
- 1.6.1 (2026-03-03): Telegram /lastday and /account commands (historical data lookups from Google Sheets)
- 1.6.0 (2026-03-02): MKT-028 asymmetric spread widths (put floor 75pt, call floor 60pt — put longs cost 7x more due to skew, wider = cheaper). MKT-024 upgraded to 3.5x/4.0x starting OTM (batch API = zero extra cost, catches extra cushion on high-credit days). Drop Entry #6 (5 entries, frees margin for wider spreads: 5 x 75pt x $100 = $37,500 <= $39,000). max_spread_width 75pt margin cap.
- 1.5.1 (2026-03-02): Telegram /snapshot command (on-demand position snapshot)
- 1.5.0 (2026-02-28): Renamed from MEIC-TF to HYDRA
- 1.4.5 (2026-02-28): MKT-026 min spread width raised from 25pt to 60pt (longs 10pt further OTM on low-VIX days = cheaper)
- 1.4.4 (2026-02-28): Add 6th entry at 12:35 PM (matching base MEIC schedule — MKT-011 credit gate ensures zero-cost skip when non-viable)
- 1.4.3 (2026-02-28): MKT-025 short-only stop loss close (configurable since v1.9.4; default: close both legs)
- 1.4.2 (2026-02-27): MEIC+ reduction raised from $0.10 to $0.15 to cover commission on one-side-stop (true breakeven)
- 1.4.1 (2026-02-27): MKT-024 wider starting OTM (2× multiplier both sides), separate put minimum $1.75 (Tammy's $1.00-$1.75 range), enhanced MKT-020/022 scan logging
- 1.4.0 (2026-02-27): Remove MKT-019 (revert to total_credit stop), disable all one-sided entries (EMA signal informational only, always full IC or skip)
- 1.3.11 (2026-02-25): MKT-018 early close threshold raised from 2% to 3% ROC (config change, 11-day analysis showed 2% left $1,025 on table)
- 1.3.10 (2026-02-25): Daily Summary: avg capital deployed, cumulative ROC, avg daily ROC, annualized return columns
- 1.3.9 (2026-02-25): MKT-021 ROC gate lowered from 5 to 3 entries, gate now counts actual placed entries not time slots (skipped/failed entries don't count)
- 1.3.8 (2026-02-24): Fix #83 - FIX-71 idempotency guard poisoned by midnight settlement (stored clock time, not trading date)
- 1.3.7 (2026-02-24): MKT-023 smart hold check (compare close-now vs worst-case-hold before early close)
- 1.3.6 (2026-02-24): MKT-011 one-sided entries only for clear trends (NEUTRAL always full IC or skip)
- 1.3.5 (2026-02-24): MKT-022 progressive put OTM tightening (mirror of MKT-020 for calls)
- 1.3.4 (2026-02-23): Fix #82 - Settlement gate lock bug (midnight reset locked gate for entire day, preventing post-market settlement)
- 1.3.3 (2026-02-23): Remove MKT-016 (stop cascade) + MKT-017 (daily loss limit) + base MEIC loss limit — bot always places all entries
- 1.3.2 (2026-02-20): MKT-021 pre-entry ROC gate (min 3 entries), Fix #81 skip $0 long legs during early close
- 1.3.1 (2026-02-20): MKT-020 progressive call OTM tightening, raise min credit to $1.00/side
- 1.3.0 (2026-02-19): MKT-019 virtual equal credit stop, MKT-018 early close based on ROC, batch quote API (7x rate limit reduction), Fix #80 Sheets resize
- 1.2.9 (2026-02-18): MKT-017 daily loss limit, Fix #77/#78/#79 (settlement, summary accuracy, counters)
- 1.2.8 (2026-02-17): EMA threshold 0.2%, MKT-016 stop cascade breaker
- 1.2.7 (2026-02-16): Daily Summary column redesign, Fix #76 fill price field names
- 1.2.6 (2026-02-13): Fix #75 - Async deferred stop fill lookup (non-blocking P&L correction)
- 1.2.5 (2026-02-13): Fix #74 - Stop loss fill price accuracy (deferred lookup was bypassed by quote fallback)
- 1.2.4 (2026-02-13): Code audit hardening - error handling, timeout protection, documentation
- 1.2.3 (2026-02-12): Fix #70 - Accurate fill price tracking (verify vs PositionBase.OpenPrice)
- 1.2.2 (2026-02-12): Fix #65-#68 - Recovery classification, long overlap, timeout protection
- 1.2.1 (2026-02-12): Fix #71-#73 - Duplicate summary prevention, net P&L, active entries fix
- 1.2.0 (2026-02-12): Accurate P&L tracking and daily summary fixes
- 1.1.8 (2026-02-11): Fix #64 - Google Sheets API timeout protection (prevents bot freeze)
- 1.1.7 (2026-02-11): Fix #63 - EUR conversion in Trades tab (pass saxo_client to log_trade)
- 1.1.6 (2026-02-11): Fix #62 - EMA values now logged to Account Summary tab
- 1.1.5 (2026-02-11): MKT-014 liquidity re-check, counter tracking, position merge detection
- 1.1.4 (2026-02-10): MKT-013 same-strike overlap prevention
- 1.1.3 (2026-02-10): Logging accuracy (Fix #49), correct MKT-011/MKT-010/trend labels
- 1.1.2 (2026-02-10): P&L tracking fixes (Fix #46/#47), expired vs skipped distinction
- 1.1.1 (2026-02-09): Hybrid credit gate - respects trend filter in non-NEUTRAL markets
- 1.1.0 (2026-02-08): MKT-011 credit gate, MKT-010 illiquidity fallback
- 1.0.0 (2026-02-04): Initial implementation with EMA trend detection
"""

from bots.hydra.strategy import HydraStrategy, TrendSignal, HydraIronCondorEntry

__all__ = [
    "HydraStrategy",
    "TrendSignal",
    "HydraIronCondorEntry",
]
