import os
from cryptography.fernet import Fernet

_ENV_KEY = "MASTER_ENCRYPTION_KEY"


def _get_cipher() -> Fernet:
    raw = os.environ.get(_ENV_KEY, "")
    if not raw:
        raise RuntimeError(f"{_ENV_KEY} is not set")
    return Fernet(raw)


def encrypt_secret(plaintext: str) -> str:
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _get_cipher().decrypt(ciphertext.encode()).decode()