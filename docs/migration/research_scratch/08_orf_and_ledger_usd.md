# Research 08 — 2026 CBOE ORF on SPX + IBKR `$LEDGER:USD` live USD-tradable amount

**Research date:** 2026-05-13
**Scope:** Two technical details for the Saxo → IBKR migration plan.

---

## Question 5 — 2026 CBOE Options Regulatory Fee on SPX

### Headline number (the one to wire into the cost model)

**SPX ORF = $0.0023 per contract, charged on the SELL side only, effective Jan 2, 2026 through Jun 30, 2026.**

After Jun 30, 2026 the rate is scheduled to **revert to $0.0017/contract** (the pre-extension rate). A new ORF methodology is contemplated for Jul 1, 2026, subject to SEC review, so this is currently a moving floor — re-check before each quarterly cost-model refresh.

### Why this is the right rate for SPX

SPX (and SPXW, the weekly/0DTE variant) trade **only on Cboe Options Exchange ("C1")**. It is a proprietary listing — there is no multi-listing on PHLX, ISE, NYSE Arca, etc. So the ORF on SPX is whatever C1 sets. C2, BZX, EDGX have their own ORFs but those exchanges do not list SPX, so we ignore them.

The C1 ORF is what was extended — *not* increased — from a Dec 31, 2025 sunset to a Jun 30, 2026 sunset. The C2/BZX/EDGX rate filings on the same day in December 2025 were *increases* (e.g., C2: $0.0002 → $0.0003), which is why the four filings have very different titles. For SPX, the correct filing is **SR-CBOE-2025-085**, titled *"Notice of Filing and Immediate Effectiveness of a Proposed Rule Change To Extend the Sunset Date of the Current Options Regulatory Fee (ORF) From December 31, 2025 to June 30, 2026"*.

### Primary sources for the rate

