"""Bound the IADSS signal rate from its MECHANISM, since nobody publishes the rate.

Third-party descriptions of the components (CryptoG review, IADSS-Signal-Tracker README):
  Confluence Model  -> arrows on a Bollinger band BREAK followed by a RE-INTEGRATION
  Mean Reversion    -> dots at overbought / oversold extremes
  BUY/SELL Early Warning   -> "MR + Confluence aligned"
  BUY/SELL Sequence Complete -> "all conditions met" (adds the Trend flip)

So: count(re-integration) >= count(aligned) >= count(Sequence Complete).
Each extra condition can only REMOVE events, so the loose counts are UPPER BOUNDS.
This is a proxy for the published mechanism, not the real script.
"""
import json, sys, time
import pandas as pd, numpy as np, requests

TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META"]
UA = {"User-Agent": "Mozilla/5.0 (compatible; market-data-client)"}  # generic: no personal info


def daily(sym, rng="10y"):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    r = requests.get(u, params={"range": rng, "interval": "1d"}, headers=UA, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"close": q["close"], "high": q["high"], "low": q["low"]},
                      index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    return df.dropna()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def count(df, bb_n=20, bb_k=2.0, ob=70, os_=30):
    c = df["close"]
    ma = c.rolling(bb_n).mean()
    sd = c.rolling(bb_n).std(ddof=0)
    up, lo = ma + bb_k * sd, ma - bb_k * sd
    r = rsi(c)
    ema_f, ema_s = c.ewm(span=10).mean(), c.ewm(span=30).mean()
    trend_up = ema_f > ema_s

    out_hi, out_lo = c > up, c < lo
    rows = []
    # walk excursions: outside for >=1 bar, then a close back inside = re-integration event
    side = None; peak_rsi = None; start = None
    for i in range(bb_n, len(c)):
        t = c.index[i]
        if side is None:
            if out_lo.iloc[i]:  side, peak_rsi, start = "buy",  r.iloc[i], t
            elif out_hi.iloc[i]: side, peak_rsi, start = "sell", r.iloc[i], t
            continue
        if side == "buy":
            peak_rsi = min(peak_rsi, r.iloc[i])
            if not out_lo.iloc[i]:
                rows.append((t, "buy", peak_rsi <= os_, bool(trend_up.iloc[i]))); side = None
        else:
            peak_rsi = max(peak_rsi, r.iloc[i])
            if not out_hi.iloc[i]:
                rows.append((t, "sell", peak_rsi >= ob, bool(~trend_up.iloc[i]))); side = None
    return pd.DataFrame(rows, columns=["date", "side", "aligned", "trend_ok"])


def main() -> None:
    print(f"{'sym':5} {'yrs':>5} {'reintg/yr':>10} {'aligned/yr':>11} {'+trend/yr':>10}")
    print("-" * 46)
    tot = []
    for s in TICKERS:
        try:
            df = daily(s)
        except Exception as e:
            print(f"{s:5} FETCH FAIL {type(e).__name__}"); continue
        ev = count(df)
        yrs = (df.index[-1] - df.index[0]).days / 365.25
        a = ev["aligned"].sum(); b = (ev["aligned"] & ev["trend_ok"]).sum()
        print(f"{s:5} {yrs:5.1f} {len(ev)/yrs:10.1f} {a/yrs:11.1f} {b/yrs:10.1f}")
        tot.append((s, yrs, len(ev), a, b))
        time.sleep(0.4)

    if tot:
        Y = sum(t[1] for t in tot)
        print("-" * 46)
        print(f"{'MEAN':5} {Y/len(tot):5.1f} {sum(t[2] for t in tot)/Y:10.1f} "
              f"{sum(t[3] for t in tot)/Y:11.1f} {sum(t[4] for t in tot)/Y:10.1f}")
        print(f"\nper-ticker-year, {len(tot)} tickers, {Y:.0f} ticker-years of daily bars")


if __name__ == "__main__":
    main()
