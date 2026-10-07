"""Account owners control their personal details; no private details enter auth responses."""

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.profile import UserProfile
from app.schemas.profile import PersonalDetails, ProfileRead
from app.services.identity import identity_key
from app.services.tokens import utcnow


def profile_cipher():
    try:
        cipher, _ = identity_key()
    except HTTPException as exc:
        raise HTTPException(
            503, "Personal details are unavailable; try later or contact support"
        ) from exc
    return cipher


def encrypt_details(details):
    cipher = profile_cipher()
    return cipher.encrypt(
        details.model_dump_json(exclude={"expected_version"}).encode()
    ).decode()


def read_details(profile):
    if profile is None:
        return None
    cipher = profile_cipher()
    try:
        details = PersonalDetails.model_validate_json(
            cipher.decrypt(profile.details_ciphertext.encode())
        )
    except (InvalidToken, ValidationError) as exc:
        raise HTTPException(
            503, "Personal details are unavailable; contact support"
        ) from exc
    return ProfileRead(
        **details.model_dump(), version=profile.version, updated_at=profile.updated_at
    )


def create_profile(db, user, details):
    encrypted = encrypt_details(details)
    db.add(UserProfile(user_id=user.id, details_ciphertext=encrypted))
    user.full_name = details.full_name


def update_profile(db, user, details):
    profile = db.get(UserProfile, user.id)
    if details.expected_version != (profile.version if profile else 0):
        raise HTTPException(409, "Your personal details changed. Reload before saving.")
    if profile is None:
        create_profile(db, user, details)
    else:
        profile.details_ciphertext = encrypt_details(details)
        profile.version += 1
        profile.updated_at = utcnow()
        user.full_name = details.full_name
    db.flush()
    return db.get(UserProfile, user.id)
