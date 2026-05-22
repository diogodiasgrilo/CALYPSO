# IBKR OAuth 1.0a credentials — VM setup (P7 Step 4, option B)

HYDRA authenticates to Interactive Brokers with OAuth 1.0a. The six
credentials are delivered to the bot by **systemd encrypted credentials**
(`LoadCredentialEncrypted=` in `deploy/hydra.service`) — they are never
process environment variables, never inherited by child processes,
tmpfs-backed at runtime, and encrypted at rest.

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
sudo install -d -m 0700 /etc/calypso/ibkr

# 2. Encrypt each credential. The --name MUST match the credential ID in
#    hydra.service exactly (systemd binds the ciphertext to that name).

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

# 4. Install the service and start.
sudo cp /opt/calypso/deploy/hydra.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra
sudo journalctl -u hydra -f
```

## How the bot reads them

`shared/ib_oauth.load_credentials("paper")` checks for
`$CREDENTIALS_DIRECTORY` (set by systemd whenever `LoadCredential*=` is
used). When present it reads all six credentials from files there, named
by the IDs in `_SYSTEMD_CRED_NAMES`. With no `$CREDENTIALS_DIRECTORY`
(dev laptop) it falls back to env vars + `$CALYPSO_IBKR_KEYS_DIR` —
unchanged.

## Rotation

Re-encrypt the changed credential (step 2) and `sudo systemctl restart hydra`.

## Notes

- `systemd-creds encrypt` keys the ciphertext to this host (host key, or
  TPM2 if present). The `.cred` files are **not** portable to another VM —
  re-encrypt on each host.
- The old Saxo `token_keeper` service is **not** needed: OAuth 1.0a is
  unattended (the live session token rotates cryptographically; the
  morning re-auth gate handles the daily reset — see
  `docs/migration/P7_GO_LIVE_PLAN.md`).
