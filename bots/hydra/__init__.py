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
- 2026-08-25 Brandon defensive-overlay hedge fixes (THE GOLDEN LOOP: plan,
  adversarial audit-before with 3 independent reviewers, implementation,
  tests-with-code, adversarial audit-after). Prompted by the 2026-08-24
  full-strategy audit (hedge cost real money on both B and C defending a
  threat that evaporated) plus a 2026-08-19 9-event historical replay
  (0-of-9 hedge firings ever defended a side that was actually breached).
  Four changes, all in bots/hydra/brandon/{strategy.py,defensive_overlay.py}
  + a new bots/hydra/brandon/hedge_recorder.py — B and C run the identical
  file, so items 2-3 below are config-gated (see the staging note) rather
  than code-gated:
  (1) C's dry-run hedge-leg pricing switched from a flat-18%-IV
  Black-Scholes model (+ a compensating flat ±$0.25/leg spread-crossing
  hack, itself now removed) to REAL broker quotes at the mid — the same
  convention every other simulated leg in this codebase already uses
  (base_strategy._estimate_entry_credit_ib's `_mid(conid)` pattern). The
  audit found the old model diverged from B's real fill by 4.4x on an
  identical structure. hedge_position.estimate_fill_price is now a
  fallback ONLY for a leg whose live quote is genuinely unavailable
  (logged, so the rare fallback path is visible). Ships active on both B
  and C immediately — naturally isolated to the dry-run branch, zero
  effect on B's live orders.
  (2) A confirmation delay before the hedge actually places
  (defensive_overlay.confirm_seconds, new self._brandon_overlay_trigger_
  first_seen_at pending-timer dict) — the hedge previously fired on the
  very first qualifying monitoring tick with no persistence check, unlike
  every other trigger-sensitive path in this codebase. Mirrors
  _brandon_check_pctwidth_shadow_stop's confirm-then-fire pattern
  (strategy.py ~line 1560) — NOT MKT-046, which an earlier design draft
  reached for and adversarial review caught as the wrong precedent: the
  critical detail is that self._brandon_overlay_placed is added to ONLY
  inside the elapsed>=confirm_seconds branch, never before — adding it
  on the first qualifying tick (as the original draft would have done)
  would let the existing dedup check permanently block re-evaluation of
  that (entry, side), so the pending timer could never reach its
  threshold and the hedge would silently never fire again. A severity
  bypass (defensive_overlay.severity_bypass_distance_pts, new logic, no
  in-file precedent — mirrors the INTENT of MKT-046's L-M6 "≥2x stop
  fires immediately" rule) skips the delay entirely when the threat is
  already inside a tighter distance band, so a genuinely fast real
  breach doesn't wait it out.
  (3) The hedge's own GEX-confirmation gate (defensive_overlay._has_
  accel_zone_on_side) gained the SAME peak-locality gate
  (accel_peak_locality_pts) and 2-independent-reads persistence gate
  (accel_peak_persistence_enabled/_tolerance_pts) the strike adjuster
  already has (gex_strike_adjuster.AdjusterConfig) — reusing that
  already-tested logic, not reimplementing it. The adversarial audit
  found the hedge's original check had NO locality gate at all (just a
  flat 0.05 min-strength threshold, vs. the adjuster's 0.10 for the
  identical concept) — meaning a single wide same-sign GEX cluster could
  rubber-stamp confirmation for any threatened short on that side
  regardless of how far the actual peak sat, plausibly the real reason
  behind the 0-of-9 replay result (bumping the threshold alone would
  have treated a symptom, not the cause). The overlay's OWN persistence-
  gate rotation (_brandon_overlay_rotate_prior_gex_profile, a NEW
  dedicated `_brandon_overlay_prior_gex_profile` pointer) is deliberately
  separate from the strike adjuster's `_brandon_prior_gex_profile`: that
  pointer only rotates once per entry decision, so reusing it for the
  hedge (which checks on every monitoring tick, all day) would compare a
  live threat against a potentially hours-stale entry-time read and fail
  persistence confirmation almost always between entries — a correctness
  bug caught during implementation, not a shared-state optimization.
  Also fixed a second, unrelated hardcoded 0.05 (defensive_overlay.
  _choose_butterfly_pin's decel-wall pin selection) to read
  self.brandon_decel_min_pct instead of a bare literal — ships on both
  immediately (values coincide today; this only removes a future silent-
  drift risk).
  STAGING (2 and 3): both change the SAME shared pre-fork code path
  (evaluate_overlay / _brandon_check_overlay's OverlayConfig
  construction) that B and C call identically before any dry-run/live
  split — B's config keeps confirm_seconds=0 / use_adjuster_gex_gate=
  false (the exact behavior-preserving defaults), C's config turns both
  on for a dry-run trial. This distinction matters because brandon_
  accel_min_pct / accel_peak_locality_pts / accel_peak_persistence_
  enabled are ALREADY live-tuned, non-default values on both B and C (0.1
  / persistence ON) for the strike adjuster — simply reusing them for the
  hedge unconditionally, as an earlier design draft assumed was a
  "no-op" (same reasoning error the original plan made before audit),
  would actually have been an immediate live behavior change on B, not
  inert. Mirrors the project's own established pattern of proving a new
  mechanism on C before ever touching B's live decisions (the narrow-
  spread %-of-width stop's ~2-week A2-SHADOW trial).
  (4) Durable hedge-history tracking — bots/hydra/brandon/hedge_
  recorder.py (BrandonHedgeRecorder), a new isolated per-variant DB
  (data/variant_<id>/brandon_hedges.db) modeled directly on dc_
  recorder.py's pattern for Strategy D/E, NOT an extension of the shared
  backtesting.db (which would add two Brandon-only tables to A/D/E's
  databases too, for no benefit, and cuts against the documented
  "Brandon-hedge precedent" for per-variant isolation this file's own
  history already established for D). Two tables (hedge_placements — one
  row per leg, hedge_settlements — one row per settled hedge), written
  fire-and-forget from _brandon_place_overlay (both dry-run and live
  branches) and the BRANDON-OVERLAY-SETTLED site — a recording failure
  never affects trading logic, same convention as every other recorder
  in this codebase. Before this, hedge history lived only in the daily-
  wiped brandon_hedge_legs.json sidecar and the log file.
  AUDIT-AFTER FINDING (same day, caught by adversarial review of the
  implemented diff, before any deploy): item 3's persistence-gate
  rotation (_brandon_overlay_rotate_prior_gex_profile) originally
  advanced a SINGLE pointer on first touch of a new fetched_at — but
  _brandon_check_overlay runs every ~2-5s monitoring tick, for every
  active entry (up to 7 concurrent slots on B), while a GEX profile only
  refetches every ~180s. Only the very first call after a fresh fetch
  got a real "prior != current" comparison; every later call that tick
  and every tick until the NEXT refresh saw prior==current and got
  force_unconfirmed=True — starving persistence confirmation almost
  permanently once enabled, which would have defeated the entire point
  of C's staged trial (use_adjuster_gex_gate=True +
  accel_peak_persistence_enabled=True) had it shipped. Fixed to a true
  2-slot ring buffer (_brandon_overlay_current_gex_profile /
  _brandon_overlay_prior_gex_profile) that only advances on a genuinely
  NEW fetch, so the prior stays valid for the full lifetime of the
  current profile across every entry and every tick. Regression tests
  added (tests/test_brandon_overlay_confirmation_2026_08_25.py,
  TestOverlayGexProfileRotation +
  TestPersistenceGateSurvivesMultipleEvaluationsOnSameProfile) and
  verified via negative control (temporarily reverted to the buggy
  rotation — 3 of 4 new tests failed as expected, including the direct
  end-to-end reproduction; restored the fix, all pass). Full suite:
  2484 passed, 0 failed after the fix.
