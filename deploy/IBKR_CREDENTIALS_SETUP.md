# IBKR OAuth 1.0a credentials — VM setup (P7 Step 4, option B)

> ## 🛑 THIS PAGE IS THE **PAPER** PROCEDURE. For LIVE money, jump to [Live-money credentials](#live-money-credentials).
>
> Everything below — "use the paper-account keypair", `/etc/calypso/ibkr/`,
> `calypso-broker.service` — is the paper setup. `LIVE_READINESS_CHECKLIST.md`
> sends you here for the **live** credentials, and following these steps
> literally with a live keypair would run
> `systemd-creds encrypt ... /etc/calypso/ibkr/<name>.cred` — **overwriting the
> paper credentials that variant B is trading on right now.** The broker would
> then authenticate to the wrong account, `_assert_account_matches_env` would
> refuse to serve the session, and the live paper seat would go down.
>
> Live money is a **second** broker with its **own** keypair, its **own**
> directory and its **own** unit. Nothing about paper is touched.
> (Gap found 2026-09-19 during a pre-cutover re-measurement of the documents
> that get *executed*, not read.)

**`calypso-broker`** authenticates to Interactive Brokers with OAuth 1.0a.
Under the shared-session architecture (see
`docs/migration/BROKER_SESSION_SERVICE_DESIGN.md`) **only** `calypso-broker`
owns the single IBKR session and holds these credentials; the HYDRA strategy
units (A/B/C) proxy all brokerage calls through the broker over loopback
(`CALYPSO_BROKER_URL`) and do not authenticate to IBKR themselves.

The six credentials are delivered to the broker by **systemd encrypted
credentials** (`LoadCredentialEncrypted=` in `deploy/calypso-broker.service`)
— they are never process environment variables, never inherited by child
processes, tmpfs-backed at runtime, and encrypted at rest.

> Cutover note: `deploy/hydra.service` still carries the same
> `LoadCredentialEncrypted=` entries today, but in broker-proxy mode
> (`CALYPSO_BROKER_URL` set, which is the tracked default) they are redundant
> fallback only — the broker is the live OAuth identity. They will be removed
> from the hydra units in a follow-up cleanup once the cutover is confirmed on
> the VM.

## The six credentials

From the IBKR OAuth self-service registration (see
`docs/migration/IB_OPEN_QUESTIONS_ANSWERED.md` §Q1):

| # | What | Form |
|---|------|------|
| 1 | consumer key | 9-char A–Z string |
| 2 | access token | string |
| 3 | access-token-secret | string |
| 4 | private signature key | PEM file |
| 5 | private encryption key | PEM file |
| 6 | Diffie-Hellman params | PEM file |

**Use the paper-account keypair** — IBKR requires distinct keypairs for
paper and live.

## One-time setup on the VM

Requires systemd ≥ 250 (`systemd-creds` — present on the GCE Debian image).
Run as root on `calypso-bot`.

```bash
# 1. Create the credentials directory (root-only).
#    `install -d -m 0700` creates with mode 700 AND root ownership in one
#    step; verify with `stat -c '%a %U:%G' /etc/calypso/ibkr` (expect 700
#    root:root). The .cred files inherit root ownership and 0600 mode
#    from systemd-creds encrypt.
sudo install -d -m 0700 /etc/calypso/ibkr

# 2. Encrypt each credential. The --name MUST match the credential ID in
#    calypso-broker.service exactly (systemd binds the ciphertext to that
#    name; the same IDs also appear in the hydra* fallback units).

#    String secrets — pipe the raw value (no trailing newline: `echo -n`):
echo -n 'YOURCONSUMERKEY' | sudo systemd-creds encrypt --name=ibkr_consumer_key - /etc/calypso/ibkr/consumer_key.cred
echo -n 'YOUR_ACCESS_TOKEN' | sudo systemd-creds encrypt --name=ibkr_access_token - /etc/calypso/ibkr/access_token.cred
echo -n 'YOUR_ACCESS_TOKEN_SECRET' | sudo systemd-creds encrypt --name=ibkr_access_token_secret - /etc/calypso/ibkr/access_token_secret.cred

#    PEM files — encrypt the file directly:
sudo systemd-creds encrypt --name=ibkr_signature_pem  private_signature.pem  /etc/calypso/ibkr/signature.pem.cred
sudo systemd-creds encrypt --name=ibkr_encryption_pem private_encryption.pem /etc/calypso/ibkr/encryption.pem.cred
sudo systemd-creds encrypt --name=ibkr_dhparam_pem    dhparam.pem            /etc/calypso/ibkr/dhparam.pem.cred

# 3. Shred the plaintext PEM files once encrypted.
shred -u private_signature.pem private_encryption.pem dhparam.pem

# 4. Install the services. DO NOT enable yet — verify first (next section).
#    calypso-broker.service is the credential-bearing unit (it authenticates
#    to IBKR); the hydra* strategy units proxy through it.
sudo cp /opt/calypso/deploy/calypso-broker.service /etc/systemd/system/
sudo cp /opt/calypso/deploy/hydra.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Pre-start verification (DO THIS BEFORE `systemctl enable`)

P7-audit M4: verify the encrypt → decrypt → unit-load round-trip
BEFORE relying on the service to do it for you. A bad encrypt step
or a typo in `LoadCredentialEncrypted=` will otherwise surface only
at start time, possibly during market hours.

```bash
# 1. Validate the service unit syntactically. `systemd-analyze verify`
#    catches typos in LoadCredentialEncrypted= names BEFORE the unit
#    ever tries to start. Exit code 0 = clean. Verify the broker (the unit
#    that actually loads these creds and authenticates to IBKR); also verify
#    hydra.service since it still declares the same fallback creds.
sudo systemd-analyze verify /etc/systemd/system/calypso-broker.service
sudo systemd-analyze verify /etc/systemd/system/hydra.service

