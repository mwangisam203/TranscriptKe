"""Encrypt record-locating identifiers; expose them only through audited staff access."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.core.config import settings


def identity_key() -> tuple[Fernet, bytes]:
    try:
        key = (settings.IDENTITY_ENCRYPTION_KEY or "").encode("ascii")
        cipher = Fernet(key)
        return cipher, base64.urlsafe_b64decode(key)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(
            503,
            "ID matching is unavailable; contact the institution or use your admission number",
        ) from exc


def identity_fields(raw: str | None, user_id: int, institution_id: int) -> dict:
    if raw is None:
        return {
            "identity_ciphertext": None,
            "identity_fingerprint": None,
            "identity_masked": None,
        }
    cipher, key = identity_key()
    fingerprint = hmac.new(
        key,
        f"record-identity:{user_id}:{institution_id}:{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "identity_ciphertext": cipher.encrypt(raw.encode()).decode(),
        "identity_fingerprint": fingerprint,
        "identity_masked": "••••" + raw[-2:],
    }


def reveal_identity(ciphertext: str) -> str:
    cipher, _ = identity_key()
    try:
        return cipher.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise HTTPException(
            503, "ID information is unavailable; contact the institution"
        ) from exc
