# IBKR OAuth 1.0a credentials — VM setup (P7 Step 4, option B)

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

## How the bot reads them

`services/broker/main.py` calls `shared/ib_oauth.load_credentials("paper")`,
which checks for `$CREDENTIALS_DIRECTORY` (set by systemd whenever
`LoadCredential*=` is used). When present it reads all six credentials from
files there, named by the IDs in `_SYSTEMD_CRED_NAMES`. With no
`$CREDENTIALS_DIRECTORY` (dev laptop) it falls back to env vars +
`$CALYPSO_IBKR_KEYS_DIR` — unchanged. (In broker-proxy mode the HYDRA
strategy units never call this; they reach the broker over loopback.)

## Rotation

Re-encrypt the changed credential (step 2) and `sudo systemctl restart calypso-broker`
(the strategies keep their loopback connection and reconnect automatically).

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
