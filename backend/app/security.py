from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet:
    if not settings.tokens_fernet_key:
        raise RuntimeError("TOKENS_FERNET_KEY is required for secure token storage.")
    return Fernet(settings.tokens_fernet_key.encode("utf-8"))


def encrypt_str(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_str(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Failed to decrypt token payload. Check TOKENS_FERNET_KEY.") from e

