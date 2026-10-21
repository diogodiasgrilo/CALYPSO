# signal-ingest — integration + runbook (TradingView → recorded trade intents)

**Status 2026-10-21: built, tested (49 tests), NOT deployed.** Localhost-only by
operator choice; the systemd unit is not installed and no public URL exists.

What it does: receives a TradingView webhook, validates it, de-duplicates it,
applies caps, records it, and updates a **simulated** position book so the run
can answer both *how often did signals fire* and *would they have made money*.

What it cannot do: **place an order.** Nothing in `services/signal_ingest/`
imports `IBClient`, `BrokerClient` or any `bots.hydra` module — the same
invariant `dashboard/` holds, pinned by a subprocess test. `dry_run: false` is
refused at startup with an explanation. Going live is a reviewed code change
(add a broker dependency + a mode), **not a config flip**.

---

## 1. TradingView side — exactly what to do

### 1.1 Prerequisite

Webhooks require **Pro+ or above**. On Free/Essential the only alert outputs are
email and in-app popups, and there is no webhook field at all. ✅ Confirmed
available 2026-10-21.

### 1.2 Create the alert

1. Open the chart with the indicator applied.
2. Right-click → **Add alert** (or the clock icon → **Create alert**).
3. **Condition**: choose the indicator and the specific alert condition it
   exposes. ⚠️ If the dropdown offers only "Any alert() function call" or
   nothing at all, the script has no `alertcondition()` and **cannot drive
   automation** — that is Check 1 failing, and it stops here.
4. **Alert actions** → tick **Webhook URL** and paste the endpoint (§3).
5. **Message**: paste the JSON below. This is the contract.

### 1.3 The exact alert message

```json
{
  "secret": "PUT_THE_SHARED_SECRET_HERE",
  "ticker": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "price": {{close}},
  "time": "{{timenow}}",
  "source": "IADSS"
}
```

Notes, each of which matters:

- `{{strategy.order.action}}` resolves to `buy`/`sell` and is only available on a
  **`strategy()`** script. On a plain `indicator()` there is no order action —
  hardcode `"action": "buy"` on a buy alert and create a second alert with
  `"action": "sell"`.
- `price` is **unquoted** — `{{close}}` substitutes a bare number. Quoting it
  still works (the service coerces), but unquoted is correct.
- `"time": "{{timenow}}"` is **not optional in practice.** It is what makes a
  resend idempotent. Without it the service falls back to a one-minute arrival
  bucket, which is a weaker guarantee (see §5.2).
- `source` is a free label. Use it to tell indicators apart in the ledger.
- **The secret is in the body, not a header**, because TradingView cannot be
  relied on to send custom auth headers.

### 1.4 TradingView's constraints (official)

| constraint | value | consequence |
|---|---|---|
| Egress IPs | `52.89.214.238`, `34.212.75.30`, `54.218.53.128`, `52.32.178.7` | the nginx allowlist in §4 |
| Ports | **80 and 443 only** | no custom port on the public URL |
| Timeout | **3 seconds**, then cancelled | measured worst case is <1s (`TestLatencyBudget`) |
| IPv6 | not supported | the public record must have an A record |

---

## 2. Deploy (localhost-only — the current, authorized step)

```bash
# 1. Pull + install the unit
gcloud compute ssh calypso-bot --zone=us-east1-b --project=calypso-trading-bot \
  --account=diogodiasgrilo@gmail.com --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull'"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/signal-ingest.service /etc/systemd/system/ && sudo systemctl daemon-reload"

# 2. Config from the template (the real one is gitignored)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso cp -n /opt/calypso/services/signal_ingest/config.json.template /opt/calypso/services/signal_ingest/config.json"

# 3. The secret goes in a systemd drop-in, NEVER in the config file
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo mkdir -p /etc/systemd/system/signal-ingest.service.d && printf '[Service]\nEnvironment=\"SIGNAL_INGEST_SECRET=%s\"\n' \"\$(openssl rand -hex 24)\" | sudo tee /etc/systemd/system/signal-ingest.service.d/secret.conf >/dev/null && sudo chmod 600 /etc/systemd/system/signal-ingest.service.d/secret.conf && sudo systemctl daemon-reload"

# 4. Start + verify
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl enable --now signal-ingest && sleep 5 && curl -s http://127.0.0.1:8002/health"
```

**Expected `/health`:** `{"status":"ok","dry_run":true,"symbols":[...],
"secret_configured":true,"signals_today":0}`.

**Read the secret back** (you need it for the TradingView alert body):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo grep -o 'SIGNAL_INGEST_SECRET=[^\"]*' /etc/systemd/system/signal-ingest.service.d/secret.conf"
```

**Smoke test with no TradingView at all:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && SIGNAL_INGEST_SECRET=<secret> .venv/bin/python -m scripts.simulate_tradingview_alert --ticker BTC --action buy --price 60000 && .venv/bin/python -m scripts.simulate_tradingview_alert --ticker BTC --action sell --price 61000'"
curl -s http://127.0.0.1:8002/summary     # expect round_trips=1, positive gross
```

**Rollback:** `sudo systemctl disable --now signal-ingest`. It owns nothing else
— no trading process depends on it and it depends on none.

---

## 3. Public exposure (NOT AUTHORIZED YET — requires an explicit decision)

TradingView cannot reach `127.0.0.1`. A real alert needs a public path. **Do not
do this without a deliberate go-ahead**: it puts an order-shaped URL on the open
internet.

