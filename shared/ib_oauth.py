"""IBKR OAuth 1.0a config loader for the CALYPSO migration.

Builds an `ibind.oauth.oauth1a.OAuth1aConfig` from credentials stored across:
  - 1Password (now) / GCP Secret Manager (Phase B): access token + access token
    secret + consumer key (the three secret values IBKR issued at registration)
  - Disk at $CALYPSO_IBKR_KEYS_DIR/{paper,live}/: the three crypto files
    (private_signature.pem, private_encryption.pem, dhparam.pem)

This module is deliberately small and dependency-light — it does NOT import
ibkr/HYDRA strategy code, so unit tests stay fast.

See docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md §A.0-A.2 for the broader
migration plan and the rationale for why OAuth 1.0a (not 2.0) is our path.

Security context:
  - All `private_*.pem` files MUST live in mode-600 directories.
  - `dh_prime` extracted from dhparam.pem is a hex string used by ibind
    for the Diffie-Hellman live-session-token rotation.
  - This module never logs secrets. Caller is responsible for keeping
    `access_token` / `access_token_secret` out of structured logs.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ibind.oauth.oauth1a import OAuth1aConfig  # module-level so tests can patch cleanly


# Path where the OAuth crypto files live. Override via env var if needed.
DEFAULT_KEYS_BASE = Path(os.environ.get(
    "CALYPSO_IBKR_KEYS_DIR",
    str(Path.home() / "ibkr-oauth"),
))


@dataclass(frozen=True)
class IBKRCredentials:
    """Loaded-but-not-yet-validated IBKR OAuth 1.0a credential bundle.

    Constructed via `load_credentials()` from environment vars (dev/laptop) or
    `load_credentials_from_secret_manager()` (production VM, Phase B).

    Fields:
      environment: 'paper' or 'live'
      consumer_key: 9-character A-Z label registered at IBKR portal
      access_token, access_token_secret: returned by IBKR at "Generate Token"
      private_signature_path, private_encryption_path: PEM files on disk
      dh_param_path: PEM file on disk; we extract the hex prime via openssl
    """
    environment: str
    consumer_key: str
    access_token: str
    access_token_secret: str
    private_signature_path: Path
    private_encryption_path: Path
    dh_param_path: Path

    def validate_paths(self) -> None:
        """Raise FileNotFoundError if any expected file is missing."""
        for label, path in (
            ("private signature", self.private_signature_path),
            ("private encryption", self.private_encryption_path),
            ("dh params", self.dh_param_path),
        ):
            if not path.exists():
                raise FileNotFoundError(
                    f"IBKR OAuth {self.environment} {label} missing: {path}"
                )

    def validate_secrets(self) -> None:
        """Raise ValueError if any required secret is empty or obviously bad."""
        if not self.consumer_key or len(self.consumer_key) > 9:
            raise ValueError(
                f"consumer_key must be 1-9 chars A-Z; got {self.consumer_key!r}"
            )
        if not self.consumer_key.isalpha() or not self.consumer_key.isupper():
            raise ValueError(
                f"consumer_key must be uppercase A-Z only; got {self.consumer_key!r}"
            )
        if not self.access_token or not self.access_token_secret:
            raise ValueError(
                f"access_token / access_token_secret must be non-empty"
            )


def extract_dh_prime(dhparam_path: Path) -> str:
    """Extract the Diffie-Hellman prime as a hex string from a dhparam PEM.

    Uses `openssl dhparam -in <path> -text` and pulls out the prime/P value.
    Pattern from ibind wiki: https://github.com/Voyz/ibind/wiki/OAuth-1.0a.
    Returns lowercase hex without separators.

    For a 2048-bit DH params file, the prime is 256 bytes / 512-514 hex chars.
    """
    if not dhparam_path.exists():
        raise FileNotFoundError(f"DH params file missing: {dhparam_path}")
    proc = subprocess.run(
        ["openssl", "dhparam", "-in", str(dhparam_path), "-text"],
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(
        r"(?:prime|P):\s*((?:\s*[0-9a-fA-F:]+\s*)+)",
        proc.stdout,
    )
    if not match:
        raise ValueError(
            f"No prime found in dhparam output for {dhparam_path}"
        )
    return re.sub(r"[\s:]", "", match.group(1))


def assert_safe_crypto_backend() -> None:
    """Fail fast if our crypto backend is the abandoned pyCrypto, not pycryptodome.

    ibind's `[oauth]` extra explicitly requires pycryptodome>=3.21, which
    provides the same `Crypto.*` import namespace as pycrypto for backwards
    compatibility. We verify via version number — pycrypto's last release was
    2.6.1 (2014); pycrypto never reached 3.x.

    Called once at IBClient.connect() to make accidental pycrypto installs
    impossible to deploy unnoticed.

    Raises:
        RuntimeError: if Crypto.__version__ doesn't start with '3.'
    """
    import Crypto
    ver = getattr(Crypto, "__version__", "0.0.0")
    if not ver.startswith("3."):
        raise RuntimeError(
            f"Unsafe crypto backend detected: Crypto.__version__={ver!r}. "
            f"Expected pycryptodome (3.x+); got pycrypto (≤2.x) or unknown. "
            f"Reinstall with: pip install --force-reinstall pycryptodome>=3.21"
        )


def _keys_dir(environment: str) -> Path:
    """Return the per-environment key directory (paper or live)."""
    if environment not in ("paper", "live"):
        raise ValueError(f"environment must be 'paper' or 'live', got {environment!r}")
    return DEFAULT_KEYS_BASE / environment


def load_credentials(
    environment: str,
    consumer_key: Optional[str] = None,
    access_token: Optional[str] = None,
    access_token_secret: Optional[str] = None,
) -> IBKRCredentials:
    """Load OAuth credentials from explicit args OR environment variables.

    Args take precedence over env vars. Env var names match ibind's expected
    names so callers can use a single shell-env source of truth:

      IBIND_OAUTH1A_CONSUMER_KEY
      IBIND_OAUTH1A_ACCESS_TOKEN
      IBIND_OAUTH1A_ACCESS_TOKEN_SECRET

    The crypto files are read from disk at `$CALYPSO_IBKR_KEYS_DIR/{env}/`
    (default `~/ibkr-oauth/{env}/`).

    Args:
        environment: 'paper' or 'live'
        consumer_key: if None, reads IBIND_OAUTH1A_CONSUMER_KEY env var
        access_token: if None, reads IBIND_OAUTH1A_ACCESS_TOKEN env var
        access_token_secret: if None, reads IBIND_OAUTH1A_ACCESS_TOKEN_SECRET

    Returns:
        IBKRCredentials with paths NOT yet validated. Caller should call
        .validate_paths() + .validate_secrets() if needed.
    """
    keys_dir = _keys_dir(environment)
    return IBKRCredentials(
        environment=environment,
        consumer_key=consumer_key or os.environ.get("IBIND_OAUTH1A_CONSUMER_KEY", ""),
        access_token=access_token or os.environ.get("IBIND_OAUTH1A_ACCESS_TOKEN", ""),
        access_token_secret=access_token_secret or os.environ.get("IBIND_OAUTH1A_ACCESS_TOKEN_SECRET", ""),
        private_signature_path=keys_dir / "private_signature.pem",
        private_encryption_path=keys_dir / "private_encryption.pem",
        dh_param_path=keys_dir / "dhparam.pem",
    )


def build_oauth1a_config(creds: IBKRCredentials, *, init_brokerage_session: bool = True) -> OAuth1aConfig:
    """Build ibind's OAuth1aConfig from validated credentials.

    Args:
        creds: validated IBKRCredentials (caller should have run
               .validate_paths() and .validate_secrets() first)
        init_brokerage_session: when True (default), ibind auto-calls
            /iserver/auth/ssodh/init on connect. Set False for the activation
            poller, which only wants to test the OAuth handshake.

    Returns:
        ibind.oauth.oauth1a.OAuth1aConfig
    """
    creds.validate_paths()
    creds.validate_secrets()
    dh_prime = extract_dh_prime(creds.dh_param_path)

    return OAuth1aConfig(
        access_token=creds.access_token,
        access_token_secret=creds.access_token_secret,
        consumer_key=creds.consumer_key,
        dh_prime=dh_prime,
        encryption_key_fp=str(creds.private_encryption_path),
        signature_key_fp=str(creds.private_signature_path),
        init_brokerage_session=init_brokerage_session,
        maintain_oauth=True,
    )
