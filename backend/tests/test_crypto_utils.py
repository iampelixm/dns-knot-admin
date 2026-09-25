"""Тесты crypto_utils.py: encrypt/decrypt roundtrip, missing key error."""

import os
from base64 import urlsafe_b64encode

import pytest
from cryptography.fernet import Fernet

from app import crypto_utils

_TEST_KEY = urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture(autouse=True)
def _ensure_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _TEST_KEY)


class TestEncryptDecrypt:
    def test_roundtrip(self) -> None:
        secret = "base64secret123"
        encrypted = crypto_utils.encrypt_secret(secret)
        assert encrypted != secret
        decrypted = crypto_utils.decrypt_secret(encrypted)
        assert decrypted == secret

    def test_empty_string(self) -> None:
        encrypted = crypto_utils.encrypt_secret("")
        assert crypto_utils.decrypt_secret(encrypted) == ""

    def test_long_secret(self) -> None:
        secret = "A" * 1000
        encrypted = crypto_utils.encrypt_secret(secret)
        assert crypto_utils.decrypt_secret(encrypted) == secret

    def test_different_encryptions(self) -> None:
        """Same plaintext should produce different ciphertexts (Fernet nonce)."""
        secret = "hello"
        e1 = crypto_utils.encrypt_secret(secret)
        e2 = crypto_utils.encrypt_secret(secret)
        assert e1 != e2
        assert crypto_utils.decrypt_secret(e1) == "hello"
        assert crypto_utils.decrypt_secret(e2) == "hello"


class TestMissingKey:
    def test_no_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MASTER_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MASTER_ENCRYPTION_KEY"):
            crypto_utils.encrypt_secret("x")

    def test_empty_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MASTER_ENCRYPTION_KEY", "")
        with pytest.raises(RuntimeError, match="MASTER_ENCRYPTION_KEY"):
            crypto_utils.encrypt_secret("x")