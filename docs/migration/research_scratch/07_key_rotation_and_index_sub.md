# 07 — IBKR OAuth 2.0 Key Rotation + Cboe Index Streaming Subscription

**Research date:** 2026-05-13
**Context:** Saxo → Interactive Brokers migration for CALYPSO 0DTE bot. Two specific data points needed to finalize the migration plan: (1) the operational cadence/policy for rotating IBKR OAuth 2.0 client public keys, and (2) the exact May 2026 monthly fee for the IBKR market-data subscription that delivers live SPX + VIX index quotes.

---

## Question 2 — Public-key rotation cadence for IBKR OAuth 2.0 `private_key_jwt`

### Short answer

**IBKR does NOT publicly document a forced rotation cadence for OAuth 2.0 client public keys.** No schedule (annual, biennial, expires-in-N-days) is mentioned in any IBKR-published material that could be located. In practice the key registered through the Self-Service Portal / Message Center remains valid until the developer themselves rotates it or IBKR support disables it on request.

This is consistent with how IBKR runs its 18-year-old OAuth 1.0a flow (legacy "OAuth 1.0a Extended" for individual accounts is unchanged since the 2018 spec — see https://www.interactivebrokers.com/webtradingapi/oauth.pdf), where the consumer's RSA-SHA256 public keys also have no documented expiry.

What *is* documented (or de-facto observable from third-party SDKs that have integrated successfully) is summarized below.

### What IBKR explicitly says

The only public IBKR documentation for OAuth 2.0 lives on IBKR Campus at https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-oauth-2-0/ (blocked from automated scraping but indexed in search). The relevant content distilled from search snippets and from the IBKR-supplied setup PDF that the `datawookie/ibauth` PyPI package mirrors at https://raw.githubusercontent.com/datawookie/ibauth/master/ib-oauth.pdf:

- **Authentication scheme.** "IBKR only supports `private_key_jwt` client authentication as described in RFC 7521 and RFC 7523." The client presents a signed JWT `client_assertion`; IBKR validates it against the public key(s) registered for that `client_id`. Quoted in `https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/` and reinforced by community references at https://github.com/Voyz/ibind/issues/102.
- **Multiple keys per client_id.** The IBKR Campus page literally uses the plural — "the public key(s) provided by the client during registration" — confirming that *more than one* public key per OAuth client is supported. This is the seam used for overlap rotation. [unconfirmed: no public ceiling on N is documented; third-party SDKs assume at least 2.]
- **Key registration channel.** Public keys are uploaded by the developer through one of two paths:
  1. The IBKR Self-Service Portal for institutional / Org accounts (https://www.ibkrguides.com/orgportal/performanceandstatements/open-authorization.htm).
  2. For individual accounts: log in to `www.ibkr.com`, click the Message Center bell icon, open a ticket of type "API → REST / Web API", and paste/upload the public-key PEM. IBKR manually approves; turnaround is reported as 1–3 business days. Source: `ibauth` PyPI page documentation at https://pypi.org/project/ibauth/0.0.11/.
- **Algorithm + key size.** IBKR's setup guide (the May 16, 2018 PDF, which is still the canonical document; mirrored in `datawookie/ibauth`) requires **2048-bit RSA** keys for OAuth 1.0a signing. The `ibauth` Python package recommends **3072-bit RSA** as a reasonable default for OAuth 2.0 with optional 4096-bit for higher security — this is library guidance, not an IBKR requirement; IBKR itself accepts ≥ 2048-bit RSA. **No ECDSA support is documented for OAuth 2.0.** The `ibind` v0.1.23 OAuth 2.0 implementation in pull request https://github.com/Voyz/ibind/pull/106 hard-codes RSA only (`pycryptodome` `RSA.import_key` + `PKCS1_v1_5` signer over `SHA256`). Algorithm in the JWT header is fixed at `RS256`. See file `ibind/oauth/oauth2.py` in PR #106.
- **Key format.** PEM. The library accepts either `private_key_pem` (string) or `private_key_path` (file). Public key uploaded to IBKR is the corresponding PEM-encoded public key (PKCS#8). JWK is not supported — there is no JWKS endpoint and IBKR does not advertise one. [unconfirmed for JWKS]
- **Distinct keys for paper vs production.** IBKR's own onboarding response (paraphrased in the `ibauth` README) is that "the RSA key for production cannot be the same as the key used for QA." A second key pair must be registered separately on the production OAuth client_id.

### JWT claims structure (extracted verbatim from the working `ibind` PR #106 implementation, which was developed against live IBKR endpoints)

The working OAuth 2.0 flow uses **two distinct JWT assertion shapes** depending on which IBKR endpoint is being hit. Source: `ibind/oauth/oauth2.py` in https://github.com/Voyz/ibind/pull/106 (open as of 2026-05-13, tested end-to-end against `https://api.ibkr.com` by the PR author):

**JWT header (both shapes):**
```json
{
  "alg": "RS256",
  "typ": "JWT",
  "kid": "<client_key_id>"
}
```
The `kid` is the **Client Key ID** that IBKR assigns when you register the public key — it is the rotation-identification primitive, allowing IBKR to keep multiple active public keys under one `client_id` and match the incoming JWT to the right one.

**Shape 1 — JWT for the `/oauth2/api/v1/token` endpoint (access-token request):**
```json
{
  "iss": "<client_id>",
  "sub": "<client_id>",
  "aud": "/token",
  "exp": <now + 60>,
  "iat": <now - 10>
}
```
Notable: very short lifetime (**60 seconds**), `iat` is set 10 seconds in the past to absorb clock skew, no `jti`, no `nbf`, `aud` is the literal string `/token` not a full URL.

**Shape 2 — JWT for the `/gw/api/v1/sso-sessions` endpoint (session-init request):**
```json
{
  "ip": "<public_ip>",
  "credential": "<ibkr_username>",
  "iss": "<client_id>",
  "exp": <now + 86400>,
  "iat": <now>
}
```
Notable: **24-hour lifetime** (matching the OAuth 1.0a live-session-token TTL), includes the caller's IP address (likely correlated to the registered allowlist if one is set), and the IBKR username as `credential`.

**Endpoints:**
- Token: `POST https://api.ibkr.com/oauth2/api/v1/token`
- SSO session: `POST https://api.ibkr.com/gw/api/v1/sso-sessions`
- SSO validate: `GET https://api.ibkr.com/v1/api/sso/validate`
- Brokerage init (for /iserver endpoints): `POST https://api.ibkr.com/v1/api/iserver/auth/ssodh/init`
- Logout: `POST https://api.ibkr.com/v1/api/logout`

Source: PR #106 description, OAuth 2.0 Authentication Workflow.

### Rotation policy — synthesized

Because IBKR is silent on cadence, real-world ops have to be self-imposed. Synthesized from the available primitives:

1. **There is no IBKR-forced rotation.** A key registered today will keep working until you ask IBKR to remove it or you replace it. Anecdotal: keys registered for OAuth 1.0a in 2018 are still working with no expiry warnings as of 2026 per active maintainers of `ibind` and `ibauth`.
2. **Overlap rotation is the supported pattern.** Because IBKR accepts multiple public keys per `client_id` (each with its own `kid`), the zero-downtime rotation flow is:
   - Generate new RSA keypair.
   - Open Message Center ticket: "Please register additional public key for OAuth client `<client_id>` with kid `<new_kid>`."
   - Wait for IBKR approval (1–3 business days; allow a week to be safe).
   - Deploy new private key + new `kid` to the bot. Both old and new key produce valid JWTs.
   - Run with new key for ≥ 24 h to confirm stability.
   - Open second Message Center ticket: "Please remove public key `<old_kid>` for OAuth client `<client_id>`."
   - IBKR removes; old key is now invalid.
3. **Compromise revocation.** No documented fast-revoke flow. The Message Center is the only channel. Realistic recovery time on a compromised key is **business-hours-bound** (call API Solutions at api-solutions@interactivebrokers.com, then open a ticket; expect same-day during US business hours, next-business-day otherwise). [unconfirmed — no SLA published] Practical mitigation: keep the IBKR account whitelisted to a fixed egress IP using the OAuth client's IP allowlist (the `ip` JWT claim in Shape 2 hints this is enforced), so even a stolen private key can't be used from arbitrary infrastructure.
4. **Self-imposed cadence recommendation for CALYPSO.** Given that the bot will be running 24×5 against real money, a **12-month rotation** is the conservative default and aligns with PCI-DSS / SOC-2 norms for asymmetric signing keys. A **6-month rotation** is reasonable if the bot's deploy infra automates the Message Center ticket flow. Either fits comfortably inside the overlap-rotation primitive IBKR provides.

### What we still don't know

- **Maximum number of registered public keys per `client_id`.** Not documented; the plural form on IBKR Campus implies ≥ 2 but no published ceiling. [unconfirmed]
- **Key-revocation SLA.** Not published. [unconfirmed]
- **JWKS / programmatic key registration.** No public JWKS endpoint and no API for key upload as of 2026-05-13. All key management is via Message Center tickets. [unconfirmed — could change if IBKR exposes a "Connected Apps" portal in future]
- **Whether `is_sufficient` / scope downgrade affects key validity.** Not documented.
- **ECDSA support.** Not mentioned anywhere — assume RSA-only.

### Sources for Q2

- IBKR Campus OAuth 2.0 reference (blocked from scraping but referenced in search snippets): https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-oauth-2-0/
- IBKR Trading Web API (states "private_key_jwt" + RFC 7521 / 7523): https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/
- IBKR Account Management Web API: https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-account-management/
- IBKR OAuth 1.0a spec PDF (May 16, 2018, still canonical for key-format / RSA-SHA256 norms): https://www.interactivebrokers.com/webtradingapi/oauth.pdf
- IBKR OAuth 1.0a guide (Org Portal — Self-Service Portal screenshots): https://www.ibkrguides.com/orgportal/performanceandstatements/open-authorization.htm
- `Voyz/ibind` issue #102 (community confirmation of OAuth 2.0 endpoint): https://github.com/Voyz/ibind/issues/102
- `Voyz/ibind` PR #106 (working OAuth 2.0 implementation, JWT claims verbatim): https://github.com/Voyz/ibind/pull/106 — file `ibind/oauth/oauth2.py` and `examples/rest_09_oauth2.py`
- `datawookie/ibauth` PyPI page (3072-bit RSA recommendation, Message Center registration walkthrough): https://pypi.org/project/ibauth/0.0.11/
- `datawookie/ibauth` README on GitHub (key format, configuration): https://github.com/datawookie/ibauth — `README.md` (commit on master 2026-03-17)
- `art1c0/ibkr-client` (status as of 2026: "OAuth2.0 should be available later"): https://github.com/art1c0/ibkr-client

---

## Question 3 — Cboe Streaming Market Indexes subscription, monthly fee (May 2026)

### Short answer

For a **non-professional IBKR Pro retail account** the relevant subscription is named **"CBOE Streaming Market Indexes"** in the IBKR Client Portal Market Data Subscriptions page (sometimes shown as "Cboe Indices Streaming Service" — same SKU). It delivers Level 1 streaming index values for the **VIX** specifically. The exact monthly fee surfaced in 2026 third-party documentation is **USD 1.50 / month non-professional** [unconfirmed against IBKR's live pricing page — see "uncertainty" notes below].

**SPX live values are NOT in that subscription.** SPX (and NDX) live index values come from a *separate* subscription on IBKR named **"CME S&P Indexes"** (Level 1 for SPX and NDX). Price for that one on the non-pro retail Pro plan is **not separately disclosed in the public docs scrapeable from automated fetch** — but search snippets consistently describe it as "$1–$5/month non-pro" with the exact rate hidden behind IBKR's logged-in Subscription Center. [unconfirmed; ~$1.50 is the most common figure quoted in third-party guides but not verifiable against IBKR's live pricing table as of 2026-05-13.]

### Critical clarification — VIX vs SPX are on different SKUs

This is the single most-confused point in IBKR's data catalog and worth flagging up-front for the migration plan:

| Index | IBKR subscription delivering live L1 | Source |
|---|---|---|
| **VIX** | "CBOE Streaming Market Indexes" | QuantConnect docs: https://www.quantconnect.com/docs/v2/cloud-platform/datasets/interactive-brokers — "CBOE Streaming Market Indexes (L1 for VIX Index)" |
| **SPX, NDX** | "CME S&P Indexes (Level 1)" | Same QuantConnect doc + Optrabot KB at https://app.loopedin.io/optrabot/kb/brokerage/market-data-subscriptions |
| **DJX (Dow Jones Indices)** | Included only in the *Professional* tier of US Securities Snapshot Bundle, not the non-pro bundle | https://www.interactivebrokers.com/en/pricing/market-data-pricing.php (search snippet) |
| **RUT (Russell)** | Cboe Global Indices Feed channel, requires the FTSE Russell channel of the CGI feed — IBKR resells it under index data; separate SKU. [unconfirmed which exact IBKR SKU] |

The CALYPSO bot needs **VIX live** *and* **SPX live**. That means **two subscriptions** on IBKR, not one — meaningful for the migration cost model.

### Exact figures we could confirm vs. couldn't

| Item | Figure | Confidence | Source |
|---|---|---|---|
| CBOE Streaming Market Indexes — non-pro monthly | **USD 1.50 / month** | **[unconfirmed]** — appears in 2026 third-party search snippets but IBKR's pricing page is blocked from automated fetch; this is consistent with the "typically $1.50/month for similar low-tier index SKUs" pattern shown for OPRA Top of Book ($1.50/mo non-pro, confirmed) | https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026 + general search snippets (2026-04 dated) |
| OPRA Top of Book — non-pro monthly | **USD 1.50 / month** | **Confirmed for 2026-04** | https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026 |
| US Securities Snapshot and Futures Value Bundle — non-pro monthly | **USD 10.00 / month** | **Confirmed for 2026-04** (waived at USD 30/month commissions) | same supa.is article + multiple search snippets |
| US Equity and Options Add-On Streaming Bundle — non-pro monthly | **USD 4.50 / month** | **Confirmed for 2026-04** | same supa.is article |
| CME L1 generic (per exchange) — non-pro | **USD 3.00 / month per exchange** or **USD 9.00 / month for all four** (CME, CBOT, COMEX, NYMEX) | **Confirmed (Insignia 2026 fee schedule)** | https://insigniafutures.com/cme-data-fees/ |
| CME S&P Indexes (specifically the SPX/NDX index L1) — non-pro | **[unconfirmed]** — likely USD 1–3 / month based on the CME L1 reference rate; IBKR Subscription Center is the authoritative source and is login-gated | https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/ + Optrabot KB |
| CME Group professional rate (per exchange) | **USD 140.00 / month** | Search-snippet confirmed for 2026 | search results citing 2026 fee list |

### What "CBOE Streaming Market Indexes" actually includes

From the Cboe-side documentation at https://www.cboe.com/us/indices/accessing-index-data/ — the underlying feed (formerly named "CSMI", since renamed "Cboe Global Indices Feed") delivers real-time values for **SPX, VIX, BXM**, plus indices from S&P Dow Jones, Morningstar, FTSE Russell, MSCI, and CoinRoutes RealPrice. Cboe sells access in *channels* (Main / SPX+VIX, CGI, MSTAR, FTSE Russell, CCCY, MSCI, INAV).

**IBKR licenses only a subset of those channels under the SKU it calls "CBOE Streaming Market Indexes"** — specifically the channel that delivers VIX (and only VIX, per QuantConnect's docs which were updated specifically to call out that SPX/NDX is NOT in this SKU at IBKR). SPX live comes through the *separate* CME-side data path on IBKR. This is non-obvious because Cboe's marketing groups SPX + VIX into the same headline feed, but IBKR's redistribution license splits them.

### Bundles and waivers

- **US Securities Snapshot and Futures Value Bundle (USD 10/month non-pro):** Includes top-of-book *snapshot* quotes for CBOE Market Data Express Indices and Dow Jones Indices (non-pro version) — *but these are snapshots, not streaming*. For a 0DTE bot that polls or streams, snapshots are not adequate.
- **Commission waiver.** The Snapshot/Futures Bundle is waived if the account generates ≥ USD 30 / month in commissions. The CBOE Streaming Market Indexes SKU is **not** part of any bundle that supports commission waiver as far as the searchable documentation shows. [unconfirmed for the exact CBOE Indices SKU — but in general, individual index-data SKUs at IBKR are *not* commission-waivable, only the broad bundles are.]
- **Free Cboe One feed:** IBKR clients get free real-time streaming Cboe One + IEX equity quotes — but this is *non-consolidated equity data*, not index data. Source: https://www.interactivebrokers.com/en/pricing/market-data-pricing.php (search snippet).

### Cheapest path to live SPX + VIX for the bot

Given the SKU split:

| Path | Components | Approx monthly cost non-pro | Suitable for 0DTE? |
|---|---|---|---|
| **Subscription path (live streaming)** | CBOE Streaming Market Indexes ($1.50 [unconfirmed]) + CME S&P Indexes ($1.50–3.00 [unconfirmed]) | **~$3–5 / month** | Yes — this is what production needs |
| Snapshot bundle path | US Securities Snapshot and Futures Value Bundle ($10/month, waived ≥ $30 commission) | $0–10 / month | **No — snapshots only, latency-bound for 0DTE entries** |
| OPRA-only path | OPRA Top of Book ($1.50/month) for option chain quotes; derive an implied SPX from front-month options | $1.50 / month | **No — derives index from options, circular dependency** |
| QuantConnect-managed path | Subscribe through QC's "QuantConnect Cloud" data plan, bypass IBKR's market-data fees entirely; bot still trades through IBKR | $0 to IBKR; QC subscription separate | Only if running CALYPSO on QC; not applicable here |

The migration plan should budget **USD 3–5 / month** for live SPX + VIX market data on IBKR, then resolve the exact figures by checking the live IBKR Subscription Center after the account is funded (the prices in the Client Portal are the authoritative source and are not exposed in IBKR's public web pricing pages without login). Worst plausible case: **USD 6.50 / month** if the CME S&P Indexes SKU is the full CME L1 generic rate of $3 + the CBOE Indices SKU is $1.50 + a $1–2 redistribution margin IBKR adds.

### Pro vs non-pro

The professional rate multiplier at IBKR for index data SKUs is documented (in search snippets sourced from 2026 third-party guides) as **~10× the non-pro rate or higher**. The professional CME rate (CME Group's standard) is USD 140 / month per exchange, vs USD 3 / month non-pro — so 47× in that specific case. For CBOE Streaming Market Indexes the pro rate is not separately documented but conservatively assume **USD 15–30 / month** range. [unconfirmed — IBKR's Subscription Center is the authoritative source.]

For CALYPSO purposes the user qualifies as non-pro (individual, not registered with a regulatory body, not in employed financial-institution capacity) — so pro pricing is informational only.

### Account-equity gate

IBKR requires accounts to maintain **USD 500** equity above the total monthly market-data cost. For ~$5/month of market data subscriptions, this is not a binding constraint at expected CALYPSO account sizes.

### Sources for Q3

- IBKR Market Data Pricing (US, the authoritative page — blocked from automated fetch but referenced in every search): https://www.interactivebrokers.com/en/pricing/market-data-pricing.php
- IBKR Market Data Subscriptions (Campus): https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/
- IBKR Subscription Considerations KB (redirects to FAQ): https://www.ibkrguides.com/kb/en-us/subscription-consideration-us-market-data.htm
- IBKR Subscribe to Market Data (Client Portal docs): https://www.ibkrguides.com/clientportal/usersettings/marketdatasubscriptions.htm
- IBKR Subscribing to Data lesson (Campus): https://www.interactivebrokers.com/campus/trading-lessons/subscribing-to-data/
- IBKR US Securities Snapshot Bundle composition (search snippet): https://www.interactivebrokers.com/en/pricing/market-data-pricing.php
- IBKR Cboe SPX page (lists data sources): https://www.interactivebrokers.com/en/trading/cboe.php
- Cboe Global Indices Feed product page (channel structure): https://www.cboe.com/us/indices/accessing-index-data/
- Cboe One Feed product page (the *free* equity feed): https://www.cboe.com/market_data_services/us/equities/cboe_one/
- QuantConnect IBKR docs (key SKU breakdown for SPX/NDX vs VIX): https://www.quantconnect.com/docs/v2/cloud-platform/datasets/interactive-brokers
- QuantConnect SPX Options forum thread (subscription requirement confirmation): https://www.quantconnect.com/forum/discussion/19144/spx-options-error-in-live-ibkr/
- supa.is 2026 IBKR market data guide (most-recent third-party with prices): https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026 (article dated 2026-03-06, prices marked "as of 2026-04")
- Optrabot Market Data Subscriptions KB: https://app.loopedin.io/optrabot/kb/brokerage/market-data-subscriptions
- Insignia Futures CME data fees (2026 reference rate): https://insigniafutures.com/cme-data-fees/
- CME Group 2025 Market Data Fee List PDF: https://www.cmegroup.com/market-data/files/january-2025-market-data-fee-list.pdf

---

## Action items for the migration plan

1. **OAuth 2.0 key registration.** Open IBKR Message Center ticket as the first step after account funding. Generate a 3072-bit RSA keypair, register the public PEM, capture the `client_id` + `client_key_id` issued. Plan a second keypair for paper/QA. Allow 3–5 business days slack in the migration timeline for the manual approval round-trip.
2. **Codify the JWT shapes** from `ibind` PR #106 in CALYPSO directly rather than depending on the unmerged PR. Two shapes: 60-sec `/token` JWT, 24-hour `/sso-sessions` JWT. Algorithm `RS256`. `kid` is the rotation primitive.
3. **Self-impose 12-month rotation** with the overlap procedure documented above. Add it to the migration runbook with the Message Center ticket templates pre-drafted.
4. **Budget USD 5 / month** for live SPX + VIX market data (`CBOE Streaming Market Indexes` + `CME S&P Indexes`) under non-pro Pro pricing. Verify exact figures inside the Client Portal Subscription Center on day-1 of account access; update the cost model with the actual figure.
5. **Do NOT rely on the US Securities Snapshot Bundle** for live index data — it's snapshot-only and unsuitable for a 0DTE entry decision. The commission-waiver path only works on the Snapshot Bundle and won't waive the CBOE Streaming Market Indexes / CME S&P Indexes SKUs the bot actually needs.
6. **Confirm "Non-Professional" status** is correctly set in Client Portal → Settings → Market Data Subscriptions → Subscriber Status before subscribing. Wrong status here causes a 10–50× price difference and is a common own-goal.
7. **Open follow-up question for IBKR API Solutions** (api-solutions@interactivebrokers.com), to be sent the day the account is opened, asking two specific things this research could not resolve from public sources:
   a. Maximum number of concurrently-registered public keys per OAuth 2.0 `client_id`.
   b. Fast-revoke SLA on a compromised public key — i.e., is there a same-day disable path or only the standard Message Center ticket?
