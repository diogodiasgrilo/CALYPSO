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
        # No uppercase hex letters anywhere
        assert prime == prime.lower()
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

    # P7 Step 4 (option B) — systemd LoadCredentialEncrypted= path:
    # when $CREDENTIALS_DIRECTORY is set, all six credentials are read
    # from files there instead of env vars + $CALYPSO_IBKR_KEYS_DIR.

    def test_systemd_credentials_directory_reads_all_six(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        (tmp_path / "ibkr_consumer_key").write_text("CALYPSOPP\n")
        (tmp_path / "ibkr_access_token").write_text("tok\n")
        (tmp_path / "ibkr_access_token_secret").write_text("sec\n")
        (tmp_path / "ibkr_signature_pem").write_text("sig")
        (tmp_path / "ibkr_encryption_pem").write_text("enc")
        (tmp_path / "ibkr_dhparam_pem").write_text("dh")
        creds = load_credentials("paper")
        assert creds.consumer_key == "CALYPSOPP"  # .strip() drops the \n
        assert creds.access_token == "tok"
        assert creds.access_token_secret == "sec"
        assert creds.private_signature_path == tmp_path / "ibkr_signature_pem"
        assert creds.private_encryption_path == tmp_path / "ibkr_encryption_pem"
        assert creds.dh_param_path == tmp_path / "ibkr_dhparam_pem"

    def test_systemd_credentials_explicit_args_win(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        (tmp_path / "ibkr_consumer_key").write_text("FROMFILE")
        creds = load_credentials("paper", consumer_key="FROMARG",
                                 access_token="t", access_token_secret="s")
        assert creds.consumer_key == "FROMARG"

    def test_systemd_missing_string_credential_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        creds = load_credentials("paper")  # no files created
        assert creds.consumer_key == ""
        assert creds.access_token == ""

    # ─── P7-audit M2: distinguish absent from unreadable ─────────────────

    def test_unreadable_credential_logs_warning_and_returns_empty(
        self, monkeypatch, tmp_path, caplog
    ):
        """M2: when a credential file exists but can't be read (permission
        denied), the loader returns "" (so downstream validation produces
        a single consistent error) AND logs a WARNING so the operator
        can see the deployment misconfig — distinct from "file absent".
        """
        import logging
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        # Create the file but make it unreadable. chmod 000 is enough on
        # POSIX to surface PermissionError from read_text() when running
        # as a non-root user.
        ck = tmp_path / "ibkr_consumer_key"
        ck.write_text("CALYPSOPP")
        ck.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING, logger="shared.ib_oauth"):
                creds = load_credentials("paper")
            assert creds.consumer_key == ""
            # WARNING message names the path and surfaces the OSError type
            warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
            assert any("ibkr_consumer_key" in m for m in warning_msgs), (
                f"expected WARNING mentioning the credential file path, "
                f"got: {warning_msgs}"
            )
        finally:
            # Restore so pytest can clean up tmp_path
            ck.chmod(0o600)

    # ─── P7-audit M3: empty CREDENTIALS_DIRECTORY raises ────────────────

    def test_empty_credentials_directory_raises(self, monkeypatch):
        """M3: a set-but-empty CREDENTIALS_DIRECTORY env var is a systemd
        misconfig (LoadCredentialEncrypted= failed). Don't silently fall
        through to the dev path — raise so the deployment bug surfaces.
        """
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", "")
        with pytest.raises(RuntimeError, match="CREDENTIALS_DIRECTORY"):
            load_credentials("paper")

    def test_whitespace_only_credentials_directory_raises(self, monkeypatch):
        """M3 variant: whitespace-only env var is also a misconfig."""
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", "   ")
        with pytest.raises(RuntimeError, match="CREDENTIALS_DIRECTORY"):
            load_credentials("paper")

    def test_unset_credentials_directory_falls_through_to_dev_path(
        self, monkeypatch
    ):
        """M3: unsetting CREDENTIALS_DIRECTORY (vs setting to "") falls
        through to the dev env-var path unchanged. Pinning the
        distinction so the M3 guard doesn't accidentally break dev."""
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        # Provide explicit args so we don't depend on the dev env vars
        # being unset / set in CI.
        creds = load_credentials("paper", consumer_key="ck", access_token="t",
                                 access_token_secret="s")
        assert creds.consumer_key == "ck"


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
        with pytest.raises(ValueError, match="at most 9 chars"):
            self._base_creds(consumer_key="TOOLONGKEY").validate_secrets()

    def test_empty_access_token_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            self._base_creds(access_token="").validate_secrets()

    def test_empty_access_token_secret_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            self._base_creds(access_token_secret="").validate_secrets()

    def test_consumer_key_with_lowercase_rejected(self):
        # Lowercase still fails — IBKR's portal silently uppercases at
        # registration, but our validate_secrets is strict so callers
        # see the canonical key. (Digits now ALLOWED, see next test.)
        with pytest.raises(ValueError, match="uppercase"):
            self._base_creds(consumer_key="calypso").validate_secrets()

    def test_consumer_key_with_digit_accepted(self):
        # Per relaxed regex ^[A-Z0-9]+$ — alphanumeric is OK.
        self._base_creds(consumer_key="CALYPSO01").validate_secrets()

    def test_consumer_key_with_lowercase_letter_rejected(self):
        with pytest.raises(ValueError, match="uppercase"):
            self._base_creds(consumer_key="CALYPSOa").validate_secrets()

    # ─── Polish Item 10: secret-leak regression tests ─────────────────────

    def test_invalid_consumer_key_error_message_does_NOT_echo_value(self):
        """Polish Item 10: validate_secrets must not echo the bad
        consumer_key value in its error message. A bad key that hits the
        regex check would otherwise leak into tracebacks and log lines.

        Test value `'BADcal999'` is 9 chars (passes the length check) and
        has lowercase letters (fails the [A-Z0-9]+ regex check) — that's
        the branch the redaction-marker lives in.
        """
        SECRET_LEAKED_IF_PRESENT = "BADcal999"  # 9 chars, has lowercase
        assert len(SECRET_LEAKED_IF_PRESENT) <= 9  # sanity: hits regex branch
        try:
            self._base_creds(consumer_key=SECRET_LEAKED_IF_PRESENT).validate_secrets()
        except ValueError as e:
            assert SECRET_LEAKED_IF_PRESENT not in str(e), (
                f"validate_secrets error message echoed the consumer_key "
                f"value: {e!r}"
            )
            assert "redacted" in str(e), (
                f"expected the redaction-marker in the message: {e!r}"
            )

    def test_too_long_consumer_key_error_message_includes_length_only(self):
        """The too-long branch already uses len() rather than the value;
        pin that contract."""
        SECRET_LEAKED_IF_PRESENT = "VERYLONGSECRETKEY12345"
        try:
            self._base_creds(consumer_key=SECRET_LEAKED_IF_PRESENT).validate_secrets()
        except ValueError as e:
            assert SECRET_LEAKED_IF_PRESENT not in str(e), (
                f"too-long branch echoed the consumer_key value: {e!r}"
            )
            assert str(len(SECRET_LEAKED_IF_PRESENT)) in str(e), (
                f"expected the length in the message: {e!r}"
            )


class TestCredentialReprDoesNotLeak:
    """Polish Item 10: the IBKRCredentials default dataclass __repr__ must
    not include the three string secrets (consumer_key, access_token,
    access_token_secret). The path fields are not secret and may remain.

    A failure here means a future refactor accidentally removed the
    field(repr=False) markers — and any traceback / logger.exception()
    that touches a credentials object would start leaking the secrets
    into logs.
    """

    def _make_creds(self):
        return IBKRCredentials(
            environment="paper",
            consumer_key="CK_LEAK_CANARY_9",
            access_token="AT_LEAK_CANARY_ABCDEFGH",
            access_token_secret="ATS_LEAK_CANARY_IJKLMNOP",
            private_signature_path=Path("/tmp/sig.pem"),
            private_encryption_path=Path("/tmp/enc.pem"),
            dh_param_path=Path("/tmp/dh.pem"),
        )

    def test_repr_excludes_consumer_key(self):
        r = repr(self._make_creds())
        assert "CK_LEAK_CANARY" not in r, (
            f"repr() leaked consumer_key: {r!r}"
        )

    def test_repr_excludes_access_token(self):
        r = repr(self._make_creds())
        assert "AT_LEAK_CANARY" not in r, (
            f"repr() leaked access_token: {r!r}"
        )

    def test_repr_excludes_access_token_secret(self):
        r = repr(self._make_creds())
        assert "ATS_LEAK_CANARY" not in r, (
            f"repr() leaked access_token_secret: {r!r}"
        )

    def test_str_excludes_all_secrets(self):
        """str(creds) defaults to __repr__ for a frozen dataclass; pin
        that no secret leaks via the str() path either."""
        s = str(self._make_creds())
        for secret in ("CK_LEAK_CANARY", "AT_LEAK_CANARY", "ATS_LEAK_CANARY"):
            assert secret not in s, f"str() leaked {secret}: {s!r}"

    def test_repr_keeps_environment_for_debugging(self):
        """The non-secret fields (environment, paths) should still appear
        in repr — operators need to identify which credentials object
        they're looking at."""
        r = repr(self._make_creds())
        assert "paper" in r, (
            f"environment should remain in repr for debugging: {r!r}"
        )

    def test_f_string_format_does_not_leak(self):
        """f-strings call __format__ which defaults to __str__ which
        defaults to __repr__ — pin that the chain doesn't leak."""
        c = self._make_creds()
        formatted = f"creds={c}"
        for secret in ("CK_LEAK_CANARY", "AT_LEAK_CANARY", "ATS_LEAK_CANARY"):
            assert secret not in formatted, (
                f"f-string leaked {secret}: {formatted!r}"
            )


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
