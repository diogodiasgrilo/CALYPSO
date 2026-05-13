# IB SPX 0DTE — workflow, pricing, and edge cases

**Scope.** Everything an automated SPX-only iron-condor bot needs to know to migrate from Saxo to Interactive Brokers. Account profile: REG-T retail, EUR base, ~$50K equity, 10-contract ICs of 25–75 pt width, 0DTE, entries 09:45–14:00 ET, TP at 80% credit, credit-based stops.

**Research date.** May 2026. Sources cited inline; documentation dates noted where the source page surfaces them.

---

## 1. Symbol, secType, exchange — the SPX/SPXW/SPY trap

The single most important thing to get right before any other engineering: **SPX and SPXW are different option series listed under the same underlying.** They are distinguished in the IB API by `tradingClass`, not by `symbol`.

| Series | What it is | Settlement | Expiry days | 0DTE? | IB `symbol` | IB `secType` | IB `exchange` | IB `tradingClass` | Multiplier |
|---|---|---|---|---|---|---|---|---|---|
| SPX (legacy) | Monthly AM-settled, European cash | AM, "SET" opening print of components on expiry Friday | 3rd Friday of each month | No | `SPX` | `OPT` | `CBOE` | `SPX` | 100 |
| SPXW | Weekly + daily PM-settled, European cash | PM, closing index value on expiry day | Mon–Fri (daily since May 2022) | **Yes** | `SPX` | `OPT` | `CBOE` | `SPXW` | 100 |
| SPY | ETF options, American, physically settled in SPY shares | EOD, physical delivery of 100 SPY/contract | Mon/Wed/Fri (and others) | Yes | `SPY` | `OPT` | `SMART` | `SPY` | 100 |
| SPX (the index itself) | Cash index used as ticker for quoting and chain look-up | n/a | n/a | n/a | `SPX` | `IND` | `CBOE` | n/a | n/a |

**Confirmed by IBKR + ib_insync option_chain notebook + marketdata.app SPX-vs-SPXW education page:** the canonical contract for a 0DTE call is

```python
from ib_async import Option

call = Option(
    symbol='SPX',                       # NOT 'SPXW' — symbol stays 'SPX'
    lastTradeDateOrContractMonth='20260513',
    strike=5500,
    right='C',                          # 'C' for call, 'P' for put
    exchange='CBOE',                    # NOT 'SMART' — SPX index options are CBOE-only
    currency='USD',
    tradingClass='SPXW',                # this is the field that selects PM-settled weekly/daily
    multiplier='100',
)
ib.qualifyContracts(call)
```

**Variant seen in the wild** (robotwealth + the aicheung/0dte-trader repo): some examples set `symbol='SPXW'` with `exchange='SMART'`. **That is not the canonical form**, and it appears to work only because IB's resolver is forgiving. Always set `symbol='SPX'`, `exchange='CBOE'`, `tradingClass='SPXW'` and call `ib.qualifyContracts()` so IB resolves a fully populated `conId`. Trade against the qualified contract, not the partially-specified one. Sources: ib_insync option_chain notebook (an actual qualified Option round-trip shows `tradingClass='SPX'` for monthly with `localSymbol='SPX   200117P03205000'`); marketdata.app "SPX vs SPXW Options — What's The Difference"; IBKR "Defining Contracts in the TWS API".

**One more nuance.** SPY is a *different instrument and a different tax bucket*. American-style + physical settlement means assignment risk all the way to 4:00 PM ET. Section 1256 60/40 does **not** apply to SPY. Do not allow accidental SPY routing — guard the bot with an assertion on `contract.symbol == 'SPX' and contract.tradingClass == 'SPXW' and contract.secType == 'OPT'`.

### Looking up the chain

The chain is queried against the underlying index, not the option:

```python
from ib_async import Index
spx = Index('SPX', 'CBOE', 'USD')
ib.qualifyContracts(spx)
params = ib.reqSecDefOptParams(spx.symbol, '', spx.secType, spx.conId)
# Returns OptionChain rows; for SPXW pick the row where tradingClass == 'SPXW'
```