- 2026-08-21 full-day audit LOW/INFO follow-ups (THE GOLDEN LOOP, 1 review
  round + 1 investigation). Same-day full-strategy execution audit for
  2026-08-21 (itself CLEAN — confirmed the prior night's MKT-047 deploy held
  up correctly in every scenario the day produced) surfaced 3 minor items;
  closed all 3:
  (1) base_strategy.py / strategy.py — the stop-loss "Stop loss executed"
  summary log line printed the static/undecayed buffer level (e.g. $317.50)
  instead of the actual MKT-042-decayed trigger that caused the breach (e.g.
  $405.76, already shown correctly one line above in the STOP-DETAIL/
  MKT-046 output) — display-only, DB/state/P&L math were already correct
  (a 2026-08-20 fix already recorded the decayed value into
  trade_stops.trigger_level, but only for that DB column, not this line).
  Fixed with a new `display_trigger_level` parameter on the shared
  MEICStrategy._execute_stop_loss (base_strategy.py) that changes ONLY the
  final returned string; HydraStrategy's override now computes
  `effective_trigger_level` BEFORE calling super() (verified safe —
  _get_effective_stop_level depends only on entry state + the wall clock,
  nothing super() mutates) and reuses the same value for both the existing
  DB write and the new display parameter. The sibling MKT-025 short-only-
  stop branch (currently dormant — short_only_stop=false on every tracked
  config) had the identical bug in its own separately-implemented return
  string; fixed for completeness, reusing its own already-computed
  effective_trigger_level. Review confirmed this has ZERO observable effect
  on B tonight — B runs narrow_spread_stop.enabled=true (A2 %-of-width
  mode), under which _get_effective_stop_level short-circuits to the same
  static value stop_level already equals, so the fix's visible effect is
  confined to A and C's shadow logs. Also flagged (documented, not fixed):
  the Telegram/email alert body and the (globally-disabled) Sheets log still
  source the static level for the same event — pre-existing, unaffected by
  this diff, a candidate for a future full-accuracy pass.
  (2) base_strategy.py — `_estimate_entry_credit_ib` returned unrounded
  floats, so an economically-exact credit (e.g. mid 0.15 - mid 0.10 =
  $0.05/share = $5.00 total) could land as a float artifact like
  4.999999999999993 and miss a credit-gate `>=` threshold it should clear by
  noise alone. Real incident: variant C's Entry #1 put credit missed the
  MKT-029 fallback-acceptance branch this way (made no difference that day —
  the call side was independently vetoed). Fixed by rounding both
  estimated_call_credit/estimated_put_credit to the cent at the point of
  computation, mirroring the pre-existing rounding already applied a few
  lines below for the MKT-048 fillable-credit stash. Review brute-forced
  ~14,400 realistic penny/nickel-tick bid/ask combinations and confirmed the
  true noise-free credit is always an exact $0.50 multiple given IBKR's
  option-tick quantization — round-half-to-even ties are not a reachable
  risk here; round() only ever cleans up ~1e-13 IEEE-754 noise. Review also
  found (documented, not fixed) the identical unrounded-credit pattern in
  MKT-020/MKT-022's progressive OTM-tightening scans — currently dead code
  on B and C (both set brandon_disable_progressive_tightening=true), only
  reachable on A (dry-run-shadow, no real orders); worth the same fix if
  tightening is ever re-enabled on a live variant. A MEDIUM-severity version
  of this same finding was raised then adversarially REFUTED for
  overclaiming "live, reachable... real paper money" risk — correctly
  downgraded once B/C's config gating was traced.
  (3) D's "unexplained near-nightly restart pattern" (flagged as an audit
  LOW item, not a confirmed bug) — investigated and fully explained by
  benign causes, no code/config fix needed. Every one of 7 restarts in a
  2-week lookback traces to a `sudo systemctl restart...` audit line from
  the operator account within 1-2 minutes prior (routine post-deploy
  restarts and debug sessions) — no autonomous crash-loop, no timer, no
  cron, no OOM kill anywhere in the window. The one ungraceful SIGKILL
  (2026-08-18, all 5 units simultaneously, not D-specific) is the same
  fleet-wide grpc-core client-teardown shutdown-hang first flagged
  2026-08-03 and already root-caused + mitigated the very next morning in
  commit 3bce8940 — likely the incident that motivated that fix. Zero
  SIGKILLs have recurred on any hydra unit since.
  10 new tests in tests/test_audit_minor_fixes_2026_08_21.py, both code
  fixes negative-controlled (reverted, confirmed RED, restored, confirmed
  GREEN). 1 review round (2 reviewers + verify pass on every finding): both
  fixes PASS, 5 findings confirmed (all INFO/LOW, no defects — reassuring
  observations + this version-history reminder + documented adjacent gaps),
  1 MEDIUM finding adversarially refuted (see above). Full suite: 2393
  passed, 15 skipped.
- MKT-047 EOD-flatten continuous re-check + dry-run cost correction
  (2026-08-20, THE GOLDEN LOOP, 2 review rounds). Prompted by the same-day
  full-strategy execution audit, which surfaced two real production
  incidents:
  (1) strategy.py — new _eod_flatten_dry_run_correct(entry, side_name) fixes
  a dry-run zero-cost booking bug on A/C: in dry-run, _close_position_with_
  retry (SAFETY-DRY-04) never produces a simulated fill price for an early
  close, so the side's FULL credit was booked as if it closed for free.
  Mirrors Brandon's existing TP/breach correction pattern (fall back to the
  pre-close spread-value mark). Real incident: variant A's day flipped from
  a reported +$24.85 to a true ~-$40/-$55; C overstated by ~$490 (+$619.50
  reported vs ~+$129.50 true). Wired into both the primary
  _execute_eod_flatten sweep and the new re-check below.
  (2) strategy.py — new _check_eod_flatten_recheck() closes a real near-miss
  on B (live paper money): the primary MKT-047 sweep decides once, at 15:50
  ET, whether each side is safe to ride to free expiry — two short puts
  measured 11pt OTM at that single check and were left riding, but SPX
  drifted and the cushion shrank to 0.84pt (near-ATM) at 15:57:30 before
  recovering to 1.4pt by close. No loss resulted, but nothing re-evaluated
  the decision as price kept moving. The new function re-watches every side
  left riding on every tick (reusing the existing ~2-12s monitoring cadence)
  from the moment the primary sweep fires until close, closing a side that
  drifts back within the cushion and applying the dry-run cost correction
  above.
  Round-1 review (3 reviewers) found 2 HIGH + 3 MEDIUM/LOW issues before
  this could ship: a 90s failed-close cooldown that only cleared on eventual
  success, not on recovery — a side that failed, went briefly safe, then
  drifted back at-risk within the same 90s window stayed silently
  unprotected; the per-entry retry loop had no wall-clock bound across
  multiple entries in one tick, letting several stuck legs compound serially
  and starve every other entry's safety check for up to the full ~10min
  window; a hardcoded now.hour>=16 market-closed bound that ignored
  early-close (1:00pm ET) days; a new _eod_side_is_live_short helper missing
  the pivot_closed exclusion already present in the pre-existing
  _eod_flatten_can_skip_side gate; and a trade_stops DB write-ordering gap
  (inherited from Brandon's pre-existing correction pattern, does not affect
  real P&L, documented not fixed). Fixed: clear the cooldown the instant a
  side is observed OTM-safe again, not only on a later successful close; a
  60s per-tick wall-clock budget (time.monotonic) so a stuck leg can only
  block one tick, not compound across entries within it; mins_to_close now
  computed via shared.market_hours.get_market_close_time(now), matching the
  existing pattern in _place_marketable_close; pivot_closed added to
  _eod_side_is_live_short.
  Round-2 review (3 reviewers + independent verification of every finding)
  confirmed all 4 round-1 remediations hold, but surfaced one new real HIGH
  finding via a dedicated interaction-focused pass: the two HIGH fixes above
  compound ACROSS ticks, not just within one — a persistently
  oscillating/failing early entry, repeatedly re-armed by the clear-on-safe
  fix, can dominate the 60s budget on many consecutive ticks (fixed
  iteration order restarts from the front every tick), starving a later,
  unrelated entry of its own recheck coverage for a meaningful fraction of
  the ~10min window — reopening HIGH#2's own anti-starvation intent. Fixed
  with a round-robin iteration order (_eod_recheck_next_start_idx): each
  tick starts where the previous tick left off, so an entry that blows the
  budget gets pushed to the back of the queue instead of perpetually
  occupying the front. Two other round-2 claims (an alert-spam risk from the
  clear-on-safe retry cadence; an intermittent full-suite test flake) were
  independently adversarially verified and refuted — the alert path already
  goes through AlertService's existing content-dedup gate, and the flake
  claim could not be reproduced (0/3 full-suite runs, 0/1 targeted prefix
  run, 10/10 isolated runs) and had no plausible mechanism given the test's
  full determinism/isolation.
  30 new/updated tests in tests/test_eod_flatten_recheck_and_dryrun_fix_
  2026_08_20.py — every fix (both original + all 5 remediations)
  independently negative-controlled (reverted, confirmed RED, restored,
  confirmed GREEN). Full suite: 2383 passed, 15 skipped. Deploy note: NOT
  yet deployed as of this entry — B is live and trading; deferred to the
  next confirmed-flat window.
- Full-strategy execution audit follow-up fixes (2026-08-20, THE GOLDEN LOOP,
  2 review rounds). Prompted by a comprehensive audit of every variant's full
  2026-08-19 trading day plus independent re-verification of everything
  deployed the night before. P&L itself was correct everywhere (every
  variant's ledger reconciled exactly); these fixes close real tracking/
  attribution/analytics gaps found in that audit, all on variant B (the live
  seat) except A1:
  (1) base_strategy.py — a failed entry's leg-in that gets safely unwound
  (_unwind_partial_entry) or an ORDER-010 accumulated partial that gets
  market-flattened (_flatten_accumulated_partial) now books the round-trip's
  commission (2x commission_per_leg x quantity — open, never booked since the
  entry never completed, + this close) into daily_state.total_commission.
  Previously invisible entirely; the reported day cost understated the true
  economic cost of a failed attempt. Real 2026-08-19 incident: variant B
  entry #6's ~25-contract round trip.
  (2) brandon/strategy.py — _brandon_settle_hedges() now groups
  _brandon_hedge_legs by placed_at (shared exactly across every leg of one
  _brandon_place_overlay() call) before settling, so an entry that receives
  TWO independent hedge placements hours apart (e.g. a call debit spread
  morning, a put butterfly afternoon) settles as two correctly-labeled
  HedgeSettlements instead of one merged, mislabeled record (aggregate $ was
  always right; structure/threatened_side attribution was not). Real
  2026-08-19 incident: variant B entry #5 (4 real placements, only 3
  BRANDON-OVERLAY-SETTLED lines). Round-1 adversarial review found and fixed
  a genuine atomicity regression this refactor introduced (booking could
  complete for one group while a later group's logging/Telegram raised
  before the entry-level guard was set, risking a double-book on a same-day
  restart) — restructured into a pure-compute phase (settle_hedge, no side
  effects), then an atomic book+guard phase (pure arithmetic, zero I/O), then
  logging/Telegram strictly after. Empirically reproduced and closed.
  (3) brandon/strategy.py — _expected_position_quantities() and
  _get_current_position_size() now exclude entries already in
  _brandon_overlay_booked (settled). _brandon_hedge_legs is intentionally
  NEVER cleared of settled legs (the dashboard's brandon_hedge_legs.json
  sidecar reader needs them to keep showing settled hedges after close — see
  the dashboard fix below), but a settled hedge's real IBKR position no
  longer exists — without this exclusion, every same-day restart after a
  hedge settled logged a permanent POS-003 "ambiguous, leaving for manual
  review" warning indistinguishable from a genuinely stuck leg. Round-2
  review confirmed this is safe on the normal path (an entry can't reach
  _brandon_overlay_booked before POS-004's broker-confirmed-flat check
  passes, since that check reuses this same method) and flagged one DORMANT
  gap for the future: MKT-018 early-close (disabled on every variant today)
  has no broker-confirmation wait and Brandon doesn't flatten hedge legs in
  that path — documented in the code, not fixed (not reachable while
  early_close_enabled stays false).
  (4) strategy.py — _execute_stop_loss()'s non-short-only branch now records
  the MKT-042-decayed _get_effective_stop_level() value (what the live
  trigger check actually fires against) into trade_stops.trigger_level,
  instead of the static entry-time base level — any stop firing inside the
  ~4h decay window (most 0DTE stops) had this DB column off by the decay
  multiplier, feeding wrong data into buffer-calibration/slot_edge/HERMES
  analysis. Real 2026-08-19 incident: variant A entry #2 put (DB showed
  $405, log showed the stop fired against an effective ~$530 trigger).
  Round-1 review found the initial version leaked the decayed value into
  _record_stop_to_db()'s net_pnl fallback too (used when actual_close_cost
  is unknown), diverging from daily_state.total_realized_pnl (which stays
  static, booked separately inside super()._execute_stop_loss()) — fixed by
  adding a dedicated effective_trigger_level parameter that feeds ONLY the
  trigger_level column; the original stop_level parameter keeps its exact
  prior meaning and net_pnl-fallback behavior. Round-2 review found the same
  gap, still unclosed, in the MKT-025 short_only_stop branch (currently
  dormant — false on every tracked config) — closed for completeness.
  (5) CLAUDE.md — corrected two stale claims found during the audit: the
  documented B/C stop formula (credit+buffer) is no longer what actually
  fires on B, which has run the A2 %-of-width override since the 2026-07-24
  swap; and skip-worktree does NOT block a git pull's fast-forward from
  overwriting config_variant_*.json content (only suppresses git status/
  diff/add from flagging local edits) — empirically confirmed the same
  night when a routine pull silently (and harmlessly, this time) applied a
  tracked value to the VM's live B/C configs.
  Also same night: 2 real dashboard display bugs fixed and deployed (see the
  entry below this one) plus verification that the 2026-08-19 shutdown-hang
  fix genuinely works (3-6s clean restarts vs the prior 58-84s, once
  properly exercised by a restart that actually loaded the fixed code).
  15+ new/updated tests across tests/test_failed_entry_commission_tracking_
  2026_08_20.py, tests/test_brandon_strategy_integration.py, tests/
  test_brandon_overlay_live_sizing_2026_07_21.py, tests/
  test_stop_db_decay_recording_2026_08_20.py, and a fixture fix in tests/
  test_preflight_audit_fixes.py — every negative control independently
  re-verified by BOTH review rounds, not just the author. Full suite: 2353
  passed, 15 skipped. Deploy note: NOT yet deployed as of this entry — B is
  live and trading; deferred to the same night's safe window (B flat).
