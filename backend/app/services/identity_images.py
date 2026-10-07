"""Validate, sanitize and encrypt private identity photographs."""

import hashlib
from io import BytesIO
from pathlib import PurePath

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.models.academic import RecordIdentityImage
from app.services.identity import identity_key
from app.services.order_attachments import MAX_ATTACHMENT_BYTES

MAX_IDENTITY_PIXELS = 16_000_000


def prepare_image(filename, data, scanner):
    suffix = PurePath(filename or "").suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(422, "ID images must be JPEG or PNG photographs")
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "Each ID image must contain data and be at most 2 MiB")
    if settings.ATTACHMENT_SCANNER == "clamav":
        scanner.scan(data)
    try:
        with Image.open(BytesIO(data), formats=["JPEG", "PNG"]) as image:
            if image.format != ("PNG" if suffix == ".png" else "JPEG"):
                raise HTTPException(422, "ID image contents do not match its extension")
            if (
                image.width * image.height > MAX_IDENTITY_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise HTTPException(
                    422, "Use a single ID photograph of at most 16 megapixels"
                )
            image.load()
            oriented = ImageOps.exif_transpose(image).convert("RGB")
            # Copy pixels only: original metadata and trailing content are never retained.
            clean = Image.new("RGB", oriented.size)
            clean.paste(oriented)
            output = BytesIO()
            clean.save(output, format="JPEG", quality=95)
            sanitized = output.getvalue()
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise HTTPException(
            422, "The ID image is damaged or is not a valid JPEG/PNG"
        ) from exc
    if len(sanitized) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            413, "The processed ID image exceeds 2 MiB; use a smaller photograph"
        )
    cipher, _ = identity_key()
    return {
        "size_bytes": len(sanitized),
        "ciphertext": cipher.encrypt(sanitized),
        "content_digest": hashlib.sha256(
            str(clean.size).encode() + clean.tobytes()
        ).hexdigest(),
    }


def attach_images(link, document_type, images):
    link.identity_images = [
        RecordIdentityImage(document_type=document_type, side=side, **data)
        for side, data in images.items()
    ]


def decrypt_image(image):
    cipher, _ = identity_key()
    try:
        return cipher.decrypt(image.ciphertext)
    except InvalidToken as exc:
        raise HTTPException(
            503, "Identity image unavailable; contact the institution"
        ) from exc