Three controls, all required, none sufficient alone:

1. **Shared secret** in the body (already enforced; blank refuses everything).
2. **IP allowlist** at nginx — the four addresses in §1.4.
3. **Path obscurity** — a non-guessable path, which is a nicety, not a control.

### nginx fragment (to add to `dashboard/deploy/nginx-dashboard-public.conf`)

```nginx
location /tv-webhook-<random-suffix> {
    allow 52.89.214.238;  allow 34.212.75.30;
    allow 54.218.53.128;  allow 52.32.178.7;
    deny  all;

    proxy_pass http://127.0.0.1:8002/webhook;
    proxy_set_header Host $host;
    # ⚠️ OVERWRITE, never $proxy_add_x_forwarded_for — the same invariant the
    # dashboard's login rate-limiter depends on. Appending lets a client forge it.
    proxy_set_header X-Forwarded-For $remote_addr;

    # TradingView cancels at 3s; fail fast rather than hold its connection.
    proxy_read_timeout 5s;
    client_max_body_size 8k;
}
```

⚠️ **Do not** put this behind the dashboard's session-cookie auth — TradingView
cannot log in. The secret + IP allowlist are the authentication for this path.

---

## 4. Reading the results — the point of the exercise

```bash
# Has anything fired, and would it have worked?
curl -s http://127.0.0.1:8002/summary

# Signals per ticker per month — THE question (see §6)
sudo -u calypso sqlite3 /opt/calypso/data/signal_ingest/signals.db \
  "SELECT substr(received_at,1,7) m, ticker, COUNT(*) FROM signals
   WHERE outcome='would_trade' GROUP BY m, ticker ORDER BY m;"

# Why were signals declined? (rejections are recorded ON PURPOSE)
sudo -u calypso sqlite3 /opt/calypso/data/signal_ingest/signals.db \
  "SELECT outcome, reason, COUNT(*) FROM signals GROUP BY outcome, reason ORDER BY 3 DESC;"

# Round trips
sudo -u calypso sqlite3 /opt/calypso/data/signal_ingest/signals.db \
  "SELECT symbol, COUNT(*), ROUND(SUM(gross_pnl),2) FROM sim_roundtrips GROUP BY symbol;"
```

🔴 **The P&L is an UPPER BOUND.** It prices round trips from the signal's own
`{{close}}`, so it ignores slippage, spread and commission. This project has
measured what that gap costs for real: entry rung pricing was **~38% of one
variant's net**, and stop-close slippage **about a third of another's**. Read
these numbers as "is there anything here at all", never as expected return.

---

## 5. Design decisions worth knowing before you trust the output

### 5.1 A sell with no position is NOT a short
Shorting is a different risk shape — borrow, unlimited loss, different margin.
Inferring one from a bare "sell" would silently invent exposure the signal never
asked for. It is recorded as `flat_noop` with a reason.

### 5.2 Only ADMITTED signals dedupe, and only they move the book
A *rejected* signal may be retried, because rejections are often transient
(market closed, daily cap). A resend of an *admitted* one is a `duplicate` and
changes nothing. Dedup runs **before** the daily cap so a resend cannot consume
budget.

### 5.3 Oversized quantities clamp, they do not reject
An oversized signal still trades at a size **we** chose, and the clamp is
recorded, so the difference stays visible. The indicator is a black box; the
limits must be ours.

### 5.4 Everything returns HTTP 200
Non-2xx makes TradingView retry. A retry storm against a permanent rejection is
pure noise. **The ledger is the record, not the status code.**

### 5.5 Rejections and duplicates are stored
A ledger holding only admitted signals answers the easy question and loses the
useful one.

---

## 6. What is still UNKNOWN — the honest gate list

Building the receiver did not answer any of these. They are the decision, and
the service is only the instrument.

| # | Question | Status |
|---|---|---|
| 1 | Can a signal leave TradingView? | ✅ **PASSED** — Pro+ confirmed 2026-10-21 |
| 2 | Is it a signal or commentary? | ❌ **OPEN.** The one inspectable script (`IA-SOL-Upside-Model`) is an `indicator()` with **no alerts** — a projection overlay. IADSS ($175/mo) is sold as "trading signals" and is the only candidate; it cannot be inspected without paying |
| 3 | Whose IBKR account trades? | ❌ **OPEN.** Dad's user is read-only *by design*; ours is paper. No real-money account exists for this |
| 4 | Frequency ≥ ~50 signals/yr? | ❌ **OPEN** — below that nothing resolves in useful time. This is what the ledger measures |
| 5 | Does the account clear the subscription drag? | ❌ **OPEN.** $1,788–2,748/yr is **8.9–13.7% at $20k**, 1.8–2.7% at $100k |
| 6 | Does IBKR **paper** support crypto? | ❌ **UNVERIFIED.** IBKR added SOL/BTC/ETH + more via Paxos/Zero Hash, but paper support is unconfirmed and permissions must be enabled |

### Not designed at all (deliberately)

The live order path. There is no position reconciliation against a real broker,
no sizing model beyond a fixed per-symbol quantity, no stop loss, no handling of
a signal that arrives while a real position disagrees with the simulated one.
**That is a separate design, and it should not be written until gates 2–5 pass.**

⚠️ And from this project's own history: **a dry run cannot test the order path.**
That is how the entry fill leak stayed hidden for months. Whatever this ledger
shows, the first real order will still be the first real test.
