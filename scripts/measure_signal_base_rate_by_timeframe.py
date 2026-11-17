"""Same mechanism, swept across TIMEFRAMES. IADSS runs 'all timeframes (1 second .. 1 week)',
so the signal rate is a CONFIGURATION CHOICE, not a property of the product."""
import sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import pandas as pd, numpy as np, requests
from measure_signal_base_rate import rsi, count, UA

PLAN = [("1wk", "10y"), ("1d", "10y"), ("1h", "2y"), ("30m", "60d"), ("15m", "60d"), ("5m", "60d")]

def bars(sym, interval, rng):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": rng, "interval": interval, "includePrePost": "false"},
                     headers=UA, timeout=25)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    return pd.DataFrame({"close": q["close"], "high": q["high"], "low": q["low"]},
                        index=pd.to_datetime(res["timestamp"], unit="s", utc=True)).dropna()

for sym in ("SPY", "NVDA"):
    print(f"\n=== {sym} ===")
    print(f"{'tf':>5} {'bars':>7} {'span_d':>7} {'reintg/yr':>10} {'aligned/yr':>11}")
    for interval, rng in PLAN:
        try:
            df = bars(sym, interval, rng)
        except Exception as e:
            print(f"{interval:>5}  FAIL {type(e).__name__}"); continue
        if len(df) < 60:
            print(f"{interval:>5} {len(df):7} too few bars"); continue
        ev = count(df)
        yrs = (df.index[-1] - df.index[0]).days / 365.25
        if yrs <= 0: continue
        print(f"{interval:>5} {len(df):7} {yrs*365.25:7.0f} {len(ev)/yrs:10.0f} {ev['aligned'].sum()/yrs:11.0f}")
        time.sleep(0.5)