- Brandon defensive-overlay hedge tightening + PII fix-forward (2026-08-19,
  THE GOLDEN LOOP, 2 review rounds). Prompted by a live-day review of B/C's
  overlay hedge history: an event replay found the hedge (debit-spread pre-
  12:30 ET / butterfly after) was arming well before real danger on B/C's
  narrow 5-10pt spreads, and never once defended a side that was actually
  breached. Two changes, both scoped to the defensive_overlay layer only —
  the credit+buffer stop-loss and the independent GEX-fallback stop
  promotion are untouched:
  (1) brandon/strategy.py:_brandon_check_overlay() — OverlayConfig's
  require_gex_confirmation was `(profile is not None)`, an unreviewed
  default from the original 2026-05-05 commit that let a hedge fire on
  distance alone whenever the Polygon GEX profile was simply present,
  without an actual accel-zone confirming real danger. Changed to always
  `True`. Does not weaken Polygon-outage protection — that path already
  runs through the separately-tested _brandon_alert_gex_fallback stop
  promotion, independent of this overlay.
  (2) config_variant_b.json / config_variant_c.json — trigger_distance_pts
  25 -> 15pt. See each file's _comment_trigger_distance_pts for the full
  rationale and confidence caveat (LOW-MEDIUM; small event sample).
  Added BRANDON-OVERLAY-WATCH instrumentation (60s-throttled per side) that
  logs distance-at-tick + gex_confirmed whenever price is within 2x the
  trigger distance, so a future replay has real recorded data instead of
  inferred bounds. Round-1 review found 4 real issues, all fixed: missing
  put-side test coverage for the GEX-confirmation fix; no config-wiring
  test proving the JSON value actually reaches the strategy; the new WATCH
  log had no throttle (would spam every tick near a threatened strike);
  gex_confirmed could read misleadingly. Round-2 re-verified all fixes
  independently and found none further. 13 new tests across
  tests/test_brandon_strategy_integration.py, negative-controlled
  throughout (each fix reverted in isolation, confirmed red, restored,
  confirmed green) — 100/100 passing in that file, full suite 2331
  passed/15 skipped. Bundled in the same commit: a PII fix-forward —
  bots/hydra/config/config_variant_{b,c}.json's alerts.email had been
  overwritten from the "your@email.com" placeholder to a real address by
  an autonomous on-VM commit (HOMER's git auto-commit, a known gotcha —
  see the operator memory) that reached origin; restored the placeholder
  going forward per an explicit operator decision (fix-forward only, not a
  history rewrite — this is a private repo and an email address, not a
  credential). Deploy note: NOT yet deployed as of this entry — bundled
  with the shutdown-hang hardening below, both deferred to the same safe
  window (B flat or market closed).
