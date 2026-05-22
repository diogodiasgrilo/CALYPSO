# HYDRA Scripts

Analysis, backtest, research and migration scripts for the HYDRA bot.
Run from the repository root. Most are one-off research artifacts —
the formal test suite is in `/tests/` (`pytest tests/`).

Backtest / analysis scripts read the local `data/backtesting.db`
(populated by HOMER) and need no broker connection.

## Backtesting (SQLite DB)

| Script | Purpose |
|--------|---------|
| `backtest_full_history.py` | Full-history HYDRA replay |
| `backtest_stop_buffers.py` | Put / call stop-buffer optimisation |
| `backtest_call_buffer_sweep.py` / `_detail.py` / `_deep.py` | Call-buffer sweep + drill-down |
| `backtest_mkt035_ref.py` / `mkt035_corrected_analysis.py` | MKT-035 down-day threshold |
| `backtest_mkt038.py` | FOMC T+1 call-only backtest |
| `backtest_downday035.py` / `downday_threshold_sweep.py` | Down-day conditional-entry sweeps |
| `backtest_base_downday.py` / `sweep_base_downday.py` | Base-entry down-day call-only EV |
| `backtest_fomc_all_days.py` | FOMC-day behaviour |
| `backtest_pot_vs_hydra.py` | Probability-of-touching vs production |
| `early_close_backtest.py` / `early_close_roc_backtest.py` | MKT-018 early-close ROC |
| `ema_trend_backtest.py` | EMA trend-signal backtest |
| `spread_width_*.py` | VIX-scaled spread-width experiments |
| `buffer_*.py` / `call_buffer_*.py` / `call_stop_buffer_analysis.py` | Stop-buffer studies |
| `mkt036_*.py` / `mkt037_dynamic_entry_backtest.py` | MKT-036 / MKT-037 experiments |

## Analysis & investigation

| Script | Purpose |
|--------|---------|
| `credit_breakeven_analysis.py` | Breakeven credit per VIX regime |
| `probability_of_touching.py` / `pot_strike_recommender.py` | POT strike-selection framework |
| `expected_value_strike.py` | EV by strike distance |
| `analyze_*.py` / `e6_*.py` / `e1_*.py` / `e23_doubling_risk.py` | Per-entry / E6 / E1 studies |
| `ask_spike_evidence.py` / `stop_trigger_investigation.py` | False-stop / ask-spike forensics |
| `apr15_*.py` / `apr17_post_stop_check.py` | Dated incident investigations |
| `compare_live_vs_backtest.py` | ThetaData-vs-live calibration |
| `audit_all_configs.py` + `config_audit_lib.py` | Config drift audit (VM vs template) |

## Migration (Saxo → IBKR rewrite)

| Script | Purpose |
|--------|---------|
| `p2_method_ranges.py` | Verify unreachable-method deletion set (AST) |
| `p2_delete_methods.py` | Delete named methods from `base_strategy.py` |
| `p4_collapse.py` | Collapse broker-branched methods to IBKR-only |
| `probe_ibkr_chain.py` / `probe_ibkr_trades.py` | Read-only IBKR paper-account probes |

## Maintenance

| Script | Purpose |
|--------|---------|
| `backfill_journal_credits.py` | Backfill missing credits in the trading journal |
| `fix_state_entry2.py` | One-off state-file repair |

## Running

```bash
# From repo root
python scripts/backtest_stop_buffers.py

# On the VM
gcloud compute ssh calypso-bot --zone=us-east1-b \
  --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/<name>.py'"
```

## Formal test suite

```bash
pytest tests/            # all tests
pytest tests/ -q         # quiet
```

**Last Updated:** 2026-05-22