This is the pattern from the ib_insync `option_chain.ipynb` notebook — `reqSecDefOptParams` returns separate rows for `SPX` (monthly) and `SPXW` (daily/weekly) trading classes on `CBOE`. Pick the SPXW row, filter `expirations` for today's date in `YYYYMMDD` form, and filter `strikes` for the relevant range. This is also where you discover that on weekly expiries (typically Fridays) **both SPX and SPXW chains exist for the same date** — pick the SPXW one because that's the PM-settled / daily-style series your strategy targets.

---

## 2. Settlement and assignment — why SPX is operationally simpler than SPY

European-style + cash settlement is the single biggest operational win of trading SPX vs SPY:

- **No early-exercise risk.** SPXW options can be exercised only at expiration ([Cboe SPX Weeklys Options page](https://www.cboe.com/tradable_products/sp_500/spx_weekly_options/specifications/)). The bot never has to monitor assignment overnight, never has to manage early-exercise of deep-ITM shorts.
- **Cash settlement = no share delivery.** When an SPXW option expires ITM, IB credits/debits cash. There is no overnight share inventory to dispose of, no PDT-relevant equity round-trip created by exercise.
- **Settlement value source.** SPXW uses the **closing print of the SPX index on expiration day** (Cboe spec; corroborated by marketdata.app). SPX monthly uses the special "SET" opening print computed from primary-market opens of each component the morning of expiration day — a different and famously gappy number, irrelevant to our SPXW-only bot.

**Trading-cessation time on expiry day.** Cboe's SPXW spec page says:
- Regular trading: 9:30 AM – 4:15 PM ET on non-expiry days.
- **On the expiration day, SPXW trading ceases at 4:00 PM ET** ([Cboe SPXW Specs](https://www.cboe.com/tradable_products/sp_500/spx_weekly_options/specifications/)).
- 1:00 PM ET cutoff on half-day holidays (e.g., the day after Thanksgiving, Christmas Eve).

The "4:15 PM ET" you may see in older docs is the *non-expiry-day* close; on 0DTE day, **trading stops at 4:00 PM ET**. After that, settlement is computed from the 4:00 PM SPX print, and cash settles T+1 (Cboe; the [optionalpha 0DTE expiration page](https://optionalpha.com/learn/0dte-expiration) confirms cash-settled at end of day). **Implication for the bot:** all close-out, stop, and TP logic must be wound down by ~3:55 PM ET, with the absolute hard kill at 3:59 PM ET.

**IB curb session note.** IB allows some trading in the curb session until 5:00 PM ET on non-expiry products — *but for SPXW on 0DTE day, the option ceases to exist at 4:00 PM ET*. There is no curb session for the expiring SPXW contract. (Source: SPX Option Trader site; corroborates the Cboe spec.)

---

## 3. SPX index real-time data — the subscription trap

You need to know the SPX index level in real time to (a) pick strikes at entry, (b) compute distance-to-strike for credit-based stops, and (c) sanity-check the bot's mid-quote against where the underlying actually is. **This is not included in IB's default free data.**

| Subscription | What it gives | Non-pro retail cost (May 2026) | Waiver | Required for |
|---|---|---|---|---|
| US Securities Snapshot and Futures Value Bundle | NBBO US equity quotes, CBOE Market Data Express Indices (this is where SPX index level lives), CME/CBOT/NYMEX futures top-of-book, Dow Jones non-pro | **$10/month** | Waived at $30+/month in IBKR commissions | SPX index level in real time (prerequisite for OPRA) |
| OPRA Top-of-Book | Real-time consolidated US options quotes (NBBO) including all SPXW strikes | **$1.50/month** | Waived at $20+/month commissions | Real-time SPXW quotes |
| Cboe One (free) | Non-consolidated equity quotes — DOES NOT include SPX cash index | $0 | n/a | Limited utility for index traders |
| CFE (Cboe Futures Exchange) | VIX futures, VIX index real-time | extra (separate) | n/a | Only if bot uses VIX as a filter |

Sources: [IBKR Market Data Pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php); [supa.is "Which IBKR Market Data Subscription Do You Actually Need? (2026 Guide)"](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026); [IBKR OPRA glossary](https://www.interactivebrokers.com/campus/glossary-terms/options-price-reporting-authority-opra/).

**Without these subscriptions:** quotes are 15-minute delayed (useless at 0DTE timescales — a 15-minute lag at entry will misprice a 25-pt iron condor by multiple dollars of credit).

**Practical recommendation.** For a 10-contract IC bot trading even a couple of times per week, commissions will easily exceed the $30/month threshold, so both subscriptions are usually free in practice. Budget $11.50/month worst case (10 cents per side per contract × 4 legs × 10 contracts × 1 trade/day ≈ $80/month commissions, comfortably above both waivers). Verify in production by checking `Account Window → Market Data Subscriptions → Auto-Waivers Active`.

**Professional-account trap.** If IB classifies the account as "Professional" (e.g., LLC entities, certain disclosed activity, anyone receiving the data inside a workplace), data costs jump roughly 10× and waivers may not apply (supa.is). Keep the account classification at **Non-Professional** during onboarding; this is asked explicitly on the IB application.

---

## 4. OPRA options data — what you actually get

What basic OPRA includes:
- Top-of-book NBBO (best bid, best ask, sizes) for every listed US option, refreshed in real time.
- Last-trade tick.
- Underlying-implied greeks (delta, gamma, theta, vega, IV) computed **server-side by IB** and pushed via `tickOptionComputation` ticks. This means the bot does **not** need its own Black-Scholes; just subscribe to `genericTickList='100,101,104,105,106,107,165,221,225,233,236,258'` or use `reqMktData` defaults and read `modelGreeks` / `bidGreeks` / `askGreeks` from the resulting `Ticker`.
- Full options chain market depth is **not** included in basic OPRA. For 0DTE iron-condor work that is fine — top-of-book is enough.

Source: IBKR OPRA glossary; ib_insync `Ticker` model docs.

**Greeks accuracy on 0DTE.** Greeks are computed from IV which is back-solved from the current option price + current SPX level + time to expiry. On 0DTE, theta and gamma are extreme; IB's pricing engine handles this fine but **delta of the wing shorts decays rapidly through the day** — do not cache greeks; re-pull them every time the bot evaluates a stop-out condition.

---

## 5. Order routing for SPX options

**SPX options trade only on Cboe** ([Cboe SPX page](https://www.cboe.com/tradable-products/sp-500/spx-options/)). There is no "SMART route" for SPX index options because there is only one venue. Always set `exchange='CBOE'` on the option contract; `exchange='SMART'` is forwarded to CBOE in practice but produces noisier diagnostics and is wrong on principle.

**Combo orders for iron condors.** Cboe operates a **Complex Order Book (COB)** that natively accepts multi-leg orders ([Cboe US Options Complex Orders](https://www.cboe.com/us/options/trading/complex_orders/); [Cboe Titanium U.S. Options Complex Book Process v1.2.69, Jan 13 2026](https://cdn.cboe.com/resources/membership/US-Options-Complex-Book-Process.pdf)). 4-leg iron condors fit; the COB supports up to 16-leg complex orders. IBKR's TWS API submits these as `BAG` securities — `Contract(secType='BAG', symbol='SPX', exchange='CBOE', currency='USD', comboLegs=[...])` with one `ComboLeg` per option leg (action `BUY`/`SELL`, ratio 1, `conId`, `exchange='CBOE'`).

**Net-credit tick size.** SPX/SPXW complex-order net prices must be in **$0.05 increments** ([Cboe Titanium spec, Jan 2026](https://cdn.cboe.com/resources/membership/US-Options-Complex-Book-Process.pdf)). Boxes/rolls can use $0.01, but a standard iron condor net credit must round to nickels. This is a real source of "limit not executable" rejections — the bot must `round(credit / 0.05) * 0.05` before sending.

**Spread fill quality vs leg-by-leg.** IBKR routes combo orders directly into the COB instead of legging them out, which is the right behaviour. Typical fill is at the **NBBO mid of the combo, not the mid of each leg** — empirically the bot will pay around $0.05–$0.10 of edge per contract vs the theoretical mid. The aicheung/0dte-trader repo's approach of submitting separate limit orders per leg ("Sell call at bid… then sell put at bid") is **wrong for our use case** — it exposes the strategy to a partial-fill state where one short leg is open without its long wing, blowing up the margin calculation and exposure profile. Stick with `BAG` orders.

**"All-or-None" trap.** AON routing on a 4-leg combo on illiquid wing strikes can starve the order out completely. Use Limit orders, not AON, and let the COB price-improve into mid over a couple of seconds. If unfilled within ~5–10 seconds at the chosen credit, cancel and re-price one nickel worse.

---

## 6. Pacing limits

| Limit | Threshold | Source |
|---|---|---|
| API general message rate | ~50 messages/second per client connection, sustained ~6/sec advisory | TWS API limits page (legacy) |
| Concurrent streaming market-data lines | **100** default per account | [IBKR Market Data Display + IB streaming docs](https://interactivebrokers.github.io/tws-api/market_data.html) |
| Extend market-data lines | Quote Booster packs: **$30/pack, +100 Level-I lines each, max 10 packs** | IBKR Market Data page |
| Concurrent open historical requests | **50** | [TWS API Historical Data Limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html) |
| Historical requests per 10 minutes | **60** | same |
| Same contract+exchange+tickType rate | **6 within 2 seconds** = pacing violation | same |
| Identical historical request cooldown | **15 seconds** | same |
| BID_ASK historical | Counts **double** against the per-10-min budget | same |

**The 100-lines limit is the dangerous one for SPX option-chain work.** A 0DTE SPXW chain at any given moment has ~400–600 active strikes (1-point strikes near the money, 5-point further out). You cannot subscribe to the whole chain.

**Strategy for the bot:**
1. At entry, call `reqSecDefOptParams` once to get the strike list (no market-data lines used).
2. Narrow to a window of strikes around the desired delta (e.g., 30 strikes within ±200 points of spot).
3. `reqMktData(contract, snapshot=True)` for each of those 30 strikes — snapshot requests release the line immediately after the snapshot fills.
4. Pick the four legs for the IC, qualify them, submit the BAG.
5. After fill, subscribe streaming (`reqMktData(contract, snapshot=False)`) only to the **four open contracts** + SPX index — 5 lines total — for the stop/TP monitor loop.

Snapshot-mode requests do not count against the 100-line streaming budget (the line is released after one round-trip). Streaming subs do. This is the pattern that lets you query a wide chain without buying booster packs.

---

## 7. Bid-ask spreads on 0DTE SPX

Empirical pattern from Cboe market structure data + practitioner reports ([Cboe Henry Schwartz 0DTE iron-condor analysis](https://www.cboe.com/insights/posts/henry-schwartzs-zero-day-spx-iron-condor-strategy-a-deep-dive/); SPX Option Trader site):

| Time of day | ATM strike spread | 30-delta wing spread | Far OTM wing spread |
|---|---|---|---|
| 09:30 – 09:45 ET (open) | $0.20–$0.50 | $0.30–$0.80 | $0.50–$1.50 |
| 09:45 – 14:00 ET (steady state) | $0.05–$0.10 | $0.10–$0.20 | $0.20–$0.50 |
| 14:00 – 15:30 ET (drift to close) | $0.05–$0.10 | $0.15–$0.40 | $0.30–$0.80 |
| 15:30 – 16:00 ET (gamma hour) | $0.10–$0.40 | $0.30–$1.00 | $0.50–$2.00+ |

**Saxo vs IB quote comparison.** IB sources the consolidated OPRA NBBO; Saxo's options quotes are typically wider than the NBBO because Saxo internalises or top-slices. Expect IB fills **inside Saxo's quotes** on the same contract by approximately the difference between consolidated-NBBO mid and Saxo's wider mid — often $0.05–$0.20 of net credit per contract for an iron condor. This is roughly a 5–15% improvement in collected credit relative to Saxo at the margin. **Do not back-fit the bot's TP threshold to Saxo's quote distribution after the migration; re-tune on the IB quote distribution.**

---

## 8. Margin for short option spreads on REG-T

**Defined-risk short spread formula at IBKR (REG-T):**

> Margin = (spread_width × multiplier × contracts) − net_credit_received

This is the standard CBOE strategy-based margin formula for short verticals; IBKR applies it to SPX/SPXW spreads with their European cash-settlement treatment. ([IBKR Options Margin Requirements](https://www.interactivebrokers.com/en/trading/margin-options.php); [What Is the Margin on an Iron Condor Option Strategy? KB-600](https://www.ibkrguides.com/kb/article-600.htm) — redirects to FAQ content/1163244548.)

**Worked example — your case.** 10 SPX 25-pt short iron condor at 2.00 net credit:

```
Each side (call vertical + put vertical) treated as a separate short spread.
For ONE side (the more expensive of the two — IBKR margins on the larger side):
  spread_width × $100 × contracts        = 25 × 100 × 10 = $25,000
  − net_credit_received_that_side        ≈ − ($1.00 × 100 × 10) = −$1,000
  = $24,000 maintenance margin per side

Both sides cannot lose at once on an iron condor, so the IC margin requirement is
the LARGER of the two short-vertical requirements — typically $24,000–$25,000
for a 25-pt 10-lot at $1.00–$2.00 credit per side.
```

**So a 10-lot 25-pt IC at REG-T blocks ~$24,000–$25,000.** That matches your "$7,500–$30,000 margin per IC" range and your $50K account easily supports 1–2 concurrent ICs of 25-pt width. A 75-pt 10-lot is ~$72,000–$75,000 — exceeds your equity, so 75-pt width is REG-T-impossible at 10 contracts on a $50K account; you would need to drop to 5–6 contracts or upsize the account.

**"Universal spread" house add-on.** Per CBOE Rule 10.3(a)(5) (cited in IBKR docs as "Rule 12.3(a)(5)" on older pages — same concept), IB may charge **102% of the net maximum market loss** as the house requirement if it exceeds the statutory minimum ([IBKR Options Margin Requirements](https://www.interactivebrokers.com/en/trading/margin-options.php)). For SPX defined-risk spreads this almost always equals the statutory `spread_width × 100 × contracts − net_credit` figure, so the practical impact is small but not zero.

**Initial vs maintenance.** REG-T initial = maintenance for short spreads (both = max-loss-minus-credit). There is no "Reg-T initial 50%" applied because you are not buying stock — short spreads' max loss is defined and IB blocks the full cash equivalent at order placement.

**Credit application.** From the search results: "the credit received upon opening a trade is not applied to the margin requirements" — interpretation: IBKR's *display* of the margin requirement shows the spread-width number gross, but the *cash credit lands in the account* on fill, increasing `AvailableFunds` by the credit amount. Net effect after fill is identical to "margin = spread_width × 100 − credit". Just don't rely on pre-trade margin estimates that look unaffordable — IB's pre-trade check is on gross max-loss; once filled, the credit offsets.

**Portfolio Margin (PM) for SPX.** PM minimum equity at IBKR is **$100,000** (not $110,000 — that figure is FINRA's $25K + $75K cushion convention; IB's stated minimum is $100K). Source: [IBKR Portfolio Margin Account glossary](https://www.interactivebrokers.com/campus/glossary-terms/portfolio-margin-account/). PM uses TIMS risk-arrays and **typically reduces SPX IC margin by 50–70%** vs REG-T because IC tail risk shows up on the TIMS shock grid as bounded. You don't qualify today at $50K equity; flag PM as a future upgrade trigger ("at $100K, switch to PM, IC margin drops, position size or width can grow").

**Day-trading buying power.** SPX index options are **not** subject to the FINRA Pattern Day Trader rule when traded as cash-settled European-style index options — but IBKR's account-level PDT flag is **on the equity account, not the option series**, and PDT *can* be triggered if you do 4+ day trades across SPY, equities, and equity options in 5 days. Pure SPX-only activity does **not** trigger PDT (search result confirms: "PDT does not apply to SPX cash-settled index options" — but IBKR's own glossary notes the equity-account-level flag). With this bot trading only SPX iron condors, PDT is functionally a non-issue. Confirm the bot never accidentally legs into SPY.

---

## 9. 0DTE-specific known issues

### 9.1 Stop-loss orders on options are unreliable

This is the strongest piece of advice that comes up across every 0DTE forum and IB documentation page: **do not use broker-side STOP or STOP_LMT orders to trigger exits on illiquid 0DTE option legs.**

Reasons:
1. Stop triggers fire on the **last-trade print**, not on mid. A single wing-strike print at an off-market level (a "stub quote" market-maker print) can trigger the stop and convert to a market order that fills $0.50–$2.00 worse than mid.
2. Stop-limit can skip entirely on a gap — your bot's intended stop level passes by while no fill happens, and the position is now uncovered.
3. The 4-leg combo has no native "stop on net credit" order type at IB. Stops only exist on single contracts.

**Source confirms our pattern is correct.** Theta Profits, SPX Option Trader, and Elite Trader threads converge on: **monitor net credit in code, submit a closing combo limit order at the bid-side mid when stop condition trips.** This is what the existing Saxo bot does and it's the right pattern at IB too. Do not migrate to broker-side stops.

### 9.2 Combo order rejections near expiration

There is no documented hard cutoff time on IB's side before 4:00 PM ET, but in practice:
- Between 3:55 PM and 4:00 PM ET, the COB price-improvement logic shortens, and wing-strike liquidity evaporates. Combos at the original credit target reject as "no liquidity at limit."
- The fix is to **price the closing combo aggressively** in the final 5 minutes — go straight to the bid of the combo (i.e., pay through) rather than fishing for mid. Edge given up here is small in $ terms because the IC is either near-worthless (winning) or near-max-loss (losing).
- Hard cutoff in the bot's logic: stop *opening* new ICs by 14:00 ET (your existing rule), and force-close any remaining position at limit-equals-current-bid by **3:55 PM ET** to leave 5 minutes of liquidity headroom.

### 9.3 All-or-None routing

Combo orders default to AON at IBKR (the BAG ships atomically — you can't get 7 out of 10 contracts filled and 3 open, the whole order fills or doesn't). This is desired behaviour. Don't try to disable it. Just accept that the order may not fill and re-quote.

### 9.4 No expired-contract data

Per the Robot Wealth IBKR 0DTE walk-through: TWS does **not** return data for expired SPXW contracts. The bot must record every fill, credit, leg P&L during the live session — there is no retroactive query. Log to your own DB on every order event.

### 9.5 SPXW vs SPX on a Friday

On the third Friday of each month, **both** SPX (AM-settled, expires that day at open) and SPXW (PM-settled, expires that day at close) chains exist. Your bot must filter on `tradingClass='SPXW'`. Trading the SPX (AM-settled) chain on its expiry Friday is operationally broken because the contract has already settled before the bot's 09:45 ET entry window opens.

---

## 10. EUR base currency, USD options

IB Universal Account holds balances in each currency separately. The relationship to options trading:

- **Cash balance per currency.** Your EUR deposits sit in EUR; USD options trades create USD positions. The account does not auto-convert at fill.
- **Margin in the option's currency.** SPX option margin is computed and held in USD against your account. If you don't have USD cash, IB **lends you USD against your EUR collateral** at the USD margin rate (approx 4–5% APR as of May 2026 per IBKR Margin Rates page) until you settle the FX.
- **Credit received in USD.** Selling the iron condor credits USD into your USD cash balance. If you previously had a USD margin loan, the credit reduces it.
- **FX settlement is manual.** Use `FXCONV` (`IDEALPRO` venue) to convert at end of day. No auto-sweep.

**Which account field to read in code.** All values displayed in USD or in `BASE` (= EUR for you):

| Field | What it means | When to read |
|---|---|---|
| `AvailableFunds` | Funds available for new positions (post-initial-margin) | Pre-trade — does this IC fit? |
| `BuyingPower` | `AvailableFunds × 4` in margin accounts (Reg-T multiplier for marginable equities; **not relevant for option spreads**) | Mostly ignore for this bot |
| `ExcessLiquidity` | Cushion above maintenance margin | Live monitoring — if this drops near zero, liquidate |
| `NetLiquidation` | Total account value in base currency | Daily P&L tracking |
| `MaintMarginReq` | Current maintenance margin held | Sanity check during open positions |

Reference: [IBKR Buying Power glossary](https://www.interactivebrokers.com/campus/glossary-terms/buying-power/); [IBKR Available for Trading Values docs](https://www.ibkrguides.com/traderworkstation/available-for-trading.htm).

**Practical bot logic.** Pre-trade, read `AvailableFunds` in USD (request `accountSummary` with tag `AvailableFunds-S` and currency `USD`). Reject the IC if `AvailableFunds < margin_estimate × 1.2` (20% buffer). Post-fill, monitor `ExcessLiquidity` continuously; if it drops below 10% of NetLiq, force-close all positions.

**Currency exposure from the credit.** Net credit lands in USD. Over a year of 0DTE ICs, that's potentially $20K–$40K of accumulated USD cash drifting from your EUR base. Two strategies:
1. Sweep daily — convert USD → EUR via FXCONV at EOD. Removes EUR/USD risk but adds 0.2 bp × volume in FX commission and you eat spread.
2. Hold USD — keeps the cash ready for the next day's margin without conversion, but you carry EUR/USD risk. Over 1-day horizons EUR/USD moves ~0.3% σ, so on a $30K USD balance that's ~$90 daily σ — usually noise vs the trading P&L.

Recommend option 2 (hold USD) during active strategy operation, sweep monthly to keep the base-currency view clean.

---

## 11. Tax & reporting

**US-tax retail traders:** SPX options are **Section 1256 contracts** → 60% long-term / 40% short-term capital gain treatment regardless of holding period. Reported on 1099-B Box 11 (aggregate gain/loss), then Form 6781 → Schedule D. IBKR provides a "Gain/Loss Worksheet for 1256 Contracts" for the US 1099-B path ([IBKR Year-End Tax Forms](https://www.interactivebrokers.com/en/support/tax-us-forms.php)).

**EU-resident with IB Ireland / IB UK / IB LLC:** Section 1256 applies to US-tax residents only. As an EU resident your local tax authority treats SPX option gains under domestic capital-gains rules — for Portugal/most of Europe, that means short-term capital gains (typically marginal income rates or a flat 28% in Portugal). **Flag for an accountant**; IB's 1099-B will not be the operative document for your filing — your IBKR **Activity Statement / Annual Report** (downloadable per-year) is.

---

## 12. Order rejections we'll see and what to do

| Rejection code/text | Cause | Bot response |
|---|---|---|
| `103 — Duplicate order ID` | Reused `orderId` after disconnect | Always `ib.client.getReqId()` for fresh IDs |
| `201 — Order rejected: insufficient margin` | Pre-trade margin check failed — IC max-loss exceeds AvailableFunds | Drop contract count, retry; or skip the trade |
| `202 — Order canceled` | Including the COB pulling unfilled marketable orders | Re-price one nickel worse, resubmit, max 3 attempts |
| `321 — Error validating request` | Most often a malformed BAG: `conId` not qualified, or leg `exchange` ≠ 'CBOE' | Always `qualifyContracts()` each leg before building the BAG |
| `434 — Order size cannot be zero` | Combo with ratio = 0 on a leg | Validate each ComboLeg `ratio >= 1` |
| `10268 — Outside RTH` | Won't apply to SPXW (no extended hours for SPXW options anyway) | Should not see this on SPX; if you do, route is wrong |
| `Order would exceed maximum allowed position` | Naked short-option position limit at IB. ICs are always covered (long wing) so this should not fire — if it does, the bag was decomposed by the router | Investigate; this is a sign the BAG was sent as separate legs |
| `Stop price too close to current price` | If you ever send broker stops (you shouldn't) | Don't use broker stops; monitor in code |

---

## 13. Migration-readiness checklist

Before the bot's first live SPX IC at IB:

1. **Account classification = Non-Professional** confirmed at onboarding.
2. **Subscriptions active:** US Securities Snapshot + Futures Value Bundle ($10) and OPRA Top-of-Book ($1.50). Verify the auto-waiver triggers after first month if commissions exceed thresholds.
3. **Contract qualification round-trip** — write a smoke test that constructs `Option('SPX', '<today>', 5500, 'C', 'CBOE', tradingClass='SPXW', multiplier='100', currency='USD')`, calls `qualifyContracts`, and asserts `contract.conId > 0` and `contract.localSymbol` starts with `SPXW`.
4. **BAG construction smoke test** — submit a paper-trade 1-lot 5-pt IC, confirm fill at net credit on the COB, confirm both verticals appear in `portfolio()`.
5. **Net-credit tick rounding** — every limit price rounded to $0.05 before send.
6. **Hard cutoff at 3:55 PM ET** in the close-all loop.
7. **Snapshot vs streaming line discipline** — never streaming-subscribe more than 5 tickers (4 legs + SPX) during a live IC.
8. **`AvailableFunds-S` (USD)** read pre-trade; reject if margin > 80% of available.
9. **`ExcessLiquidity` monitor** with auto-liquidate at <10% of NetLiq.
10. **Logging** every order, fill, leg quote at sub-second granularity (TWS does not retain expired contract data — the bot is the system of record).

---

## Sources

- [Cboe SPX Weekly Options Specifications](https://www.cboe.com/tradable_products/sp_500/spx_weekly_options/specifications/)
- [Cboe S&P 500 Index Options Specifications](https://www.cboe.com/tradable_products/sp_500/spx_options/specifications/)
- [Cboe US Options Complex Orders](https://www.cboe.com/us/options/trading/complex_orders/)
- [Cboe Titanium U.S. Options Complex Book Process v1.2.69, Jan 13 2026](https://cdn.cboe.com/resources/membership/US-Options-Complex-Book-Process.pdf)
- [Cboe — Henry Schwartz Zero-Day SPX Iron Condor Strategy](https://www.cboe.com/insights/posts/henry-schwartzs-zero-day-spx-iron-condor-strategy-a-deep-dive/)
- [Cboe 0DTE Trading Resources](https://www.cboe.com/tradable-products/0dte/)
- [IBKR Trading 0DTE Options with the IBKR Native API](https://www.interactivebrokers.com/campus/ibkr-quant-news/trading-0dte-options-with-the-ibkr-native-api/)
- [IBKR 0DTE Options Glossary](https://www.interactivebrokers.com/campus/glossary-terms/0dte-options/)
- [IBKR Defining Contracts in the TWS API](https://www.interactivebrokers.com/campus/trading-lessons/defining-contracts-in-the-tws-api/)
- [IBKR Market Data Pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)
- [IBKR Cboe SPX page](https://www.interactivebrokers.com/en/trading/cboe.php)
- [IBKR Options Margin Requirements](https://www.interactivebrokers.com/en/trading/margin-options.php)
- [IBKR Portfolio Margin Account Glossary](https://www.interactivebrokers.com/campus/glossary-terms/portfolio-margin-account/)
- [IBKR What Is the Margin on an Iron Condor Option Strategy? (KB-600)](https://www.ibkrguides.com/kb/article-600.htm)
- [IBKR Buying Power Glossary](https://www.interactivebrokers.com/campus/glossary-terms/buying-power/)
- [IBKR Available for Trading Values Documentation](https://www.ibkrguides.com/traderworkstation/available-for-trading.htm)
- [IBKR OPRA Glossary](https://www.interactivebrokers.com/campus/glossary-terms/options-price-reporting-authority-opra/)
- [IBKR Year-End Tax Forms](https://www.interactivebrokers.com/en/support/tax-us-forms.php)
- [IBKR Subscription Considerations for U.S. Market Data](https://www.ibkrguides.com/kb/en-us/subscription-consideration-us-market-data.htm)
- [TWS API v9.72+ Streaming Market Data](https://interactivebrokers.github.io/tws-api/market_data.html)
- [TWS API v9.72+ Historical Data Limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html)
- [ib_async on GitHub (replaces ib_insync)](https://github.com/ib-api-reloaded/ib_async)
- [ib_insync option_chain.ipynb](https://github.com/erdewit/ib_insync/blob/master/notebooks/option_chain.ipynb)
- [Robot Wealth — Trading 0DTE Options with the IBKR Native API](https://robotwealth.com/trading-0dte-options-with-the-ibkr-native-api/)
- [aicheung/0dte-trader on GitHub](https://github.com/aicheung/0dte-trader)
- [marketdata.app — SPX vs SPXW Options — What's The Difference?](https://www.marketdata.app/education/options/spx-vs-spxw-options/)
- [flyonthewall.ai — SPX Options Expiration Guide](https://flyonthewall.ai/spx-options-expiration/)
- [optionalpha.com — What Time Do 0DTE Options Expire?](https://optionalpha.com/learn/0dte-expiration)
- [Theta Profits — Stop-Loss on Credit Spreads in 0DTE](https://www.thetaprofits.com/stop-loss-on-credit-spreads-in-0dte-options-trading/)
- [SPX Option Trader — 0DTE Trading Guidelines](https://www.spxoptiontrader.com/spx-weekly-options-trading/)
- [supa.is — Interactive Brokers Market Data Subscription 2026 Guide](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026)
- [Elite Trader — When does a 0 DTE SPX stop trading?](https://www.elitetrader.com/et/threads/when-does-a-0-dte-spx-stop-trading.369180/)
- [Day Trading Toolkit — Section 1256 60/40 Rule](https://daytradingtoolkit.com/day-trading-basics/section-1256-contracts-tax-advantage/)