- Strategy-process shutdown-hang investigation + client-hygiene hardening
  (2026-08-19, THE GOLDEN LOOP, 2 review rounds). Root-caused the recurring
  "SIGTERM logs 'Shutdown complete' but the process doesn't actually exit
  for 44-82+s, needing a forced SIGKILL" symptom first flagged 2026-08-03
  (broker-side fixed then; strategy-process side left open). Workflow-driven
  investigation (code audit + live VM evidence + grpcio-internals research)
  found: the lingering grpc_global_tim/event_engine/lifeguard child threads
  seen at SIGKILL time are grpc-core PROCESS-GLOBAL singletons, lazily
  spawned by the first channel any client library constructs (Secret
  Manager, used by every variant at startup; Pub/Sub, constructed even on
  alert-DISABLED D/E per AlertService's L-C2 comment) — confirmed via
  grpcio 1.80.0's own compiled source strings that these can only be torn
  down by grpc's internal shutdown sequence at real interpreter exit, not
  by closing an individual channel. The "abandoned Pub/Sub publish future"
  theory was checked against real logs and does NOT fully explain it alone
  (D hit the identical hang with zero Pub/Sub calls that day, alerts
  disabled). Shipped as honest, real client-hygiene hardening — NOT a
  proven elimination of the underlying grpc-core teardown latency:
  AlertService.close() (new) releases the Pub/Sub publisher's channel
  promptly on a bounded background thread (CLOSE_TIMEOUT_S=5s, matching
  shared/logger_service.py's _sheets_call_with_timeout convention), called
  from bots/hydra/main.py's shutdown sequence via two new testable helpers
  (close_alert_service_safely, log_shutdown_diagnostics — the latter logs
  live Python thread names + /proc/self/status's native thread count right
  before "Shutdown complete", filling the exact evidence gap that made
  this investigation need a full forensic pass instead of a log read).
  shared/secret_manager.py's get_secret()/update_secret() now release
  their per-call SecretManagerServiceClient the same way. Round-1
  adversarial review (3 reviewers) found 6 real issues, ALL fixed: (1) an
  initial future.cancel() "fix" was a hard no-op for Pub/Sub futures
  (verified against the installed library source) — removed rather than
  left in place implying a mitigation that doesn't exist; (2)
  AlertService.close()'s close call initially had no timeout, breaking
  this codebase's own bounded-blocking-call convention — fixed; (3) an
  unsynchronized self._publisher read-then-use (TOCTOU) — narrowed via a
  local snapshot; (4) HIGH — the initial secret_manager fix used the
  client as a context manager, and a close-time exception AFTER a
  successful RPC propagated past the pending return, reporting a real
  secret fetch/update as a failure (reproduced empirically) — fixed by
  moving the close into an explicit `finally` that can never override an
  already-decided RPC outcome; (5) matching test-coverage gap — closed;
  (6) the new helpers were only wired into run_bot()'s main-loop finally:
  block, not its ~6 earlier startup-failure early-return paths — fixed,
  all now call both helpers (passing None for `strategy` where it isn't
  in scope yet). Round-2 review re-verified all 6 fixes independently
  (fresh code reads + empirical repro, not just re-reading round-1's
  claims) and found ONE more real issue via a fresh sweep:
  _close_secret_manager_client's own threading.Thread()/.start() calls
  were unguarded, so a failure to even spawn the close thread (e.g. OS
  thread exhaustion) reproduced the identical success-masked-as-failure
  bug through a different trigger — fixed by wrapping the spawn itself.
  25 new tests (tests/test_shutdown_hang_grpc_cleanup_2026_08_18.py),
  negative-controlled throughout both rounds (each specific fix reverted
  in isolation, confirmed the matching test goes red, restored, confirmed
  green) — caught 2 real bugs in the tests THEMSELVES along the way (a
  simulated-hang duration shorter than the bound being tested, silently
  passing either way; the process-wide `time` module patch from an
  existing autouse fixture silently neutering a time.sleep()-based hang
  simulation) before they could ship as false-confidence coverage. Full
  suite: see the run immediately after this entry for the final count.
  Deploy note: NOT yet deployed as of this entry — 2026-08-19 is a live
  trading day with variant B (the live paper seat) holding real open
  positions; deploy deliberately deferred to a safe window (B flat or
  market closed), never a mid-session restart with positions open.
- Calendar (D/E) daily-reset sidecar race + phantom-activity EOD summary
  (2026-08-18, THE GOLDEN LOOP). Investigated the previously-unresolved
  "D's dc_open_trades.json came back empty" loose thread (proven not caused
  by the 2026-08-17 pnl_history deploy, but never root-caused) and found two
  distinct real bugs in calendar_strategy_base.py, both applying to D and E:
  BUG A (write-ordering race): CalendarStrategyBase._reset_for_new_day()
  re-attaches any carried multi-day position AFTER calling
  super()._reset_for_new_day() — but the base reset ends with its own
  _save_state_to_disk() call, made while daily_state.entries is still the
  freshly-emptied list (the carry re-append hasn't run yet). Via this
  class's _save_state_to_disk() override, that also writes dc_open_trades
  .json EMPTY. Harmless if a later same-day save re-persists the (correctly
  repopulated in-memory) state first; but a restart landing in that window
  reads the empty sidecar on boot and permanently drops the position from
  tracking, no further monitoring ever. Confirmed this exact sequence
  produced the observed 2026-08-17->18 empty file (its mtime lands on the
  reset that also logged the [CAL-CARRY] carry-forward for it). FIX: track
  re_save_needed = bool(carried) and call _save_state_to_disk() again at
  the end of _reset_for_new_day(), once daily_state.entries is correct.
  BUG B (phantom EOD summary): main.py's had_trading_activity gate included
  `len(daily_state.entries) > 0`, which is always true for as long as ANY
  multi-day position is carried (re-attached every reset) — it can never
  distinguish "holding a position, nothing new today" from real activity.
  Observed: at 00:00:36 ET on 2026-08-18, 14 seconds after the midnight
  reset, this falsely read True purely because D's carried Aug-13 calendar
  was in daily_state.entries — triggering log_daily_summary() to book the
  position's stale reset-moment unrealized mark (-$323.75) into
  hydra_metrics.json as a fabricated "realized" result for a day that had
  not started trading, corrupting D's cumulative track record for that
  date (needs a separate manual data reconciliation on the VM; not
  self-healing since the position was never actually settled). FIX:
  extracted the check into MEICStrategy._had_trading_activity_today()
  (base_strategy.py, byte-identical default logic — A/B/C unaffected) and
  overrode it in CalendarStrategyBase to require either nonzero realized
  P&L/entries_completed, or at least one entry whose entry_time falls on
  TODAY — a carried-only entry no longer counts. main.py now calls
  strategy._had_trading_activity_today() instead of re-inlining the check.
  14 new tests (tests/test_calendar_carry_and_activity_gate_2026_08_18.py),
  including a real end-to-end test that writes/reads the actual sidecar
  file. Negative-controlled: 13 of 14 correctly fail with both fixes
  reverted (the 14th is a no-carry no-op check, correctly unaffected).
  Full suite: 2294 passed (was 2280), 15 skipped.
- Fix #84's final-pnl_history-point moved to log_daily_summary() (2026-08-17).
  B's dashboard "today" card showed +$78.40 for the whole evening after the
  true settled total was -$76.60 — exactly the -$155.00 combined loss on two
  Brandon defensive-overlay hedges (entries #3/#4) that settled a few seconds
  AFTER the point had already been written. ROOT CAUSE: Fix #84 wrote the
  "final" _pnl_history point inside check_after_hours_settlement() (strategy.
  py), which main.py calls BEFORE log_daily_summary() — but for a Brandon
  variant, log_daily_summary() is what settles the hedges (_brandon_settle_
  hedges folds their P&L into total_realized_pnl before calling super()), so
  the point captured a pre-hedge-settlement snapshot nothing ever corrected.
  The real Telegram/email alert and DB daily_summaries row were NEVER wrong —
  both are built later, inside log_daily_summary()'s own get_daily_summary()
  call, after hedge settlement. Only the dashboard's in-memory pnl_history
  curve had this staleness bug.
  FIX: removed both Fix #84 call sites from check_after_hours_settlement()
  (strategy.py, ~line 13043 area); added the final-point write to base_
  strategy.py's log_daily_summary() (MEICStrategy — every concrete strategy
  reaches this via super()) instead, positioned right after net_pnl/
  commission are computed and using that SAME net_pnl the alert/Sheets/DB row
  already report. Since a subclass's own settlement work always runs BEFORE
  its super().log_daily_summary() call reaches this method's body, the point
  can no longer be written ahead of any subclass-specific settlement step —
  not just Brandon's, whatever future ones get added too. Same-minute points
  now overwrite instead of appending a duplicate (matches the regular
  heartbeat-driven pnl_history updater's existing convention).
  Applies to all five variants (A/B/C/D/E all reach the shared base method);
  in practice only B/C's Brandon hedges can trigger the staleness this fixes.
  6 new tests (tests/test_pnl_history_settlement_ordering_2026_08_17.py),
  negative-controlled (5 of 6 correctly fail with the fix reverted — the 6th
  covers a zero-expired-credit path that was already a no-op pre-fix). Full
  suite: 2275 passed (was 2269), 15 skipped.
- GEX accel-zone peak persistence gate (2026-08-12, THE GOLDEN LOOP). Ships
  INERT — accel_peak_persistence_enabled defaults False in both AdjusterConfig
  and the strategy config-read; config_variant_b.json/config_variant_c.json
  are NOT edited by this change, so this deploy is a behavioral no-op on both
  live B and shadow C. Flipping it on is a deliberate, separate follow-up
  (C first, observe, then B) — see below.
  CONTEXT: a workflow-driven forensic investigation (source-read + 45-day DB
  baseline + per-event GEX/price detail + price-action counterfactual) into
  B/C's 3-consecutive-zero-entry-day streak (2026-08-10 -> 08-12) found the
  cause: the GEX accel-zone strike-veto — which can abort an ENTIRE entry via
  require-both-sides — fires off a single, unsmoothed GEX-profile read.
  Stable-peak days (08-10) correctly predicted a real pin; drifting-peak days
  (08-11, early 08-12, peak moving 15-40pt between entry-slot reads) did not
  — SPX moved away and never returned. ~1-in-3 hit rate across the week's 18
  events. Not new to this week either: the mechanism has been the dominant
  skip cause fleet-wide since one_sided_entries_enabled=false shipped
  2026-07-16 (C ran an even longer 6-day solo dead streak 07-17->07-24 at
  HIGHER VIX, ruling out "low VIX explains it").
  AUDIT-BEFORE (verified directly against source, not assumed): gex_provider.
  py's _detect_clusters/_flush_cluster recompute GEXCluster.peak_strike fresh
  from scratch on every build_profile() call — zero persistence anywhere.
  gex_strike_adjuster.py's AdjusterConfig.accel_peak_locality_pts defaults
  25.0, unoverridden in either config_variant_b.json or config_variant_c.json.
  strategy.py's _brandon_get_gex_profile has a 3-min TTL cache; _calculate_
  strikes (delta-target picker) is the ONLY call site using force_refresh=
  True, meaning exactly one genuinely-fresh Polygon read happens per entry-
  slot attempt (B's slots are 30 min apart — well beyond the TTL and Polygon's
  own ~15-min update cadence, so consecutive entry slots are genuinely
  independent reads). _brandon_apply_strike_adjuster's call-side
  require-both-sides abort used to `return` immediately, so the put side
  never even ran — zero log evidence of what it would have decided.
  DESIGN: adjust_call_strike/adjust_put_strike gain a `prior_profile` param
  and two new AdjusterConfig fields (accel_peak_persistence_enabled=False,
  accel_peak_persistence_tolerance_pts=10.0). When enabled and a prior read
  is supplied, an in-locality accel-zone SKIP only fires if `prior_profile`
  ALSO shows a covering accel cluster (same expiry) whose peak is within
  tolerance of the current peak — otherwise the decision falls through to the
  existing decel/SHIFT check, then KEEP, exactly as if this accel zone
  weren't in locality range at all (no bolt-on "downgrade to KEEP" branch
  needed — a bare `continue` reuses the function's existing fallthrough).
  A third `force_unconfirmed` param (added in round 3, see below) makes that
  same fallthrough reachable even with `prior_profile=None`, for the case
  where the only "prior" available IS the current read itself (see round 3).
  strategy.py tracks `self._brandon_prior_gex_profile` (per-variant, in-
  memory, unpersisted — B and C run different entry-slot grids, so "the
  previous read" is inherently a per-variant concept; a restart or the first
  slot of the day simply runs with no prior, falling back to today's single-
  read behavior for that one slot). SECONDARY FIX: the call-side
  require-both-sides abort no longer `return`s immediately — an
  `already_aborted` flag now lets the put side still evaluate + log (never
  mutate) after a call-side abort, closing the observability gap.
  REVIEW — 3 rounds, all with independent adversarial verification of every
  finding (not just asserted):
  Round 1 (3 parallel dimension reviewers — correctness, live-trading
  blast-radius, test-coverage — each finding independently re-derived by a
  fresh verifier) found and fixed: (a) HIGH — a self-comparison bug. The
  rotation guard only stopped the POINTER (self._brandon_prior_gex_profile)
  from being reassigned on a repeat sighting of the same profile; it did NOT
  stop that already-rotated pointer from being read back out and handed to
  the confirm check as `prior_profile` against the very profile it was set
  from — a real self-comparison (trivially "confirmed" at 0pt drift,
  indistinguishable in the log/reason string from a genuine independent
  second read). Reproducible via an entry retry reusing the same
  force-refresh cache write (ENTRY_RETRY_DELAY_SECONDS=15s < the 30s
  force-refresh sibling-reuse window), or via any of _brandon_get_gex_
  profile's stale-fallback branches (failure cooldown, spot<=0, fetch
  exception) returning the same cached profile. Fixed in round 1: `prior_
  profile` is nulled to None whenever its fetched_at matches the current
  profile's — a prior that IS the current read is not an independent read.
  (Refined in round 3 below — nulling to None alone turned out to conflate
  this case with "no prior has ever been read," which needs different
  handling; `force_unconfirmed` is the final mechanism.) (b) LOW — a
  misleading "no matching accel zone in prior read" log message when a
  covering cluster WAS found but on a mismatched expiry; split into an
  explicit 3-way branch. (c) MEDIUM test-coverage gaps — missing put-side
  mirrors for two call-side tests, no exact-tolerance-boundary test, no
  dual-side (call+put simultaneously) integration test, and the existing
  same-fetched_at rotation test only asserted the pointer (trivially true
  either way), never the actual confirm-check outcome — all closed with new
  tests, including a precise unittest.mock.patch.object spy test asserting
  prior_profile=None is what actually gets PASSED to the adjuster on a
  same-fetched_at reuse (the mechanism-level proof, since the SKIP/KEEP
  ACTION alone can coincide between "self-confirmed" and "no-prior-
  available" when the peak is in locality either way).
  Round 2 (fresh independent reviewer, no knowledge of round 1's findings,
  + independent verification) converged on the round-1 mechanism: traced
  every return path of _brandon_get_gex_profile by hand and confirmed the
  fetched_at-based guard is generically correct across all of them;
  confirmed the new tests genuinely exercise what they claim (no
  tautological asserts); found only a trivial unresolved forward-reference
  type hint (GEXCluster referenced but not imported in gex_strike_adjuster.
  py — fixed) and a version-history pointer gap. No logic defects.
  Round 3: triggered by a SEPARATE follow-up review (a 3-lens interaction
  check — breach-exit, defensive overlay, and a full state-assumption sweep
  across bots/hydra/brandon/ — run specifically to clear the feature for its
  first real activation, accel_peak_persistence_enabled=true on variant C
  only) whose adversarial verifier, while confirming all three lenses clear,
  independently surfaced a real gap the three lenses themselves weren't
  scoped to catch: round 1's "null to None whenever fetched_at matches" fix
  made a same-profile ENTRY RETRY (ENTRY_RETRY_DELAY_SECONDS=15s, comfortably
  inside the 30s force-refresh sibling-reuse window, so a retry 15s later
  reads back its own prior write) collapse to prior_profile=None — which the
  adjuster treats identically to "no prior has EVER been read," the
  intentional legacy-SKIP path for the genuinely first evaluation of a day.
  Fail-safe in direction (skews toward MORE skipping, never toward placing
  something a clean read would have blocked) but it silently reverted some
  retried entries to pre-persistence-gate behavior while looking identical
  in the logs to a genuine decision — directly undermining the point of the
  C-only observation trial this flip exists to run. Fixed: a new
  `force_unconfirmed: bool = False` param on adjust_call_strike/
  adjust_put_strike, set by the caller specifically for the same-fetched_at
  case, distinct from `prior_profile=None`. It routes straight to
  "unconfirmed" (fall through to SHIFT/KEEP with a distinct log detail:
  "re-evaluating the same GEX read as before — no new independent
  confirmation available yet") without touching prior_profile at all,
  leaving the true "no prior ever" first-of-day path untouched. A follow-up
  fresh-eyes round on this specific fix (single reviewer + independent
  verifier) converged: threading verified symmetric across call/put, all 4
  combinations of (persistence enabled/disabled) x (force_unconfirmed
  True/False) traced and confirmed correct, no AttributeError path, both
  strategy.py call sites verified correct, the retry-timing premise
  re-verified against current source (not stale) — no new logic defects.
  5 SEPARATE NEGATIVE CONTROLS actually run across all 3 rounds (sabotage ->
  confirm RED -> restore -> confirm GREEN), not just asserted: the
  persistence-confirmation check itself; the prior-profile rotation
  ordering (reproducing the round-1 self-comparison bug by re-deriving
  prior_profile AFTER rotating instead of before); the put-side
  observability fix (restoring the old early-return); the round-1 fix
  itself (disabling the fetched_at-nulling guard, confirmed the spy test
  goes red); and the round-3 force_unconfirmed fix (reproducing the exact
  retry-collapse-to-SKIP bug, confirmed both the mechanism-level spy
  assertion and a new outcome-level test — an entry that should KEEP on
  retry instead SKIPs — go red, then green on restore).
  Full suite: 2239 passed / 15 skipped / 0 failed (was 2234 after round 2,
  2228 after round 1, 2208 before this feature).
  New/extended tests: tests/test_brandon_gex_strike_adjuster.py (21 new,
  36 total) and tests/test_brandon_strategy_integration.py (10 new, 87
  total).
  NOT DONE, DELIBERATELY: accel_peak_locality_pts (the 25pt buffer) itself
  is untouched — the same distance range produced both this week's one
  correct veto and its incorrect ones, so shrinking it would trade one
  error type for another without fixing the actual (unsmoothed-single-read)
  defect. one_sided_entries_enabled is untouched — every skip this week was
  call-side; loosening require-both-sides would produce put-only entries,
  the exact pattern the onesided_entry_negative_expectancy memory documents
  as net-negative. No 3-read/2-of-3 confirmation window — a simple 2-reads-
  agree gate is the right first step; a longer window would push the
  earliest possible confirmed veto to ~1 hour into the session for
  marginal, currently-unvalidatable robustness gain against an 18-23-event
  forensic sample. config_variant_b.json/config_variant_c.json are NOT
  edited here — flipping accel_peak_persistence_enabled on is a deliberate,
  separate, auditable follow-up: C first (zero live risk), observe
  entries-taken vs. SKIP-avoided over a handful of trading days, then B.
- Direct-Telegram bypass for CRITICAL/HIGH alerts (2026-08-04, THE GOLDEN
  LOOP). Closes the gap the SAME-DAY "alert-delivery reliability hardening"
  entry below explicitly deferred ("NOT DONE, DELIBERATELY: a fully
  independent bypass-Pub/Sub channel") — that fix made Pub/Sub publish
  failures retry + dead-letter, but every path still depended on Pub/Sub
  itself; a real Pub/Sub outage would still silence a CRITICAL/HIGH alert
  completely. User: "let's fix it properly... that is how we should do
  every single feature or fix on this project too" — referring to THE
  GOLDEN LOOP, ported the same day from the Adventist Intelligence project
  (`feedback_golden_loop.md`): plan -> AUDIT-BEFORE -> implement -> test
  (with explicit negative-control proof) -> AUDIT-AFTER (4 rounds here, not
  3 — round 3 found something non-cosmetic) -> converge. This entry is
  written in that shape.
  AUDIT-BEFORE (done before writing a line of code, not assumed): the
  codebase already talks to the Telegram Bot API directly in THREE places —
  bots/hydra/telegram_commands.py's command-poller reply (variant-A-only,
  unusable as a bypass from B/C/D/E), services/homer/main.py's
  send_telegram_alert (closest shape — standalone, no poller coupling),
  cloud_functions/alert_processor/main.py's own send (IS the Pub/Sub path
  itself). None share code. shared/secret_manager.py had getters for every
  OTHER credential type but no get_telegram_credentials(). Conclusion: add
  ONE new getter + ONE new small module, don't write a 4th duplicate, don't
  refactor the other 3 (live, working, unrelated code paths — out of scope).
  IMPLEMENT: shared/secret_manager.py gained get_telegram_credentials()
  (byte-identical pattern to get_saxo_credentials() etc.). New module
  shared/telegram_direct.py: send_telegram_direct(message, title,
  credentials=None) -> bool, POSTs directly to api.telegram.org, Markdown-
  then-plaintext-on-non-200 fallback (mirrors HOMER's proven pattern), 5s
  timeout/attempt, never raises. shared/alert_service.py::AlertService
  gained _attempt_telegram_bypass, called from send_alert's failure tail for
  CRITICAL/HIGH only, right after _write_dead_letter — credentials cached
  on the instance (only on success, so a transient fetch failure retries
  next trigger rather than being treated as permanent).
  4 ROUNDS OF REVIEW FOUND AND FIXED REAL BUGS (this is what AUDIT-AFTER is
  for):
  Round 1 — (a) a double-fetch bug: the original wiring passed a still-None
  credential cache straight into send_telegram_direct, which would then
  re-fetch internally, doubling Secret Manager timeout exposure on exactly
  the failure path the worst-case accounting cares about most. Fixed to
  short-circuit (skip the Telegram POST attempt entirely) if the one fetch
  attempt already failed. (b) a bot-token leak risk: a `requests` connection
  exception can embed the token in the URL it's raised from; telegram_
  direct.py's except block now redacts it before logging (the SAME risk
  bots/hydra/telegram_commands.py's _TokenRedactingFilter was built to close
  for that module — this is a second instance of the identical lesson, not
  covered by that filter since it's instance-based and this is a stateless
  function). (c) recomputed + corrected the shutdown-margin accounting
  (TimeoutStopSec raised 75s->95s hydra units / 90s->110s broker at this
  point in the review).
  Round 2 — found the round-1 "95s" figure still undercounted two terms:
  _write_dead_letter's own 2s bounded flock wait, and (bigger) that the 5
  hydra*.service units — unlike calypso-broker.service — never set
  GCP_PROJECT, so shared/secret_manager.py's is_running_on_gcp()/
  get_project_id() were paying ~3s of metadata-server HTTP round-trips
  before EVERY Secret Manager fetch on those units, not just this bypass.
  Fixed properly, not just re-documented: added Environment="GCP_PROJECT=
  calypso-trading-bot" to all 5 hydra*.service files, matching the broker's
  own already-proven-in-production setting — this actually removes the
  latency system-wide rather than just accounting around it. TimeoutStopSec
  raised again to 100s (hydra units, 88s worst case) / confirmed 110s
  (broker, 96s worst case) still held.
  Round 3 — found something non-cosmetic: the "Pub/Sub client never
  initialized at all" branch in send_alert (not on GCP, or a sustained
  outage the lazy-reinit hasn't recovered from) used to return early WITHOUT
  ever writing a dead-letter record or attempting the bypass — a MORE severe
  case of the exact "Pub/Sub is down" scenario this whole feature exists
  for, silently unprotected by it. Fixed: that branch now falls through to
  the same dead-letter-write + CRITICAL/HIGH-bypass logic as the publish-
  exception tail. Also caught a stale "95s" left in one comment by an
  earlier edit pass that didn't match the actual 100s directive four lines
  later — corrected.
  Round 4 — converged, no new findings. Independently reproduced the full
  suite pass count and confirmed no stray dead-letter files were left by
  the test run.
  Every fix above was proven, not just asserted: THREE separate negative
  controls were actually run during this work (not hypothetical) — the
  bypass call itself disabled -> 6 tests red, restored -> green; the
  credential short-circuit fix disabled -> the specific test for it red
  (showing the double-fetch), restored -> green; the round-3
  never-initialized fix disabled -> its 2 new tests red, restored -> green.
  Full suite: 2208 passed / 15 skipped / 0 failed (was 2182 before this
  feature, climbing from 2153 across the whole 2026-08-04 alert-reliability
  day of work). New test file: tests/test_alert_telegram_bypass.py (26
  tests).
  NOT DONE, DELIBERATELY: telegram_commands.py's and HOMER's existing
  Telegram-send call sites are NOT refactored to use the new shared module
  (scope discipline — live, working, unrelated code paths). Email is not
  given a second bypass channel (Telegram-only; email already shares the
  same Cloud Function as Telegram in the normal path, and Telegram is the
  higher-value "operator's phone, immediate visibility" target).
- Alert-delivery reliability hardening (2026-08-04). While checking for
  anything left half-dug from the 2026-08-03 audit, live logs showed a NEW
  bug on every strategy restart that night: `shared/alert_service.py`'s
  Pub/Sub publish-failure handler logged `f"Failed to publish alert to
  Pub/Sub: {e}"`, but the underlying call (`future.result(timeout=5)`) raises
  a bare `concurrent.futures.TimeoutError()` on timeout — which stringifies
  to `""` — so the log line was `"Failed to publish alert to Pub/Sub: "` with
  nothing after the colon. Confirmed via 30-day history this recurs (5x/30d
  fleet-wide), not a one-off. User's directive, verbatim: "I wanna do it
  properly... this is going to be a world class product used by a lot of
  people in the future... make the basics 100% correct" — same rigor as the
  2026-08-03 fixes: plan -> implement -> test -> adversarial review to
  convergence (3 rounds; rounds 1-2 found 8 real issues across the surface
  the fix touched, round 3 converged clean).
  CORE FIX (`shared/alert_service.py`): a `describe_exception(e)` helper
  (`f"{type(e).__name__}: {text}" if text else type(e).__name__` — never
  blank) applied at every exception-log site in the file. Publish failures on
  CRITICAL/HIGH now get ONE retry (2 attempts total, ~11s worst case) before
  giving up — this alert may be the last thing a process does before shutting
  down (e.g. an emergency-exit alert), so it's worth the bounded second try;
  MEDIUM/LOW stay at 1 attempt (keeps the common case — routine
  bot_started/bot_stopped alerts on every restart — at the old latency). A
  publisher that failed to construct at process start (`_initialized=False`)
  no longer stays permanently local-only for the process's life: `send_alert`
  now lazily retries `_initialize()` on a 60s cooldown. Alerts that still
  fail after the retry budget get a durable, independent record appended to
  `data/failed_alerts.jsonl` (`FAILED_ALERTS_PATH`) — greppable without
  journalctl/GCP access, so a lost alert isn't purely a line in a log file
  that rotates.
  ADVERSARIAL REVIEW FOUND AND FIXED REAL BUGS IN THE FIX ITSELF (this is the
  point of the process): (1) the lazy-reinit block originally reused the
  pre-existing `_gate_lock` (acquired by `_apply_alert_gate` on EVERY
  send_alert call) while holding it across `_initialize()` — an UNBOUNDED
  call (GCP metadata-server discovery has no timeout). A hung reinit on one
  thread would have blocked every OTHER thread's alerts, including a
  concurrent CRITICAL alert from the Telegram poller thread on variant A.
  FIXED: dedicated `_reinit_lock`, acquired NON-BLOCKING
  (`acquire(blocking=False)`) — if another thread is already mid-reinit, the
  caller falls straight through to local-log-only instead of waiting on a
  possibly-hung call. Proven with a real multi-threaded test (not mocked):
  thread A stuck in a faked hanging `_initialize()`, thread B (same instance)
  returns in <1s instead of blocking. (2) `_write_dead_letter`'s
  `fcntl.flock()` was originally a plain BLOCKING call with no timeout — this
  file is shared by every process that constructs an AlertService (variants
  A-E and calypso-broker), and a real Pub/Sub outage (the scenario this file
  exists for) tends to hit them all near-simultaneously, and the write can
  fire synchronously from the main trading loop. FIXED to match this
  codebase's own established `LOCK_EX|LOCK_NB` polling pattern
  (`shared/token_coordinator.py`'s `_acquire_lock`) with a 2.0s bound
  (`DEAD_LETTER_LOCK_TIMEOUT_S`) — skips the write on contention rather than
  blocking. Proven with a real 12-thread concurrent-write test asserting the
  file stays fully parseable, not just that flock() was called. (3) the
  shutdown-margin comment originally claimed the ~11s CRITICAL/HIGH retry
  worst case was "well inside" the 2026-08-03 TimeoutStopSec budgets —
  corrected to an honest accounting: it CAN stack with a single non-abortable
  order-family retry's ~55s worst case within the same shutdown sequence
  (55+11=66s of the 75s hydra-unit budget), leaving materially less headroom
  than the original comment implied, though the remaining shutdown steps are
  fast/local/no-further-network-calls so this should still be sufficient —
  not closed to zero risk, just accurately described now. Deliberately NOT
  wired to `shared.ib_retry.SHUTDOWN_EVENT` (unlike IBKR session/market
  retries): continuing to retry a CRITICAL/HIGH alert during shutdown has
  real safety value (it's often the alert telling an operator something
  needs attention), so aborting it the instant shutdown begins would defeat
  the point — this is a deliberate difference from the 2026-08-03 pattern,
  not an oversight.
  SAME BUG CLASS, FOUND ELSEWHERE BY REVIEW, NOT THE ORIGINAL SWEEP: 4 sites
  in `bots/hydra/base_strategy.py`/`strategy.py` where a CRITICAL/HIGH
  alert's OWN send-failure was logged at `logger.debug` (invisible in
  production) and mislabeled "(non-fatal)" — an untracked orphan broker leg,
  a deferred settlement booking, a failed short buy-back, and (found by
  round-1 review, not the original sweep) a STUCK EMERGENCY CLOSE
  (`_emergency_close_alert_once`) — arguably the most severe of the four, on
  the escalation path for a live unhedged/stuck position. All 4 now
  `logger.error` + `describe_exception`, matching 5 other "alert send failed"
  sites in the same two files that already used warning/error (these were
  regressions from that established convention, not intentional design).
  ROUND-2 REVIEW FOUND THE ENTIRE `bots/hydra/brandon/strategy.py` MODULE —
  the code that runs LIVE on variant B and dry-run-shadow on C — had NEVER
  BEEN CHECKED (the original sweep and round 1 both stopped at
  base_strategy.py/strategy.py). Fixed 5 sites: the chokepoint
  `_brandon_send_telegram` that every single Brandon alert call routes
  through (the fix that actually matters — includes CRITICAL alerts like the
  overlay-partial-fill naked-position warning), 3 call sites whose own
  try/except around that chokepoint is dead code today (the chokepoint never
  raises) but fixed for consistency/defense-in-depth, and one adjacent
  non-alert site (`log_daily_summary`'s settlement-booking except block) with
  the identical blank-message + misleading "(non-fatal)" pattern on a real
  P&L-mismatch risk (same class as the 2026-07-21 dashboard/cumulative
  mismatch documented elsewhere in this file).
  Also fixed: `services/argus/notify.py` (ARGUS's own health-check alert
  delivery) and `shared/data_recorder.py`'s `_safe_write` (wraps
  record_entry/record_stop/record_daily_summary — real trade-record
  persistence), same blank-`str(e)` pattern, diagnostics-only.
  NOT DONE, DELIBERATELY (AT THE TIME): a fully independent (bypass-Pub/Sub)
  delivery channel (e.g. direct Telegram API as a last resort when Pub/Sub
  itself is down) — confirmed ARGUS shares the same Pub/Sub-dependent path
  today, so it can't watchdog a full Pub/Sub outage either. Genuinely
  valuable, but expands the alert-delivery attack surface and secrets
  footprint; deserved its own discussion, not a decision folded into this
  fix. **SHIPPED LATER THE SAME DAY** — see the "Direct-Telegram bypass for
  CRITICAL/HIGH alerts" entry ABOVE this one; this note is kept for the
  historical record of the decision, not as a current gap. `data/
  failed_alerts.jsonl` has no retention sweep (unlike `pre_start_snapshot.sh`'s
  50-snapshot cap) — deliberately: this file grows only on genuine Pub/Sub
  failures (~5/30 days fleet-wide historically, each a few KB), an entirely
  different growth profile from a per-restart snapshot file; flagged, not
  forgotten. (Still true even with the bypass channel — the bypass reduces
  how OFTEN a lost alert needs the dead-letter file as its only record, it
  doesn't change the file's own growth profile.)
  Full suite: 2182 passed / 15 skipped / 0 failed (was 2153 before this fix).
  New test files: test_alert_publish_reliability.py (18, incl. 2 real
  multi-threaded concurrency tests), test_alert_send_failure_logging_
  2026_08_04.py (7), test_alert_diagnostics_argus_and_data_recorder_
  2026_08_04.py (3), test_brandon_alert_failure_logging_2026_08_04.py (6).
- Three fixes from a full-day, all-5-strategy audit (2026-08-03). A deep-dive
  audit (5 parallel agents, every entry for the day checked against DB+logs+
  state) found zero wrong trading decisions but surfaced 3 real issues, fixed
  to varying degrees of completeness based on confidence in the root cause and
  risk on live-trading code — implemented against a written plan, then 3
  rounds of adversarial review (found and closed 4 real issues across rounds
  1-2, converged clean on round 3).
  (1) CALENDAR DASHBOARD P&L BUG (D/E, schema-neutral, display-only) — FULLY
  FIXED. `_save_state_to_disk`'s `pnl_history` builder (`strategy.py`) used the
  credit-vertical formula (`call_spread_credit - call_spread_value`, written
  for A/B/C) unmodified for `CalendarEntry` (D/E), whose fields mean something
  structurally different (a calendar is a debit purchase, not a credit sale).
  Confirmed live: dashboard showed -$164 on E's open position; real P&L
  (`dc_calendar_snapshots` DB / `dc_open_trades.json` sidecar / heartbeat log,
  all agreeing) was -$8 — wrong in BOTH open phases (CALENDAR and TRANSFORMED),
  accidentally correct only in CLOSED. FIX: `isinstance(entry, CalendarEntry)`
  branch adds `entry.unrealized_pnl` (the existing, correct, phase-aware
  formula already used everywhere else calendar P&L is shown) instead, with a
  fresh/marks-not-loaded-yet guard mirroring the pre-existing IC pattern. No
  consumer changes needed (traced every reader of `pnl_history`; D/E's live
  dashboard already bypasses this field via the sidecar, but the underlying
  state-file data was objectively wrong and any future feature reading it
  would have silently inherited the bug). New tests reproduce the actual -$164
  vs -$8 incident numbers and pin a byte-identical non-regression test for the
  untouched A/B/C math (which had no direct test before either).
  (2) BROKER + STRATEGY-PROCESS SHUTDOWN HANG — BROKER FULLY FIXED, STRATEGY
  PROCESSES CONSERVATIVELY MITIGATED, ROOT CAUSE STILL OPEN. Today's deploy
  restart needed a forced SIGKILL across `calypso-broker` (84 processes) and
  `hydra`/`hydra_variant_{b,d,e}` — first occurrence in 2+ weeks of restarts.
  Broker root cause PROVEN: `_on_shutdown`'s `maintain_thread.join(timeout=30)`
  couldn't interrupt an in-flight `ensure_connected()` retry backoff (a plain
  `time.sleep()`, ~63s documented worst case) — landed exactly while the
  broker was mid OAuth-rehandshake. FIX: `shared/ib_retry.py` gained a
  process-wide `SHUTDOWN_EVENT` + `ShutdownRequested` exception + a new
  `abortable_on_shutdown` flag on `retry_with_backoff` (default True); the
  sleep between retry attempts is now `SHUTDOWN_EVENT.wait(delay)` instead of
  `time.sleep(delay)`, aborting within one tick once the event fires.
  `shared/ib_client.py:_ib_call` passes `abortable_on_shutdown=(family !=
  "orders")` — order placement/cancel/modify is DELIBERATELY NEVER abortable
  (bailing mid order-retry risks a naked/partial leg, strictly worse than a
  slow shutdown); every other family (session/market/portfolio/history/oauth)
  is fast-abortable, which is what actually fixes the broker (`ensure_connected`
  is family='session'). `_on_shutdown`'s body was extracted to a standalone,
  directly-testable `_shutdown_broker()` (`services/broker/main.py`) that sets
  `SHUTDOWN_EVENT` before joining; also added a specific `except
  ib_retry.ShutdownRequested: ... break` in the maintenance loop so a
  deliberate shutdown-abort can't be miscounted as a real re-auth failure and
  fire a false "session re-auth FAILED" alert (caught in round-1 review).
  STRATEGY PROCESSES (hydra/A, B, C, D, E): unlike the broker, these talk to
  IBKR only via `BrokerClient` (HTTP proxy, no `retry_with_backoff` of their
  own) and were NOT stuck in any order-placement retry at restart time (fleet
  was flat) — the exact mechanism that made them need SIGKILL is NOT fully
  root-caused. Deliberately did not guess at a fix inside the order-placement/
  emergency-close retry loops on a live-trading process without solid
  evidence. Instead: (a) `TimeoutStopSec` raised on all 6 units — broker
  30s->90s (comfortably above the ~63s non-abortable orders-family worst
  case), hydra*.service 30s->75s (above the 55s worst case for a SINGLE
  in-flight order HTTP call on a 7-contract B/C entry: `place_and_wait_for_
  fill`'s `min(30+3*(qty-1),45)=45s` server-side timeout + `BrokerClient`'s
  +10s pad). ROUND-2 REVIEW CAVEAT, STILL OPEN: this only bounds a single
  HTTP call — a full iron-condor entry can leg in up to 4 legs x 5 retry rungs
  each (`base_strategy.py`'s `PROGRESSIVE_RETRY_SEQUENCE`) with no
  shutdown-flag check between rungs/legs, so `TimeoutStopSec=75` does NOT
  guarantee a graceful stop if SIGTERM lands mid-entry-placement — it narrows
  the window materially (75s vs the old 30s, and vs the true worst case of
  session-hangs that used to compound across MULTIPLE calls) without closing
  it completely. Do not treat this as a closed fix for the strategy-process
  side. (b) `bots/hydra/main.py`'s `signal_handler` now logs a best-effort
  diagnostic snapshot (strategy state, in-progress entry number) on SIGTERM —
  read-only, try/except-wrapped, cannot affect shutdown behavior — so a
  recurrence's logs pinpoint the cause instead of requiring another round of
  journalctl archaeology.
  (3) B/C DELTA-TARGET "DEGRADED-DATA" GUARD — TELEMETRY ADDED, NO BEHAVIOR
  CHANGE (BY DESIGN). The guard (`brandon/strategy.py`, 4δ floor vs an 8δ
  target, added 2026-07-18 after a real -$138 phantom-loss incident) fired 4x
  in one afternoon on B, previously believed rare. Investigation: NOT a bug —
  the ~16% chain-hydration ratio observed is the near-deterministic output of
  a hardcoded `max_contracts_to_hydrate=80` cap on a ~500-strike chain, and a
  quiet/trending low-VIX session routinely pushes the 8δ target outside that
  window. This is a live-trading risk-parameter question (loosen the floor?
  raise the hydration cap and accept more Polygon load? leave it?), not
  something to decide unilaterally mid-fix — so this ships PURE TELEMETRY,
  zero behavior change. Schema v15 (`skipped_entries`: `hydration_pct`,
  `achieved_delta`, `target_delta`, `delta_floor`, all nullable, populated only
  on this specific skip path). ROUND-1 REVIEW CAUGHT A REAL BUG IN THE
  TELEMETRY ITSELF: hydration counts were first tracked as a per-instance
  counter, updated only on a fresh Polygon fetch — but the skip site always
  calls `_brandon_get_gex_profile(force_refresh=True)`, which can return a
  SIBLING VARIANT's just-cached profile (B and C share entry slots) without
  updating that counter, so `hydration_pct` could silently describe a
  DIFFERENT decision than the one it was attached to (or be stale/None) while
  the delta/target/floor fields were fine. FIXED properly, not patched around:
  `chain_total`/`hydrated_count` are now fields ON `GEXProfile` itself
  (`gex_provider.py`), stamped via `dataclasses.replace()` the moment a fresh
  fetch completes, before the profile is stored/cached anywhere — every reuse
  path (in-process TTL cache, cross-process shared-cache file, sibling-variant
  reuse under the fetch lock) now automatically carries correct counts with
  it, by construction, with no separate bookkeeping. `gex_shared_cache.py`'s
  JSON save/load updated to persist the two fields (`.get(...,0)`
  backward-compat for pre-2026-08-03 cache files).
  Full suite: 2153 passed / 15 skipped / 0 failed. New/updated test files:
  test_pnl_history_calendar_fix.py, test_delta_target_telemetry_v15.py,
  test_ib_retry.py (+7), test_ib_client.py (+1), test_broker_shutdown_hang_
  fix.py, test_main_sigterm_diagnostic.py, test_brandon_degraded_delta_guard_
  2026_07_18.py (+6), test_brandon_gex_shared_cache.py (+2).
- Entry-execution-FAILURE recording + alerting (2026-07-31, live incident on
  variant B). Entry #3 exhausted all order-placement retries — IBKR's paper
  matching engine accepted a market order but never filled it (confirmed via a
  direct broker query: the order was genuinely accepted, not rejected, and sat
  with zero fill despite a live moving 5-cent-wide market; a documented IBKR
  paper-API reliability issue, not a HYDRA logic bug) — and the failure
  produced ZERO operator-visible signal: no DB row, no dashboard detail (the
  entry-slot card rendered as a blank "window passed", indistinguishable from a
  slot that never happened), no alert — only a raw log line and an incremented
  in-memory counter (`daily_state.entries_failed`) nothing surfaced. FIX: new
  `_record_failed_entry` (HydraStrategy) reuses `_record_skipped_entry`'s
  plumbing (append to `daily_state.entries`, `skipped_entries` DB row) but sets
  a new `execution_failed` field/DB column (schema v14, additive
  `skipped_entries.execution_failed INTEGER NOT NULL DEFAULT 0`) and alerts via
  a new `AlertType.ENTRY_EXECUTION_FAILED` at explicit `priority=HIGH` — never
  inherited/LOW, since LOW/MEDIUM alerts are silently dropped when a variant's
  `alerts.enabled=false` (the severity bypass in `shared/alert_service.py` only
  lets HIGH/CRITICAL through regardless). Wired into the 4 retries-exhausted
  sinks: `strategy.py` (the live path — covers A directly, B/C by inheritance
  through Brandon's override), `double_calendar_strategy.py` (D) and
  `spy_double_calendar_strategy.py` (E) with `send_alert=False` (their configs
  document "dry-run path emits none" as an intentional no-paging invariant —
  DB visibility only, no Telegram), and `strangle_strategy.py` (unused). D/E/
  Strangle also pass `used_retry_loop=False` (they place in a single attempt,
  no retry ladder — the message correctly says "on the first attempt" instead
  of claiming a retry count that never happened). Dashboard: a new "FAILED"
  disposition (backend `_entry_disposition`, checked before "SKIPPED" — a
  failure sets both `call_side_skipped`/`put_side_skipped=True` too, for
  backward-compat with existing "no real position" checks) rendered as a
  visually distinct red card/badge/dot across every surface that previously
  computed skip status independently and would otherwise have silently
  collapsed a failure into a routine skip: `EntryCard.tsx`, `EntryTimeline.tsx`,
  `Comparison.tsx`, `EntryGrid.tsx`, the iOS Scriptable widget, and the
  `/api/widget` backend. HERMES's daily analyst (`services/hermes/
  data_collector.py`) also checks `execution_failed` first in both
  `_classify_outcome` and its `entry_type` classification — before this fix it
  would have narrated the failure as a clean, uneventful $0 "put_only" trade.
  Found and fixed via 3 rounds of adversarial review (8 findings total across
  rounds 1-2, converged clean on round 3) after implementing against a written
  plan. +26 tests: 4 new files (test_entry_execution_failure.py,
  test_execution_failed_v14.py, test_calendar_execution_failure.py,
  test_hermes_execution_failed_classification.py) plus updates to
  test_strangle_strategy.py and test_dashboard_variant_buffer_margin.py. Full
  suite 2056 passed / 15 skipped / 0 failed.
- Skipped-entry reasons rewritten for humans (2026-07-22). The dashboard
  skip-reason strings carried backend jargon (MKT-011 / MKT-032 / MKT-010 /
  Downday-035 / Upday-035 / "require-both-sides: one-sided (GEX-skip)
  suppressed") that means nothing to a non-operator. Rewrote every user-facing
  `_record_skipped_entry` reason to say WHY in plain English + show the actual
  minimums: the credit gate now reads "Not enough premium … call spread $X
  (need ≥ $min), put spread $Y (need ≥ $min). Skipped (credit gate)."; the
  require-both-sides skip explains one-sided = negative expectancy / naked-short
  tail risk and names the cause (credit gate vs GEX accel-zone skip); illiquid
  wings + conditional-no-trigger similarly de-jargoned. Backend logs keep their
  MKT codes; only the display strings changed. Also RE-ENABLED C's defensive
  overlays (config) now that the sizing fix below is deployed — C runs the full
  Brandon strategy again (loads at its next restart).
- Defensive-overlay LIVE placement over-placement fix + atomicity (2026-07-21,
  found while auditing the B<->C live-paper swap). Overlay leg `quantity` is
  ALREADY scaled to contracts_per_entry (a butterfly is 10/20/10 = 40 for a
  10-lot variant). The live path looped `for q in range(leg.quantity)` and each
  `_place_option_order` placed contracts_per_entry contracts → it attempted
  leg.quantity × contracts_per_entry (400 on a 40-contract butterfly). The
  max_contracts_per_underlying cap then truncated it mid-structure into a naked
  short with NO unwind. It DID fire once in production — on C, 2026-06-10: the
  overlay placed contracts_per_entry × intended qty (98 vs 14 contracts, i.e.
  C's 7-fold) and C's overlays were DISABLED that day as the mitigation (config
  `_comment_disabled`: "re-enable only after the sizing fix"). It has not
  recurred only because C's overlays stayed OFF and B is dry-run — this IS that
  long-awaited sizing fix. FIX: `_place_option_order`/`_place_option_order_ib`
  gained an optional `quantity` (None ⇒ contracts_per_entry, so the IC path is
  byte-identical; ORDER-006 now validates the ACTUAL requested qty). The overlay
  caller places each leg ONCE at leg.quantity, chunked by max_contracts_per_order,
  and is now ATOMIC — a partial fill unwinds every filled leg
  (`_brandon_unwind_overlay_legs` → `_flatten_accumulated_partial`) instead of
  tracking a partial structure / stranding a naked short. +10 tests
  (test_brandon_overlay_live_sizing_2026_07_21).
- Overlay double-book guard, atomic (2026-07-18, follow-up to the reconcile fix
  below). The aggregate-only overlay booking (hedge whose entry is absent from
  daily_state at settle) had NO idempotency guard, so a settle-sweep re-run after a
  restart could double-book it into gross_pnl (B dry-run; C is overlay=False, immune).
  FIX: a UNIFIED per-day set `_brandon_overlay_booked` that BOTH booking paths check +
  set (also closes the presence-FLIP double-book: absent one run, present the next).
  Persisted in hydra_state.json ATOMICALLY with total_realized_pnl (same os.replace,
  same same-day-gated restore) — NOT the hedge sidecar, which would let a crash restore
  the guard without the booked total and silently LOSE the overlay (2nd review finding).
  +9 tests (booking/flip idempotency + save co-location + same-day-gated restore); two
  adversarial-review rounds each caught a real bug pre-deploy. B/C only reconcile 100%
  including on overlay + restart days.
- Reconciliation overlay-aware + per-day unattributed-overlay column (2026-07-18).
  The per-entry identity `sum(trade_entries.realized_pnl) == daily_summaries.gross_pnl`
  had two blind spots that made an audit false-flag: (a) a Brandon defensive-overlay
  hedge whose entry is ABSENT from daily_state at settle (post-close / cross-day
  restart) is booked aggregate-only — its P&L lands in gross but on no entry
  (2026-07-07 B: per-entry sum $2925 vs gross $392, EXPECTED not corruption); (b)
  pre-feature days (< 2026-07-02) carry unbooked 0.0 realized_pnl. FIX: new
  `_unattributed_overlay_pnl()` (0.0 base; Brandon sums settlements whose entry is
  absent) so the live RECONCILE guard identity is `sum(entries) + unattributed ==
  total_realized_pnl` — derived from THIS process's settlement sweep so a genuine
  aggregate double-book still surfaces as drift (not masked). Persisted via schema
  **v13** (`daily_summaries.unattributed_overlay_pnl`, additive/nullable, HOMER
  parity) — forwarded through `_record_daily_summary_to_db`'s payload — so slot_edge
  + offline audits reconcile too. slot_edge cross-check now overlay-adjusted +
  floored to the reliable window (>= 2026-07-02) + intersected to days present in
  daily_summaries (no cross-table drift). Daily/cumulative P&L UNCHANGED (this only
  fixes attribution + the reconciliation check). +15 tests; 4-lens adversarial review
  caught the un-forwarded-key bug (column would have written NULL) pre-deploy. See
  memory `per-slot-edge-and-realized-pnl`.
- Brandon degraded-Polygon-data guard + honest dry-run (2026-07-17). ROOT CAUSE:
  on 2026-07-17 (OPEX Fri) Polygon's greek feed degraded (80/1000 strikes hydrated);
  Brandon's 8δ delta-target, with no real 8δ strikes in a sparse chain, sold the
  "closest" — actually ~0.5-1δ, far-OTM, ~$0.05 premium. Every existing guard missed
  it (profile FRESH so the stale-guard passed; pick BELOW the max-delta clamp; ~$0
  credit so the too-rich price-veto couldn't fire). Impact: live C churned
  protective-long leg-in/unwinds (the net-credit guard-floor saved it → 0 positions,
  no orphans); dry-run B booked 3 phantom $0-credit ICs → dashboard −$138 (commission
  only). A (non-Brandon) unaffected (+$403 normal day). FIXES: (1) find_strike_at_delta
  gains return_delta=True → (strike, achieved_delta) so the caller sees the picked delta
  (bots/hydra/brandon/gex_provider.py). (2) DEGRADED-DATA FLOOR in _calculate_strikes: a
  short below target × min_delta_pct_of_target (config under delta_target_strike_selection,
  default 0.5 → 4δ for an 8δ target) → SKIP the entry (operator-chosen over the
  OTM-multiplier fallback) via entry.abort_entry_reason + a MEDIUM Telegram alert. (3)
  _initiate_entry routes abort_entry_reason to a new _skip_degraded_entry clean-skip (early
  + post-dispatch, mirrors _skip_require_both_sides). (4) HONEST-DRY-RUN: base
  _simulate_entry skips a sim entry when NO ACTIVE side clears the net-credit floor
  (min_net_credit_per_contract×100 = $5/ct), gated by skip_dryrun_below_net_credit_floor
  (default true) — so B stops booking phantom $0 ICs, but a legit one-sided or
  one-viable-leg entry still books (call_active/put_active mirror _execute_entry;
  2026-07-18 review fix). Both knobs config-reversible; A unaffected (non-Brandon → real
  credit). NOTE: the delta-floor guard's one-sided neutrality assumes B/C keep
  one_sided_entries_enabled=false — revisit if that flag is flipped back on. +15 tests
  (test_brandon_degraded_delta_guard_2026_07_18); 4-lens adversarial review + focused
  re-verify → SAFE-TO-DEPLOY. See memory `brandon_degraded_polygon_data_guard`.
- Require-both-sides / no one-sided entries on B+C (2026-07-16). Data showed one-sided
  entries (put_only-dominant) win 69-77% but carry NEGATIVE expectancy on B and C
  (naked-short fat tail), net -$14.3k (B) / -$3.8k (C) over 2026-07-01..07-16 — worse
  than full ICs. Set `one_sided_entries_enabled=false` on B+C. The credit-gate one-sided
  path (MKT-011/032/039/040) already skips when the flag is false; ADDED coverage for the
  two paths that bypassed it: (a) the Brandon GEX strike-adjuster SKIP (bots/hydra/brandon/
  strategy.py) now sets `entry.require_both_abort` instead of routing one-sided when the
  flag is off, and `_execute/_simulate_entry` return without placing; (b) `_initiate_entry`
  gains two guards (pre-dispatch for credit-gate/E6 one-sided, post-dispatch for the GEX
  abort) that funnel to a new `_skip_require_both_sides` clean-skip helper (recorded as a
  SKIP, not a failed retry). Fully config-reversible (flag→true restores prior behavior).
  Flag reads are getattr-defensive (default true). +6 tests (test_brandon_strategy_integration
  TestRequireBothSidesGuards + TestStrikeAdjusterLive). Also fixed a residual data bug:
  variant C 2026-07-06 two full_ic entries carried stale realized_pnl (-1489.54/-734.49 vs
  the correct +105.00/0.00; SPX never breached, peak cost-to-close $315) — a leftover of the
  07-06 stale-SPX settlement bug (daily/cumulative were corrected, entry rows were not).
  Corrected in C's backtesting.db (GCS-backed); every reliable day now reconciles
  SUM(realized_pnl)==gross_pnl. See memory `onesided_entry_negative_expectancy`.
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