# 2. Decrypt each .cred file back to plaintext and check byte length.
#    Sanity: did we lose bytes during the encrypt step?
#    Plaintext is only printed to stdout / piped to wc; nothing lands
#    on disk.
for f in /etc/calypso/ibkr/*.cred; do
    n=$(sudo systemd-creds decrypt "$f" - | wc -c)
    printf '%-60s %s bytes\n' "$f" "$n"
done
# Expected (paper):
#   consumer_key.cred  9 bytes  (IBKR 9-char A-Z key)
#   access_token.cred  ~32 bytes
#   access_token_secret.cred  ~32 bytes
#   signature.pem.cred  ~1700 bytes (RSA 2048)
#   encryption.pem.cred ~1700 bytes
#   dhparam.pem.cred   ~400-500 bytes
# A `0 bytes` line means the encrypt step ingested nothing — re-run it.

# 3. Spot-check the consumer key matches what 1Password has.
sudo systemd-creds decrypt /etc/calypso/ibkr/consumer_key.cred -
# Expected: prints exactly your consumer key, no trailing newline.

# 4. Once steps 1-3 are clean, enable + start the broker FIRST (it owns the
#    single IBKR session), confirm it authenticated, then start the strategies
#    (they Want/After calypso-broker, so they wait for it).
sudo systemctl enable --now calypso-broker
sudo journalctl -u calypso-broker -f   # wait for /health → authenticated
# Then bring up the strategy units (proxy through the broker):
sudo systemctl enable --now hydra
sudo journalctl -u hydra -f
```

If any of steps 1-3 fail, **do not** enable the service. Re-run the
failing encrypt step (verifying the source value first) or fix the
typo in `calypso-broker.service`.


---

## Live-money credentials

**Prerequisite:** the account is funded and permissioned, and IBKR has issued a
**NEW live-OAuth keypair**. IBKR requires distinct keypairs per environment — the
paper keypair does not work live, and reusing it is not an option. Activation
takes roughly two weeks, so everything here happens *after* a long wait, which is
exactly why it is written down rather than improvised.

**The one rule: never write into `/etc/calypso/ibkr/`.** That directory holds the
paper credentials `calypso-broker` is using to trade B right now. Live money gets
`/etc/calypso/ibkr-live/` — a SIBLING, never a subdirectory, so that a rotation or
cleanup aimed at paper cannot reach real-money keys.

```bash
# 1. Live credential directory (root-only, SEPARATE from paper's).
sudo install -d -m 0700 /etc/calypso/ibkr-live
stat -c '%a %U:%G' /etc/calypso/ibkr-live      # expect: 700 root:root

# 2. Encrypt the SIX LIVE credentials. The --name IDs are the SAME as paper's
#    (they are the credential IDs systemd binds the ciphertext to, and both
#    units use the same six) — only the DESTINATION PATH differs. Read that
#    twice: the names matching is correct, the paths matching would be the bug.
echo -n 'LIVECONSUMERKEY' | sudo systemd-creds encrypt --name=ibkr_consumer_key - /etc/calypso/ibkr-live/consumer_key.cred
echo -n 'LIVE_ACCESS_TOKEN' | sudo systemd-creds encrypt --name=ibkr_access_token - /etc/calypso/ibkr-live/access_token.cred
echo -n 'LIVE_ACCESS_TOKEN_SECRET' | sudo systemd-creds encrypt --name=ibkr_access_token_secret - /etc/calypso/ibkr-live/access_token_secret.cred
sudo systemd-creds encrypt --name=ibkr_signature_pem  live_signature.pem  /etc/calypso/ibkr-live/signature.pem.cred
sudo systemd-creds encrypt --name=ibkr_encryption_pem live_encryption.pem /etc/calypso/ibkr-live/encryption.pem.cred
sudo systemd-creds encrypt --name=ibkr_dhparam_pem    live_dhparam.pem    /etc/calypso/ibkr-live/dhparam.pem.cred

# 3. Shred the plaintext.
shred -u live_signature.pem live_encryption.pem live_dhparam.pem

# 4. Confirm PAPER is untouched — six files, and mtimes from the original setup.
ls -la --time-style=full-iso /etc/calypso/ibkr/*.cred

# 5. Install the live broker unit. It already carries CALYPSO_IBKR_ENV=live,
#    binds :8789, and points at /etc/calypso/ibkr-live/. DO NOT enable yet.
sudo cp /opt/calypso/deploy/calypso-broker-live.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Pre-start verification (live) — the same three checks, against the live paths

```bash
# 1. Unit syntax, including the LoadCredentialEncrypted= names.
sudo systemd-analyze verify /etc/systemd/system/calypso-broker-live.service

# 2. Decrypt round-trip + byte length, over the LIVE directory only.
for f in /etc/calypso/ibkr-live/*.cred; do
    n=$(sudo systemd-creds decrypt "$f" - | wc -c)
    printf '%-60s %s bytes\n' "$f" "$n"
done
# Same expected ranges as paper: consumer_key 9, tokens ~32,
# signature/encryption PEM ~1700, dhparam ~400-500. A `0 bytes` line means the
# encrypt step ingested nothing — re-run it.

# 3. Spot-check the consumer key against the LIVE entry in 1Password (NOT the
#    paper one — they are different keys and confusing them is the whole risk).
sudo systemd-creds decrypt /etc/calypso/ibkr-live/consumer_key.cred -
```

### First start — broker alone, no strategy pointed at it

```bash
# 4. Start the live broker with NOTHING trading against it, and confirm WHO it
#    reached before any strategy can act on it.
sudo systemctl enable --now calypso-broker-live
curl -s http://127.0.0.1:8789/health
# REQUIRED: {"environment":"live","account":"<code>",...,"authenticated":true}
#   * environment MUST read "live" — "paper" means CALYPSO_IBKR_ENV did not take.
#   * the account code must NOT start with "D" — paper accounts are DU…/DUR…
#     (this account is DUR049068). A D-code here means live credentials were
#     encrypted from the paper keypair.
# STOP on either. Do not start bm.

# 5. Confirm PAPER is still healthy and independent — two brokers now run at
#    once, which is only safe because they are different IBKR USERNAMES.
curl -s http://127.0.0.1:8788/health
# expect: {"environment":"paper","account":"DUR049068",...,"competing":false}

# 6. Only now: the real-money strategy, at ONE contract (Gate 8).
sudo cp /opt/calypso/deploy/hydra_variant_bm.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now hydra_variant_bm
sudo journalctl -u hydra_variant_bm -f
# expect: ACCOUNT-ASSERT OK: variant BM declares 'live_money'; broker reports 'live_money'
# A mismatch REFUSES to start — that is S2 working, not a fault to work around.
```

**Rollback:** `sudo systemctl stop hydra_variant_bm`. Paper is untouched at every
step above; stopping bm stops real-money trading and nothing else.

**Live rotation:** re-encrypt into `/etc/calypso/ibkr-live/` and
`sudo systemctl restart calypso-broker-live`. Restarting the *paper* broker does
nothing for live, and vice versa — they are separate sessions on separate
usernames.

## How the bot reads them

`services/broker/main.py` calls `shared/ib_oauth.load_credentials(resolve_environment())` — **not** a hardcoded
`"paper"`, which is what this line said until 2026-09-19. `resolve_environment()`
reads `$CALYPSO_IBKR_ENV` and defaults to `paper`; anything not in
`VALID_ENVIRONMENTS` raises rather than falling back, so a typo can never
silently select the wrong account in either direction. The env var is set to
`live` inside `calypso-broker-live.service` and left unset (= paper) for
`calypso-broker.service`. The loader then checks for `$CREDENTIALS_DIRECTORY` (set by systemd whenever
`LoadCredential*=` is used). When present it reads all six credentials from
files there, named by the IDs in `_SYSTEMD_CRED_NAMES`. With no
`$CREDENTIALS_DIRECTORY` (dev laptop) it falls back to env vars +
`$CALYPSO_IBKR_KEYS_DIR` — unchanged. (In broker-proxy mode the HYDRA
strategy units never call this; they reach the broker over loopback.)

## Rotation

**Paper:** re-encrypt the changed credential into `/etc/calypso/ibkr/` (step 2) and
`sudo systemctl restart calypso-broker` (the strategies keep their loopback connection
and reconnect automatically).

**Live money:** re-encrypt into `/etc/calypso/ibkr-live/` and restart
`calypso-broker-live` — see [Live-money credentials](#live-money-credentials). The two
are separate sessions on separate IBKR usernames: restarting one does nothing for the
other, and a rotation aimed at paper must never touch `/etc/calypso/ibkr-live/`.

## Notes

- `systemd-creds encrypt` keys the ciphertext to this host (host key, or
  TPM2 if present). The `.cred` files are **not** portable to another VM —
  re-encrypt on each host.
- The old Saxo `token_keeper` service is **not** needed: OAuth 1.0a is
  unattended (the live session token rotates cryptographically; the
  morning re-auth gate inside `calypso-broker` handles the daily reset —
  see `docs/migration/BROKER_SESSION_SERVICE_DESIGN.md` and
  `docs/migration/archive/P7_GO_LIVE_PLAN.md` — archived, superseded by
  Gates 1-5 in `docs/migration/PROJECT_STATUS.md`).
