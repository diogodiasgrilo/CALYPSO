"""Tests for shared.ib_oauth — DH prime extraction, credential loading, safety assertion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_oauth import (
    DEFAULT_KEYS_BASE,
    IBKRCredentials,
    assert_safe_crypto_backend,
    build_oauth1a_config,
    extract_dh_prime,
    load_credentials,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def real_paper_dhparam():
    """The actual paper dhparam.pem file generated on this laptop during Phase 0.

    Skipped if not present (e.g., on CI where ~/ibkr-oauth/ doesn't exist).
    """
    p = Path.home() / "ibkr-oauth" / "paper" / "dhparam.pem"
    if not p.exists():
        pytest.skip(f"Real paper dhparam.pem not present at {p}")
    return p


@pytest.fixture
def fresh_dhparam(tmp_path):
    """Generate a fresh small 1024-bit DH params file in a tmp dir.

    Used by tests that need a valid file but don't care which one. 1024-bit
    keeps test runtime under a second.
    """
    path = tmp_path / "dhparam.pem"
    subprocess.run(
        ["openssl", "dhparam", "-out", str(path), "1024"],
        check=True,
        capture_output=True,
    )
    return path


# ─── DH prime extraction ────────────────────────────────────────────────────


class TestExtractDHPrime:
    def test_extracts_512_to_516_hex_chars_for_2048bit(self, real_paper_dhparam):
        prime = extract_dh_prime(real_paper_dhparam)
        # 2048 bits / 4 bits per hex char = 512 hex chars. Allow ±2 for
        # leading-zero edge cases (openssl prints leading 00s).
        assert 510 <= len(prime) <= 520, f"unexpected length: {len(prime)}"

    def test_returns_lowercase_hex_only(self, real_paper_dhparam):
        prime = extract_dh_prime(real_paper_dhparam)
        assert prime.islower() or prime.replace("0", "").replace("1", "").replace("2", "").replace("3", "").replace("4", "").replace("5", "").replace("6", "").replace("7", "").replace("8", "").replace("9", "") == ""  # all hex digit chars in lowercase
        assert all(c in "0123456789abcdef" for c in prime), "non-hex chars present"

    def test_no_separators_or_whitespace(self, real_paper_dhparam):
        prime = extract_dh_prime(real_paper_dhparam)
        assert ":" not in prime
        assert " " not in prime
        assert "\n" not in prime

    def test_works_on_fresh_smaller_dhparam(self, fresh_dhparam):
        # 1024-bit DH params → 256 hex chars (give or take leading zeros)
        prime = extract_dh_prime(fresh_dhparam)
        assert 250 <= len(prime) <= 260, f"unexpected length: {len(prime)}"
        assert all(c in "0123456789abcdef" for c in prime)

    def test_missing_file_raises_filenotfound(self, tmp_path):
        missing = tmp_path / "nope.pem"
        with pytest.raises(FileNotFoundError):
            extract_dh_prime(missing)

    def test_invalid_pem_raises_called_process_error(self, tmp_path):
        bad = tmp_path / "bad.pem"
        bad.write_text("not a real DH params file")
        # openssl dhparam will exit non-zero
        with pytest.raises(subprocess.CalledProcessError):
            extract_dh_prime(bad)


# ─── Credential loading ─────────────────────────────────────────────────────


class TestLoadCredentials:
    def test_explicit_args_take_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("IBIND_OAUTH1A_CONSUMER_KEY", "FROMENV")
        creds = load_credentials(
            "paper",
            consumer_key="FROMARG",
            access_token="t",
            access_token_secret="s",
        )
        assert creds.consumer_key == "FROMARG"

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("IBIND_OAUTH1A_CONSUMER_KEY", "CALYPSOPP")
        monkeypatch.setenv("IBIND_OAUTH1A_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("IBIND_OAUTH1A_ACCESS_TOKEN_SECRET", "sec")
        creds = load_credentials("paper")
        assert creds.consumer_key == "CALYPSOPP"
        assert creds.access_token == "tok"
        assert creds.access_token_secret == "sec"

    def test_paper_vs_live_use_separate_directories(self, monkeypatch):
        paper = load_credentials("paper", "K", "t", "s")
        live = load_credentials("live", "K", "t", "s")
        assert "paper" in str(paper.private_signature_path)
        assert "live" in str(live.private_signature_path)
        assert paper.private_signature_path != live.private_signature_path

    def test_invalid_environment_raises(self):
        with pytest.raises(ValueError, match="paper.*live"):
            load_credentials("staging", "K", "t", "s")

    def test_env_var_override_for_keys_dir(self, monkeypatch, tmp_path, reload_ib_oauth):
        custom_dir = tmp_path / "custom"
        monkeypatch.setenv("CALYPSO_IBKR_KEYS_DIR", str(custom_dir))
        ib_oauth = reload_ib_oauth()
        creds = ib_oauth.load_credentials("paper", "K", "t", "s")
        assert str(creds.private_signature_path).startswith(str(custom_dir))


@pytest.fixture
def reload_ib_oauth():
    """Helper to re-import shared.ib_oauth after env vars change.

    DEFAULT_KEYS_BASE is computed at module-import time, so changing
    CALYPSO_IBKR_KEYS_DIR mid-test requires a re-import.
    """
    import importlib

    def _reload():
        import shared.ib_oauth as ib_oauth
        return importlib.reload(ib_oauth)

    return _reload


# ─── Validation ─────────────────────────────────────────────────────────────


class TestValidateSecrets:
    def _base_creds(self, **kw):
        defaults = dict(
            environment="paper",
            consumer_key="CALYPSOPP",
            access_token="x",
            access_token_secret="y",
            private_signature_path=Path("/tmp/a.pem"),
            private_encryption_path=Path("/tmp/b.pem"),
            dh_param_path=Path("/tmp/c.pem"),
        )
        defaults.update(kw)
        return IBKRCredentials(**defaults)

    def test_valid_creds_pass(self):
        self._base_creds().validate_secrets()  # no raise

    def test_lowercase_consumer_key_rejected(self):
        # IBKR uppercases consumer keys at registration; lowercase is wrong
        with pytest.raises(ValueError, match="uppercase"):
            self._base_creds(consumer_key="calypsopp").validate_secrets()

    def test_too_long_consumer_key_rejected(self):
        with pytest.raises(ValueError, match="1-9 chars"):
            self._base_creds(consumer_key="TOOLONGKEY").validate_secrets()

    def test_empty_access_token_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            self._base_creds(access_token="").validate_secrets()

    def test_empty_access_token_secret_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            self._base_creds(access_token_secret="").validate_secrets()

    def test_consumer_key_with_digit_rejected(self):
        # 9 chars but contains a digit — IBKR portal accepts A-Z only
        with pytest.raises(ValueError, match="A-Z"):
            self._base_creds(consumer_key="CALYPSO01").validate_secrets()


class TestValidatePaths:
    def test_existing_paths_pass(self, tmp_path):
        sig = tmp_path / "sig.pem"; sig.write_text("x")
        enc = tmp_path / "enc.pem"; enc.write_text("x")
        dh  = tmp_path / "dh.pem";  dh.write_text("x")
        creds = IBKRCredentials(
            environment="paper", consumer_key="K", access_token="t", access_token_secret="s",
            private_signature_path=sig, private_encryption_path=enc, dh_param_path=dh,
        )
        creds.validate_paths()  # no raise

    def test_missing_signature_raises(self, tmp_path):
        enc = tmp_path / "enc.pem"; enc.write_text("x")
        dh  = tmp_path / "dh.pem";  dh.write_text("x")
        creds = IBKRCredentials(
            environment="paper", consumer_key="K", access_token="t", access_token_secret="s",
            private_signature_path=tmp_path / "missing.pem",
            private_encryption_path=enc, dh_param_path=dh,
        )
        with pytest.raises(FileNotFoundError, match="signature"):
            creds.validate_paths()


# ─── Crypto backend safety ──────────────────────────────────────────────────


class TestAssertSafeCryptoBackend:
    def test_pycryptodome_3x_passes(self):
        # The currently-installed Crypto is pycryptodome 3.x — should pass.
        assert_safe_crypto_backend()  # no raise

    def test_pycrypto_2x_fails(self, monkeypatch):
        # Simulate pycrypto's last release (2.6.1) being installed instead.
        import Crypto
        monkeypatch.setattr(Crypto, "__version__", "2.6.1", raising=False)
        with pytest.raises(RuntimeError, match="Unsafe crypto backend"):
            assert_safe_crypto_backend()

    def test_unknown_backend_fails(self, monkeypatch):
        import Crypto
        monkeypatch.setattr(Crypto, "__version__", "999.999", raising=False)
        with pytest.raises(RuntimeError, match="Unsafe crypto backend"):
            assert_safe_crypto_backend()


# ─── ibind OAuth1aConfig construction ───────────────────────────────────────


class TestBuildOauth1aConfig:
    def test_constructs_config_with_real_files(self, real_paper_dhparam, tmp_path):
        # Use real DH file but fake private keys (we're testing the wiring,
        # not the cryptographic content — ibind's IbkrClient init does the
        # actual signing later).
        sig_path = tmp_path / "private_signature.pem"
        enc_path = tmp_path / "private_encryption.pem"
        sig_path.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
        enc_path.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")

        creds = IBKRCredentials(
            environment="paper",
            consumer_key="CALYPSOPP",
            access_token="fake_token",
            access_token_secret="fake_secret",
            private_signature_path=sig_path,
            private_encryption_path=enc_path,
            dh_param_path=real_paper_dhparam,
        )
        cfg = build_oauth1a_config(creds)
        assert cfg.consumer_key == "CALYPSOPP"
        assert cfg.access_token == "fake_token"
        assert cfg.access_token_secret == "fake_secret"
        assert cfg.encryption_key_fp == str(enc_path)
        assert cfg.signature_key_fp == str(sig_path)
        # DH prime should be a long hex string
        assert len(cfg.dh_prime) >= 500
        assert all(c in "0123456789abcdef" for c in cfg.dh_prime)
        assert cfg.init_brokerage_session is True

    def test_init_brokerage_session_false_pollerized(self, real_paper_dhparam, tmp_path):
        # The activation poller passes init_brokerage_session=False so it
        # only tests the OAuth handshake, not the full brokerage path.
        sig = tmp_path / "s.pem"; sig.write_text("fake")
        enc = tmp_path / "e.pem"; enc.write_text("fake")
        creds = IBKRCredentials(
            environment="paper", consumer_key="K", access_token="t", access_token_secret="s",
            private_signature_path=sig, private_encryption_path=enc, dh_param_path=real_paper_dhparam,
        )
        cfg = build_oauth1a_config(creds, init_brokerage_session=False)
        assert cfg.init_brokerage_session is False
