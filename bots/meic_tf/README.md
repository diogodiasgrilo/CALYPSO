# MEIC-TF (Trend Following Hybrid) Trading Bot

A modified MEIC bot that adds EMA-based trend direction detection to avoid losses on strong trend days.

## Strategy Overview

MEIC-TF combines Tammy Chambless's MEIC (Multiple Entry Iron Condors) with trend-following concepts from METF:

- **Before each entry**, check 20 EMA vs 40 EMA on SPX 1-minute bars
- **BULLISH** (20 > 40): Place PUT spread only (calls are risky in uptrend)
- **BEARISH** (20 < 40): Place CALL spread only (puts are risky in downtrend)
- **NEUTRAL** (within 0.1%): Place full iron condor (standard MEIC behavior)

### Why This Works

On February 4, 2026, pure MEIC had all 6 entries get their PUT side stopped because the market was in a sustained downtrend. MEIC-TF would have detected the bearish trend and only placed call spreads, avoiding ~$1,500 in losses.

### Entry Schedule (Same as MEIC)

| Entry | Time (ET) |
|-------|-----------|
| 1 | 10:00 AM |
| 2 | 10:30 AM |
| 3 | 11:00 AM |
| 4 | 11:30 AM |
| 5 | 12:00 PM |
| 6 | 12:30 PM |

### Trend-Based Entry Logic

| Trend Signal | What Gets Placed | Rationale |
|--------------|------------------|-----------|
| BULLISH | PUT spread only | Uptrend → calls risky, puts safe |
| BEARISH | CALL spread only | Downtrend → puts risky, calls safe |
| NEUTRAL | Full iron condor | Range-bound → both sides safe |

## Configuration

```json
{
    "trend_filter": {
        "enabled": true,
        "ema_short_period": 20,
        "ema_long_period": 40,
        "ema_neutral_threshold": 0.001,
        "recheck_each_entry": true,
        "chart_bars_count": 50,
        "chart_horizon_minutes": 1
    }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable/disable trend filtering |
| `ema_short_period` | `20` | Fast EMA period |
| `ema_long_period` | `40` | Slow EMA period |
| `ema_neutral_threshold` | `0.001` | 0.1% - threshold for neutral zone |
| `recheck_each_entry` | `true` | Re-check EMAs before each entry |
| `chart_bars_count` | `50` | Number of 1-min bars to fetch |
| `chart_horizon_minutes` | `1` | Bar interval (1 = 1 minute) |

## Usage

```bash
# Run in simulation mode (no real orders)
python -m bots.meic_tf.main --dry-run

# Run with live data (real orders)
python -m bots.meic_tf.main --live

# Show current status
python -m bots.meic_tf.main --status
```

## Deployment

1. Copy config template and edit:
```bash
cp bots/meic_tf/config/config.json.template bots/meic_tf/config/config.json
# Edit config.json with your settings
```

2. Install systemd service:
```bash
sudo cp bots/meic_tf/meic_tf.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meic_tf
sudo systemctl start meic_tf
```

3. Monitor:
```bash
sudo journalctl -u meic_tf -f
```

## Differences from Pure MEIC

| Aspect | Pure MEIC | MEIC-TF |
|--------|-----------|---------|
| Entry type | Always full IC | Trend-dependent |
| Premium per entry | Higher (both sides) | Lower on trend days |
| Trend day losses | Expected (stops on one side) | Avoided (safe side only) |
| Range day behavior | Normal | Identical to MEIC |
| Complexity | Simpler | Slightly more complex |

## Risk Considerations

1. **Lower premium on trend days**: One-sided = ~50% of full IC premium
2. **Trend reversal risk**: If trend flips after entry, the "safe" side becomes risky
3. **EMA lag**: EMAs are lagging indicators; sudden reversals may not be detected immediately

## State Files

MEIC-TF uses **separate state files** from MEIC to allow both bots to run simultaneously:

| File | Description |
|------|-------------|
| `data/meic_tf_state.json` | MEIC-TF daily state (entries, P&L, stops) |
| `data/position_registry.json` | Shared with all SPX bots for position isolation |

**Important**: The Position Registry is shared across all SPX bots (MEIC, MEIC-TF, Iron Fly) to prevent position conflicts when multiple bots trade the same underlying.

## Files

```
bots/meic_tf/
├── main.py                 # Entry point
├── strategy.py             # Trend-following strategy (extends MEIC)
├── meic_tf.service         # Systemd service file
├── config/
│   └── config.json.template
└── README.md               # This file
```

## Related Documentation

- [MEIC Strategy Specification](../../docs/MEIC_STRATEGY_SPECIFICATION.md)
- [MEIC Edge Cases](../../docs/MEIC_EDGE_CASES.md)
- [Technical Indicators](../../shared/technical_indicators.py)

## Version History

- **1.0.0** (2026-02-04): Initial implementation
  - EMA 20/40 trend detection
  - One-sided entries for trending markets
  - Full IC for neutral markets