1. Cboe's customer-facing fee notice PDF: [Cboe Options Exchange Regulatory Fee Update Effective January 2, 2026](https://cdn.cboe.com/resources/fee_schedule/2026/Cboe-Options-Exchanges-Regulatory-Fee-Update-Effective-January-2-2026.pdf) (PDF created 2025-11-26, posted in `cdn.cboe.com/resources/fee_schedule/2026/`).
2. SEC Federal Register notice for SR-CBOE-2025-085: [federalregister.gov/documents/2025/12/22/2025-23517](https://www.federalregister.gov/documents/2025/12/22/2025-23517/self-regulatory-organizations-cboe-exchange-inc-notice-of-filing-and-immediate-effectiveness-of-a). Quote from the filing: *"the current rate of $0.0023 per contract side ... reverting to $0.0017 per contract side on June 30, 2026."*
3. The May 1, 2026 consolidated Cboe Equity Options Fee Schedule confirms the same rate is still in force: [cdn.cboe.com/resources/membership/Cboe_FeeSchedule.pdf](https://cdn.cboe.com/resources/membership/Cboe_FeeSchedule.pdf).

### Distinguishing ORF from the OTHER per-contract CBOE charges on SPX

When the cost model says "CBOE fees", that's actually a stack of four separate line-items. For one SPX contract trade through IBKR you can see all of:

| Fee | Rate (per contract) | Sides charged | Source |
|---|---|---|---|
| Cboe ORF (regulatory) | **$0.0023** | sell only | [Cboe Jan 2 2026 notice](https://cdn.cboe.com/resources/fee_schedule/2026/Cboe-Options-Exchanges-Regulatory-Fee-Update-Effective-January-2-2026.pdf) |
| Cboe SPX Trade Processing Service | **$0.0025** | both | [Cboe Fee Schedule May 1 2026](https://cdn.cboe.com/resources/membership/Cboe_FeeSchedule.pdf) |
| Cboe SPX/SPXW Execution Surcharge ("Index Option Surcharge") | **$0.45** for non-floor electronic customer | both | [Cboe Fee Schedule May 1 2026](https://cdn.cboe.com/resources/membership/Cboe_FeeSchedule.pdf) |
| Cboe Marketing Fee / Cust Priority Surcharge | varies; for customer SPX usually $0.00 | n/a | same schedule |

Plus federal + clearing fees that are *not* CBOE-specific:

| Fee | Rate | Sides charged | Source |
|---|---|---|---|
| SEC Section 31 (FY2026, on/after Apr 4 2026) | **$20.60 per $1M of notional sales** = **$0.0000206 × notional** | sell only | [SEC Section 31 Fee Rate Advisory FY2026](https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2) |
| FINRA Trading Activity Fee (TAF) on options | **$0.00279 per contract**, capped $9.05/trade | sell only | [FINRA Sec. 1 Schedule A](https://www.finra.org/rules-guidance/guidance/faqs/trading-activity-fee) |
| OCC Clearing Fee | **$0.025/contract**, monthly cap of $55 per OCC member account | both | [OCC Schedule of Fees](https://www.theocc.com/company-information/schedule-of-fees) |

Note on the Section 31 calendar split for FY2026: from Oct 1, 2025 through Apr 3, 2026 the rate was **$0 / million** (a rare zero-rate period because FY2025 collections came in over target and the FY2026 appropriation arrived late). From Apr 4, 2026 onward it is **$20.60/million** (the SEC's order is dated March 2026, [FR 2026-04233](https://www.federalregister.gov/documents/2026/03/04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates)). Since we are migrating in May 2026, the live rate is **$20.60/million**.

### Does IBKR add a markup on regulatory fees?

**Regulatory fees (ORF, Section 31, TAF, OCC clearing) are passed through at cost.** This is a U.S. industry convention — broker-dealers do not mark up regulatory passthroughs in the per-transaction line item; they would have to disclose any markup separately under FINRA Rule 2122 / Reg ATS notices.

**Non-regulatory exchange fees (Trade Processing Service, Execution Surcharge, maker/taker rebates) are NOT a direct pass-through** under IBKR's Tiered plan. Quoting IBKR directly: *"IBKR's Tiered commission models are not intended to be a direct pass-through of exchange and third-party fees and rebates, and costs passed on to clients in IBKR's Tiered commission schedule may be greater than the costs paid by IBKR to the relevant exchange, regulator, clearinghouse or third party."* — [IBKR Other Fees](https://www.interactivebrokers.com/en/pricing/other-fees.php).

For our 0DTE iron condor cost model, what this means practically:
- Regulatory ORF on the IBKR ticket = exactly $0.0023/contract on sells. No markup.
- The Trade Processing Service $0.0025/contract that IBKR's [Cboe Options Fees passthrough page](https://www.interactivebrokers.com/en/accounts/fees/CBOEoptfee.php) lists is also at cost (it's an SRO fee, not a commercial markup-eligible fee).
- The IBKR commission itself ($0.65/contract Fixed, or tiered $0.25–$0.65 depending on monthly volume) is where IBKR makes its money — see [IBKR Options Commissions](https://www.interactivebrokers.com/en/pricing/commissions-options.php).

### Total per-execution cost math for a 10-contract iron condor

Round-trip closing a 10-contract SPX 0DTE iron condor = **4 legs × 10 contracts = 40 contracts** sold to close (and 40 contracts were bought to open at entry).

**Opening leg (4 × 10 = 40 contracts, BUY-side):**

| Fee | Per contract (buy side) | × 40 contracts |
|---|---|---|
| IBKR commission (Fixed plan worst case) | $0.65 | $26.00 |
| Cboe Trade Processing Service | $0.0025 | $0.10 |
| Cboe SPX Index Option Surcharge | $0.45 | $18.00 |
| OCC Clearing | $0.025 | $1.00 |
| ORF | $0.0000 (buy = exempt) | $0.00 |
| Section 31 | $0.0000 (buy = exempt) | $0.00 |
| FINRA TAF | $0.0000 (buy = exempt) | $0.00 |
| **Open total** |   | **~$45.10** |

**Closing leg (4 × 10 = 40 contracts, SELL-side, assume avg premium $0.50/contract → notional sales = 40 × $0.50 × $100 = $2,000):**

| Fee | Per contract (sell side) | × 40 contracts |
|---|---|---|
| IBKR commission (Fixed plan worst case) | $0.65 | $26.00 |
| Cboe Trade Processing Service | $0.0025 | $0.10 |
| Cboe SPX Index Option Surcharge | $0.45 | $18.00 |
| OCC Clearing | $0.025 | $1.00 |
| **ORF** | **$0.0023** | **$0.092** |
| FINRA TAF | $0.00279 | $0.1116 |
| Section 31 | $20.60/$M × $2,000 notional / 40 contracts | $0.0412 (whole order, not per contract) |
| **Close total** |   | **~$45.34** |

**Round trip per 10-lot IC: ~$90.45.** ORF is genuinely a rounding error on SPX index options because the contract is high-notional — the Cboe SPX Index Option Surcharge ($0.45/contract) and the IBKR commission itself ($0.65/contract) dominate. The ORF math you do has to be right because of compliance and reconciliation, but it is **not** a P&L driver.

If we move to IBKR Tiered with volume > 10,000 contracts/month, the commission drops to $0.25/contract, knocking ~$16/round-trip off — still $74-ish, still dominated by the Cboe SPX surcharge.

---

## Question 6 — `accountSummary` tag that returns the live USD-tradable amount on an EUR-base account

### Headline answer

For an EUR-base IBKR account that needs to gate orders on **live USD buying power**, the correct call is:

```python
# ib_async 2.1.0 — pure async, recommended
await ib.reqAccountSummaryAsync(
    group="All",
    tags="BuyingPower,AvailableFunds,ExcessLiquidity,FullAvailableFunds,"
         "MaintMarginReq,$LEDGER:USD",
)
```

then read items where `item.currency == "USD"` for the USD-specific cash/balance fields, and read `BuyingPower / AvailableFunds / ExcessLiquidity / MaintMarginReq` (which are *always* base-currency = EUR for a EUR-base account) and **convert at the live FX rate also returned in the `$LEDGER:USD` block**.

### How TWS exposes per-currency data

Two distinct concepts get conflated in IBKR's docs:

**(A) The 30+ "summary" tags** — `BuyingPower`, `AvailableFunds`, `ExcessLiquidity`, `FullAvailableFunds`, `MaintMarginReq`, `NetLiquidation`, `EquityWithLoanValue`, `Cushion`, etc.

These are **always reported in the account's base currency**. They are *not* per-currency. For a EUR-base account, `BuyingPower` is **EUR-denominated**, even if the trade you want to do is USD-denominated. There is no `BuyingPower@USD` tag — the TWS data model does not have one. Confirmed in the [TWS API Account Summary docs](https://interactivebrokers.github.io/tws-api/account_summary.html) and the [ib_async 2.1.0 wrapper](https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ib.html).

**(B) The `$LEDGER` pseudo-tag** — emits a per-currency view of the **cash ledger**. Emits one row per currency the account holds, with `CashBalance`, `TotalCashBalance`, `RealizedPnL`, `UnrealizedPnL`, `ExchangeRate`, etc.

Three flavors:
- `$LEDGER` — base currency only (one row, currency = base).
- `$LEDGER:USD` (or `:EUR`, `:HKD`, etc.) — that currency only (one row, currency = that ISO code).
- `$LEDGER:ALL` — every currency the account has activity in (N rows). ib_async's default `accountSummary()` tag list includes `$LEDGER:ALL`.

So **the USD-tradable amount is computed, not read directly:**

```
USD_tradable = EUR_BuyingPower × ExchangeRate_USD_per_EUR
             + USD_CashBalance (if any, from $LEDGER:USD)
```

IBKR computes this internally — when you submit a USD options order, the margin check is done in base currency *after* applying the live FX rate IBKR holds. There is no broker-side "USD buying power" gate; the gate is base-currency `AvailableFunds`.

### What IBKR actually checks pre-trade

For an SPX iron condor with $5-wide spreads × 10 contracts, the **margin requirement = $5 × $100 × 10 × 2 verticals = $10,000 USD**. IBKR will convert that $10,000 USD to EUR at the live ECB-derived FX rate (the `ExchangeRate` value emitted on the `$LEDGER:USD` row) and compare against your EUR `AvailableFunds`. So the bot's pre-entry check should mirror that:

```python
async def usd_buying_power(ib: IB) -> float:
    """Return live USD-equivalent buying power for the EUR-base account."""
    summary = await ib.reqAccountSummaryAsync(
        group="All",
        tags="AvailableFunds,$LEDGER:USD",
    )
    eur_avail = next(
        float(v.value) for v in summary
        if v.tag == "AvailableFunds" and v.currency in ("", "EUR")
    )
    # $LEDGER:USD emits ExchangeRate as USD-per-base; e.g. 1.09 means
    # 1 EUR = 1.09 USD.
    usd_per_eur = next(
        float(v.value) for v in summary
        if v.tag == "ExchangeRate" and v.currency == "USD"
    )
    usd_cash = next(
        (float(v.value) for v in summary
         if v.tag == "CashBalance" and v.currency == "USD"),
        0.0,
    )
    return eur_avail * usd_per_eur + usd_cash
```

### Update cadence — the question that bites people

The TWS docs are explicit: ["every three minutes those values which have changed will be returned. The update frequency of 3 minutes is the same as the TWS Account Window and cannot be changed."](https://interactivebrokers.github.io/tws-api/account_summary.html)

**This means `AvailableFunds` does NOT update on every fill.** A burst of 0DTE fills can blow through your real buying power and `AvailableFunds` won't reflect it for up to 3 minutes. IBKR's *server-side* margin engine sees fills instantly and will reject orders that exceed margin, but the client-side number you read is stale up to 3 minutes.

**Mitigations the bot should implement:**

1. **Subtract reserved margin client-side**: after submitting each order, decrement the cached `usd_buying_power` by the order's margin requirement until the next `accountSummary` tick rolls in. The bot should not trust the API to report the post-fill state in time.
2. **Use `reqPnL` + `reqPnLSingle` for the equity curve** — those are real-time, pushed on every fill. `accountSummary` for buying power, `reqPnL` for unrealized.
3. **Listen for `errorEvent` with code 201/202** — those fire when an order is rejected for margin, which is the only definitive signal that the live server-side number was breached.
4. **`FullAvailableFunds` vs `AvailableFunds`**: `FullAvailableFunds` reflects the post-SMA-recalc state used at the start of the next trading day. For 0DTE intraday gating, use plain `AvailableFunds` — `Full*` is for overnight/swing logic.

### Edge cases & gotchas

- **FX conversion timing**: IBKR holds the FX rate as a snapshot — it does *not* tick on every quote in the FX market. The `ExchangeRate` field in `$LEDGER:USD` updates with the rest of the 3-minute account summary cadence. For tight pre-trade USD buying-power checks, the bot should pad by 50–100 bps to absorb intraday EUR/USD drift.
- **"Soft" vs "hard" margin**: IBKR runs both a real-time SMA check (hard — orders rejected) and an end-of-day full-recalc (soft — can produce a margin call). The API only exposes the hard check via the `MaintMarginReq` and `AvailableFunds` fields. For our IC strategy where positions close intraday, the soft check never fires.
- **Settlement lag on USD cash**: USD option premium collected today shows up in `CashBalance` (USD row) at T+1 settlement, but the SMA / `AvailableFunds` math credits it immediately via `EquityWithLoanValue`. So `usd_buying_power` computed from `AvailableFunds × FX + USD_CashBalance` is correct — it doesn't double-count, because pending settlement isn't in `CashBalance`.
- **Tags filtered on `accountSummary` are case-sensitive** — `$LEDGER:USD` works; `$ledger:usd` silently returns nothing.

### Why `reqAccountSummary` over `reqAccountUpdates`

`reqAccountUpdates(true, "DU1234567")` returns the same data plus a portfolio snapshot, but it's a one-account-at-a-time call and TWS will refuse to subscribe to more than one at once. `reqAccountSummary` with `group="All"` is cleaner for our single-account case and is what ib_async's `accountSummary()` helper uses under the hood. Both share the **same 3-minute update cadence** — the server doesn't push faster regardless of which call you make.

### Working code (ib_async 2.1.0)

```python
import asyncio
from ib_async import IB

ASYNC_TAGS = (
    "BuyingPower,AvailableFunds,ExcessLiquidity,FullAvailableFunds,"
    "MaintMarginReq,NetLiquidation,$LEDGER:USD"
)

async def live_usd_tradable(ib: IB) -> dict:
    """Return live USD-tradable buying power + diagnostics for a EUR-base account.

    Updates every 3 minutes (TWS-enforced cadence). Caller must reserve
    margin client-side for in-flight orders.
    """
    rows = await ib.reqAccountSummaryAsync(group="All", tags=ASYNC_TAGS)

    def get(tag, currency=None, default=None):
        for v in rows:
            if v.tag != tag:
                continue
            if currency is None or v.currency == currency:
                return v.value
        return default

    eur_avail = float(get("AvailableFunds", default="0") or 0)
    usd_per_eur = float(get("ExchangeRate", "USD", default="1.0") or 1.0)
    usd_cash = float(get("CashBalance", "USD", default="0") or 0)
    return {
        "usd_tradable": eur_avail * usd_per_eur + usd_cash,
        "eur_available_funds": eur_avail,
        "usd_per_eur_rate": usd_per_eur,
        "usd_cash_balance": usd_cash,
        "maint_margin_eur": float(get("MaintMarginReq", default="0") or 0),
    }


async def main():
    ib = IB()
    await ib.connectAsync("127.0.0.1", 7497, clientId=11)
    try:
        snapshot = await live_usd_tradable(ib)
        print(snapshot)
    finally:
        ib.disconnect()


asyncio.run(main())
```

The synchronous variant is identical with `ib.accountSummary()` (no `await`) — ib_async exposes both patterns from the same `IB` object.

### Sources for Question 6

- [TWS API v9.72+ Account Summary docs](https://interactivebrokers.github.io/tws-api/account_summary.html) — authoritative on tag list, `$LEDGER` semantics, and the 3-minute cadence.
- [ib_async 2.1.0 IB module source](https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ib.html) — confirms `accountSummary()` default tag list ends with `$LEDGER:ALL` and that the sync/async pattern is fully exposed.
- [ib_async 2.1.0 readme](https://ib-api-reloaded.github.io/ib_async/readme.html) — sync/async dual-pattern explainer.
- [erdewit/ib_insync issue #136 — Currency in accountSummary](https://github.com/erdewit/ib_insync/issues/136) — first-hand confirmation that `$LEDGER:USD` is the supported way to filter; the ib_insync archived state was inherited and fixed in ib_async.

---

## Bottom-line numbers the migration plan can lock in

- **SPX ORF (Cboe C1)**: **$0.0023/contract, sell-side only, through Jun 30 2026.** Re-check before July 1 — reverts to $0.0017 or moves to a new methodology.
- **SEC Section 31 (FY2026, post-Apr 4 2026)**: **$20.60 per $1M of notional sales**, sell-side only.
- **OCC clearing**: $0.025/contract both sides, monthly cap $55/member account.
- **Cboe Trade Processing Service**: $0.0025/contract both sides.
- **Cboe SPX Index Option Surcharge**: $0.45/contract both sides (the real cost driver, dwarfs the ORF by ~200×).
- **IBKR commission**: $0.65/contract Fixed; $0.25–$0.65 Tiered (no regulatory markup).
- **USD-tradable for EUR-base account**: `AvailableFunds × ExchangeRate@USD + CashBalance@USD`, refreshed every 3 minutes; bot reserves margin client-side between ticks.
