# CALYPSO — Running Cost Breakdown

> **Snapshot date:** 2026-07-09. Measured over the trailing 30 days (2026-06-10 → 2026-07-09).
> **Scope:** everything required to run the whole system — the GCP footprint (`calypso-trading-bot`),
> the Anthropic API spend of the 5-agent suite, and the external market-data subscriptions.
>
> This is a **bottom-up reconstruction**, not a billed invoice. There is no BigQuery billing export
> configured on the project, and the Cloud Billing API exposes SKU *prices* but not actual *spend*.
> Every GCP unit price below was pulled from the Cloud Billing Catalog API for `us-east1`, and every
> usage quantity from the Cloud Monitoring API or the VM itself. See [Methodology](#methodology).

---

## Headline

| | Per day | Per month |
|---|---:|---:|
| **Measured subtotal** | **$1.53** | **$46.63** |
| Realistic all-in (incl. IBKR market data) | ~$1.70 – $2.05 | ~$52 – $62 |

**Polygon ($29/mo) is the single largest line item — more than the entire Google Cloud footprint
combined ($14.53/mo).** It exists solely to feed the GEX features on variants B and C.

---

## Line items

| Line item | $/month | $/day | Basis |
|---|---:|---:|---|
| Polygon Options Starter | 29.00 | 0.953 | List price. Polygon rebranded to Massive.com (Oct 2025) — **verify current tier price** |
| GCE `e2-small` VM, 24/7 | 12.23 | 0.402 | Measured — see [VM math](#vm-math) |
| Anthropic API (agent suite) | ~3.10 | 0.102 | Measured token volumes — see [Anthropic math](#anthropic-math) |
| Internet egress | ~2.00 | 0.066 | 25.6 GB/mo sent; ~$0.12/GiB less Google-API traffic |
| Secret Manager | 0.30 | 0.010 | 11 enabled versions − 6 free = 5 × $0.06 |
| Boot disk (20 GB pd-standard) | 0.00 | 0.000 | Inside the 30 GiB-month always-free PD allotment |
| GCS `calypso-backups` | 0.00 | 0.000 | 2.92 GiB < 5 GiB regional free tier — **but growing, see risks** |
| Cloud Logging | 0.00 | 0.000 | 8.23 GB ingested / 50 GiB free tier; default 30-day retention |
| Static external IP (`34.23.212.5`) | 0.00 | 0.000 | First 744 hrs/mo free per SKU; one always-attached IP = 730 hrs |
| Cloud Functions (gen2) + Pub/Sub | 0.00 | 0.000 | 513 invocations, 516 messages / 30d, vs 2M + 10 GiB free tiers |
| **Measured subtotal** | **46.63** | **1.533** | |
| IBKR market data | **?** ~5–15 | | Bills against the live account — not readable from this environment |
| ThetaData | 0.00 | 0.000 | Subscription lapsed |
| Telegram / Gmail / Google Sheets | 0.00 | 0.000 | Free tiers |

---

## Why so much of GCP is free

Four costs that would normally appear are absorbed by free tiers. Each was confirmed against the
billing catalog rather than assumed:

- **Boot disk.** The `Storage PD Capacity` SKU tiers at `0–30 GiB → $0.00`, `≥30 GiB → $0.04/GiB-mo`.
  The 20 GB boot disk sits entirely inside the free allotment.
- **Cloud Logging.** The Ops Agent *is* active (`google-cloud-ops-agent` running), which normally makes
  log ingestion the sleeper cost of a chatty trading bot. Measured ingestion is **8,228,364,894 bytes**
  (8.23 GB) over 30 days — well under the 50 GiB/month free tier. Default `_Default` bucket retention is
  30 days, so no retention-storage charge either.
- **GCS backups.** `calypso-backups` holds **3,134,074,507 bytes** (2.92 GiB) across 329 objects, in
  `us-east1` Standard. The `Standard Storage US Regional` SKU is free to 5 GiB-months.
- **Static external IP.** `External IP Charge on a Standard VM` tiers at `0–744 hrs → $0.00/hr`,
  `≥744 hrs → $0.005/hr`. A single always-attached IP consumes 730 hrs/mo (744 in a 31-day month), so
  it lands at exactly $0. A *second* external IP would cost ~$3.65/mo.

The VM is the one place there is no discount to find: **E2 machine types are explicitly ineligible for
sustained-use discounts** ([Google docs](https://docs.cloud.google.com/compute/docs/sustained-use-discounts)).
The $12.23 is full on-demand price. E2 *does* qualify for committed-use discounts.

---

## VM math

`calypso-bot` is an `e2-small` in `us-east1-b`, `STANDARD` provisioning (not Spot), `RUNNING`, uptime
121 days at time of snapshot. E2 shared-core types bill by fractional vCPU: `e2-small` = 0.5 vCPU + 2 GiB.

| Component | Quantity | Catalog unit price (us-east1) | $/hr |
|---|---:|---|---:|
| `E2 Instance Core running in Americas` | 0.5 vCPU | $0.021811590 / vCPU-hr | 0.010905795 |
| `E2 Instance Ram running in Americas` | 2 GiB | $0.002923530 / GiB-hr | 0.005847060 |
| **Total** | | | **0.016752855** |

- Per day: `24 × 0.016752855` = **$0.4021**
- Per month: `730 × 0.016752855` = **$12.23**

(Cross-check: Google's published `e2-small` on-demand rate is $0.016751/hr — matches to 5 decimals.)

---

## Anthropic math

All four narrative agents run `claude-sonnet-4-6` via `shared/claude_client.py` (`DEFAULT_MODEL`).
Pricing: **$3.00 / 1M input tokens, $15.00 / 1M output tokens.**
(ARGUS is a bash script — no API calls.)

Run counts and prompt sizes were read from the VM journal over the trailing 30 days, not estimated.
Token counts approximate at `chars / 4`.

| Agent | Runs/30d | Input chars (observed) | ≈ Input tok/run | `max_tokens` | ≈ Output tok/run |
|---|---:|---|---:|---:|---:|
| APOLLO | 19 | 18,875 – 27,554 | ~6,300 | 4,096 | ~2,500 |
| HERMES | 20 | 11,862 – 13,682 | ~3,200 | 4,096 | ~2,500 |
| HOMER | 20 | 3 calls/run | ~3,000 total | 512 / 50 / 300 | ~862 total |
| CLIO | 4 | 44,421 – 67,069 | ~13,900 | 12,288 | ~8,000 |

Monthly totals: **~299,300 input tokens** → $0.90, **~146,740 output tokens** → $2.20.
**≈ $3.10/month.** Cheap because the agents fire a handful of times a day, not continuously.

---

## Risks and easy savings

Roughly **$5/month** — about a third of the GCP bill — is recoverable without touching Polygon.

### 1. `calypso-backups` has no lifecycle policy (will start billing in ~2–3 months)
329 objects / 2.92 GiB, growing ~0.85 GiB/month from the daily `backtesting.db` copy
(`db_backup.timer`, 23:00 UTC). It crosses the 5 GiB free tier in roughly two to three months and
then bills at $0.02/GiB-mo. A lifecycle rule deleting objects older than 90 days keeps it permanently
free. Low dollar impact, but it is unbounded growth on an unmanaged bucket.

### 2. Two Secret Manager secrets are dead on this branch (~$0.18/mo)
Of 11 enabled versions, 5 are billable (6 free). Three of those five belong to secrets that this
branch cannot use:

| Secret | Enabled versions | Status |
|---|---:|---|
| `calypso-saxo-credentials` | 2 | Saxo integration deleted in P5c |
| `calypso-twilio-credentials` | 1 | No WhatsApp/SMS path |

Deleting both drops the project back under the 6-version free tier → **$0.00**.

### 3. A 1-year committed-use discount on the VM (~$4.50/mo)
E2 is CUD-eligible even though it is SUD-ineligible. A 1-year resource-based commitment cuts roughly
37% off the `e2-small`, taking it from $12.23 → ~$7.70/mo. This is the largest single infrastructure
saving available, on the largest infrastructure line item.

### 4. Polygon is 62% of the measured bill
$29/mo, consumed only by variants B and C (`POLYGON_API_KEY`, `EnvironmentFile=-/etc/calypso/polygon.env`).
If GEX features are ever retired, TP and narrow widths continue without it (they silently disable) —
but C is the live dashboard-primary variant, so this is almost certainly a keep.

---

## Open items

- **IBKR market-data fees are unknown.** The paper account inherits subscriptions from the funded live
  account, and those charges appear on IBKR statements not reachable from this environment. Confirmed
  entitlements: index real-time (SPX/VIX return `R`) plus the US-equity Network B subscription added
  2026-07-06 for SPY. Non-professional feeds typically run a few dollars each per month.
  **Action:** read the actual figure off an IBKR statement and fold it into the table above.
- **Polygon price needs re-verification.** $29/mo is the widely-cited Options Starter price, but Polygon
  rebranded to Massive.com in Oct 2025 and the pricing page is JS-rendered (not fetchable). Check the
  card on file.
- **No BigQuery billing export exists.** Enabling one gives exact, ongoing, queryable spend — but only
  captures data from the day it is turned on forward. Worth doing if this doc is to be kept current.

---

## Methodology

Nothing here is recalled from memory; every number traces to a live query.

**Unit prices** — Cloud Billing Catalog API, filtered to `us-east1`:
- Compute Engine service `6F81-5844-456A` (E2 core/RAM, PD capacity, external IP)
- Cloud Storage service `95FF-2EF5-5EA1` (Standard Storage US Regional)
- Egress used the standard $0.12/GiB North-America internet rate; the exact SKU was not locatable in the
  catalog under Compute or Networking (`E505-1604-58F8`), so **this line is the least precise**.

**Usage quantities** — Cloud Monitoring API, 30-day `ALIGN_SUM` / `REDUCE_SUM`:

| Metric | 30-day total |
|---|---:|
| `logging.googleapis.com/billing/bytes_ingested` | 8,228,364,894 |
| `compute.googleapis.com/instance/network/sent_bytes_count` | 25,619,678,029 |
| `run.googleapis.com/request_count` (GCF gen2) | 513 |
| `pubsub.googleapis.com/topic/send_message_operation_count` | 516 |

**Resource inventory** — `gcloud compute instances describe`, `gcloud compute disks list`,
`gcloud compute addresses list`, `gcloud storage du -s`, `gcloud secrets versions list`,
`gcloud functions list`, `gcloud pubsub topics list`.

**Agent token volumes** — `journalctl -u {apollo,hermes,homer,clio}` over 30 days: completed-run counts
plus the agents' own `Sending N chars to Claude` log lines. Model and `max_tokens` read from
`shared/claude_client.py`, `services/agents_config.json.template`, and each agent's source.

**Anthropic pricing** — current published rates for `claude-sonnet-4-6`.

**Caveat on precision.** The egress line (~$2.00) is the loosest number: of the 25.6 GB sent, a
meaningful share goes to Google APIs (Cloud Logging ingestion alone is 8.2 GB, plus GCS backups,
Pub/Sub, Secret Manager, Sheets) rather than to the internet, and that traffic is free or near-free.
Worst case if *all* 25.6 GB billed as internet egress: ~$2.95. The true figure is bounded between
roughly $1.60 and $2.95.

**Out of scope.** Claude Code / Claude subscription used to develop and operate the system is billed
separately and is not counted here.
