"""Dashboard configuration via environment variables with sensible defaults."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Base path for CALYPSO installation (dashboard reads HYDRA's data from here)
    calypso_root: Path = Path("/opt/calypso")

    # ── PRIMARY (main page) data source ──────────────────────────────────
    # The main dashboard page + WebSocket + widget read these "canonical"
    # paths. 2026-06-02: the operator made variant C the LIVE canonical
    # strategy, so these point at C's data (data/variant_c/*). A is demoted to
    # a dry-run shadow and is surfaced as an explicit variant "a" on
    # /comparison (its real paths are the variant_a_* fields below).
    # `primary_label` is shown in the header so it's unambiguous which strategy
    # the main page is displaying. (To switch the primary back to A, point
    # these four at data/hydra_* + logs/hydra/ and set primary_label/bot_config_file.)
    primary_label: str = "C · LIVE (Brandon narrow 5/10pt)"
    bot_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_c.json")
    hydra_state_file: Path = Path("/opt/calypso/data/variant_c/hydra_state.json")
    hydra_metrics_file: Path = Path("/opt/calypso/data/variant_c/hydra_metrics.json")
    backtesting_db: Path = Path("/opt/calypso/data/variant_c/backtesting.db")
    position_registry_file: Path = Path("/opt/calypso/data/position_registry.json")

    # Log file (primary = C)
    hydra_log_file: Path = Path("/opt/calypso/logs/hydra_variant_c/bot.log")

    # ── MARKET-DATA (SPX/VIX price chart) source ─────────────────────────
    # The SPX/VIX price chart is ACCOUNT-AGNOSTIC — every variant (A/B/C)
    # watches the SAME S&P 500 index, so the candle chart should come from the
    # DENSEST recorder, independent of which variant is "primary" for trades.
    # Variant A samples ~4-8 ticks/min (real candle bodies + wicks); variant C
    # samples ~1 tick/min (every 1-min bar collapses to a flat doji → the
    # "spare crosses" look). Pointing the price chart at A's dense DB/log gives
    # full, accurate candlesticks while the trade overlay (strikes, entries,
    # stops, P&L) stays on the primary's data above. Override with
    # DASHBOARD_MARKET_DATA_DB / DASHBOARD_MARKET_LOG_FILE if A is ever retired
    # (point at whichever variant samples densest). If this source has no data,
    # the chart falls back to the primary's sparse data and the frontend renders
    # a clean line instead of crosses (never broken-looking).
    market_data_db: Path = Path("/opt/calypso/data/backtesting.db")
    market_log_file: Path = Path("/opt/calypso/logs/hydra/bot.log")

    # Comparison mode (head-to-head dry-run experiment).
    # When comparison_mode_enabled = True, the dashboard exposes:
    #   - /api/variants/* endpoints reading each variant's parallel state/metrics/db
    #   - /comparison page in the SPA (hidden from nav otherwise)
    # Each non-A variant is a second HYDRA process running in dry mode with a
    # different config (config_variant_<id>.json), writing to data/variant_<id>/.
    # The router builds its registry by enumerating variant_<id>_state_file fields
    # below — to add a variant D, add 5 fields here and the router picks it up.
    comparison_mode_enabled: bool = False

    # Variant A — baseline HYDRA (75pt MKT-027). 2026-06-02: demoted to a
    # DRY-RUN SHADOW (records to its own DB only; Sheets + alerts off). Now an
    # EXPLICIT variant pointing at the canonical data/hydra_* + logs/hydra/
    # paths (it was special-cased to the main paths before C became primary).
    variant_a_label: str = "A (baseline 75pt · dry-run shadow)"
    variant_a_state_file: Path = Path("/opt/calypso/data/hydra_state.json")
    variant_a_metrics_file: Path = Path("/opt/calypso/data/hydra_metrics.json")
    variant_a_backtesting_db: Path = Path("/opt/calypso/data/backtesting.db")
    variant_a_log_file: Path = Path("/opt/calypso/logs/hydra/bot.log")
    variant_a_config_file: Path = Path("/opt/calypso/bots/hydra/config/config.json")

    # Variant B — Brandon Trojan Horse stack with NARROW 5/10pt widths (same
    # as C) at 10c, on a 7-slot entry grid (09:45 / 10:15 / 10:45 / 11:15 /
    # 11:45 / 12:15 / 12:45) — the HIGH-CADENCE variant vs C's 3 slots.
    # (History: originally 7-slot × 15c; trimmed to a 4-slot grid 2026-05-13 on
    # commission-drag concerns, then re-widened back to the full 7-slot grid —
    # so the old "4-slot" label was stale, corrected 2026-07-14.) Brandon TP /
    # GEX adjuster (peak-localized SKIP) / GEX breach exit / defensive overlay
    # all LIVE; HYDRA stop runs in shadow mode only.
    variant_b_state_file: Path = Path("/opt/calypso/data/variant_b/hydra_state.json")
    variant_b_metrics_file: Path = Path("/opt/calypso/data/variant_b/hydra_metrics.json")
    variant_b_backtesting_db: Path = Path("/opt/calypso/data/variant_b/backtesting.db")
    variant_b_log_file: Path = Path("/opt/calypso/logs/hydra_variant_b/bot.log")
    variant_b_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_b.json")
    variant_b_label: str = "B (Brandon narrow, 7-slot grid)"

    # Variant C — Brandon Trojan Horse stack + Brandon's narrow 5/10pt spreads.
    # Same Brandon stack as B; only spread width differs (narrow_spread.enabled=true
    # overrides MKT-027 with 5pt at VIX<22, 10pt at VIX>=22).
    variant_c_state_file: Path = Path("/opt/calypso/data/variant_c/hydra_state.json")
    variant_c_metrics_file: Path = Path("/opt/calypso/data/variant_c/hydra_metrics.json")
    variant_c_backtesting_db: Path = Path("/opt/calypso/data/variant_c/backtesting.db")
    variant_c_log_file: Path = Path("/opt/calypso/logs/hydra_variant_c/bot.log")
    variant_c_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_c.json")
    variant_c_label: str = "C (LIVE · Brandon narrow 5/10pt)"

    # ── Variant D — Strategy D "DC Time Machine" (PRE-STAGED, not yet active) ──
    # Paths defined here so Phase 7 (D-aware rendering) is a small diff, but 'd'
    # is intentionally NOT in _VARIANT_IDS (routers/variants.py) yet: D is a
    # multi-day net-DEBIT double calendar that the current IC-shaped comparison
    # would render as a credit + a fabricated width. Activate in Phase 7.
    variant_d_state_file: Path = Path("/opt/calypso/data/variant_d/hydra_state.json")
    variant_d_metrics_file: Path = Path("/opt/calypso/data/variant_d/hydra_metrics.json")
    # NOTE: this is D's MARKET-TICK DB (shared DataRecorder), for the variant
    # framework if 'd' is ever added to _VARIANT_IDS. D's CALENDAR tables live in
    # a SEPARATE file, data/variant_d/dc_calendar.db, surfaced via /api/dc/status
    # (routers/dc.py), NOT through this field.
    variant_d_backtesting_db: Path = Path("/opt/calypso/data/variant_d/backtesting.db")
    variant_d_log_file: Path = Path("/opt/calypso/logs/hydra_variant_d/bot.log")
    variant_d_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_d.json")
    variant_d_label: str = "D (DC Time Machine · multi-day · dry-run scaffold)"

    # ── Variant E — SPY Double Calendar (PRE-STAGED, not yet active) ──────────
    # Second multi-day net-DEBIT calendar strategy (sibling of D). Like D it is
    # intentionally NOT in _VARIANT_IDS (routers/variants.py) — the IC-shaped
    # /api/variants comparison would mis-render a debit calendar as a credit.
    # The /api/strategies group endpoints (calendar_multiday) serve E + D via
    # the shape-distinct DCDBReader instead. Paths mirror variant_d_* under
    # data/variant_e/. Its CALENDAR tables live in a SEPARATE file,
    # data/variant_e/dc_calendar.db (sibling of D's), surfaced via the calendar
    # group payload — NOT through the backtesting_db field (that's the shared
    # DataRecorder market-tick DB, kept for framework symmetry only).
    variant_e_state_file: Path = Path("/opt/calypso/data/variant_e/hydra_state.json")
    variant_e_metrics_file: Path = Path("/opt/calypso/data/variant_e/hydra_metrics.json")
    variant_e_backtesting_db: Path = Path("/opt/calypso/data/variant_e/backtesting.db")
    variant_e_log_file: Path = Path("/opt/calypso/logs/hydra_variant_e/bot.log")
    variant_e_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_e.json")
    variant_e_label: str = "E (SPY Double Calendar · multi-day · dry-run scaffold)"

    # ── Cumulative track-record BASELINE (rebase the cumulative to a date) ────
    # 2026-06-04: every cumulative figure (the single P&L card + the Comparison
    # lifetime totals + the running-cumulative curves) is summed from each
    # variant's `daily_summaries` table over ALL history. But that history is
    # mostly DRY-RUN simulation (A & B have NEVER traded live; C only since
    # 2026-06-04) and is contaminated by phantom/duplicate rows — and the
    # variants started on different dates, so their lifetime totals aren't
    # comparable. These fields rebase the cumulative SUMS + running curves to a
    # chosen start date (WHERE date >= baseline), zeroing prior history. Empty
    # string = full history (unchanged / backwards-compatible). NO DB mutation —
    # the History/calendar pages stay UNFILTERED so prior days remain visible.
    # `baseline_date` rebases the PRIMARY (C) card/WS; the per-variant fields
    # rebase each variant's Comparison curve + lifetime total independently.
    # Set via DASHBOARD_BASELINE_DATE / DASHBOARD_VARIANT_{A,B,C}_BASELINE_DATE
    # (ISO YYYY-MM-DD).
    baseline_date: str = ""
    variant_a_baseline_date: str = ""
    variant_b_baseline_date: str = ""
    variant_c_baseline_date: str = ""
    # D & E (calendars): the dashboard hides every calendar ENTERED before the
    # baseline (so a pre-fix calendar can't reappear by closing later). NO DB
    # mutation — raw rows are preserved for the D/E validation work; only the view
    # is culled. Override via DASHBOARD_VARIANT_{D,E}_BASELINE_DATE.
    #
    # D = 2026-07-11 (2026-07-10): D's earlier data is misleading on THREE counts.
    # (a) pre-cfc6027 (<= 07-02) rows are born-at--20% valuation artifacts that
    # instantly tripped the -20% stop; (b) days through 07-09 were priced with the
    # full-touch fill model (dry_run_fill_model unset => 1.0), booking the full
    # ~$170 round-trip spread as a loss on a ~6-hour hold (73% of the -$960); the
    # fill model was calibrated to 0.5 on 07-09. (c) 07-10 then got hit by a
    # crossed-quote spike (long call mid 15.0 < short call 24.0 => calendar value
    # -$2,550 => phantom -20% stop for -$797), fixed by the mark-sanity guard
    # (commit a1678ce). So 07-11 is D's first session with BOTH the calibrated fill
    # model AND the mark guard — the true clean start. See memory
    # d_fill_model_and_sameday_close.
    #
    # E = 2026-07-07 (first day on real-time SPY via the Network B feed). NOTE: E's
    # dry_run_fill_model is ALSO unset (full touch) — milder (tighter SPY spreads,
    # and E HOLDS multi-day so the spread amortizes instead of repeating daily) and
    # E has zero completed trades, but worth calibrating before grading its edge.
    variant_d_baseline_date: str = "2026-07-11"
    variant_e_baseline_date: str = "2026-07-07"

    # Agent intel directories
    agent_intel_dir: Path = Path("/opt/calypso/intel")

    # API security
    api_key: str = ""

    # Polling intervals (seconds)
    state_poll_interval: float = 1.0
    metrics_poll_interval: float = 10.0
    db_poll_interval: float = 30.0
    log_poll_interval: float = 2.0
    market_status_interval: float = 60.0

    # WebSocket
    ws_heartbeat_interval: float = 25.0

    # Server
    host: str = "127.0.0.1"
    port: int = 8001
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:8080",
        "http://35.231.243.156:8080",
    ]

    model_config = {"env_prefix": "DASHBOARD_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
