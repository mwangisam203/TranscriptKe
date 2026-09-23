import subprocess

from fastapi import HTTPException

from app.core.config import settings

MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENTS = 5
BINARY_TYPES = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}


class AttachmentScanner:
    def scan(self, data: bytes):
        if settings.ATTACHMENT_SCANNER != "clamav":
            raise HTTPException(
                503,
                "PDF and image attachments need the institution's malware scanner. Text attachments are available.",
            )
        try:
            result = subprocess.run(
                [settings.CLAMAV_COMMAND, "--no-summary", "-"],
                input=data,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(
                503, "Attachment scanning is unavailable; try again later"
            ) from exc
        if result.returncode == 1:
            raise HTTPException(422, "Attachment rejected by the malware scanner")
        if result.returncode != 0:
            raise HTTPException(
                503, "Attachment scanning is unavailable; try again later"
            )


def get_attachment_scanner():
    return AttachmentScanner()


def validate_attachment(filename, data, scanner):
    if (
        not filename
        or len(filename) > 200
        or any(char in filename for char in ("/", "\\", "\r", "\n"))
        or any(ord(char) < 32 for char in filename)
    ):
        raise HTTPException(422, "Use a simple filename without path separators")
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "Attachments must contain data and be at most 2 MiB")
    suffix = "." + filename.rsplit(".", 1)[-1].lower()
    if suffix == ".txt":
        try:
            value = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Text attachments must be UTF-8") from exc
        if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
            raise HTTPException(
                422, "Text attachments cannot contain binary control characters"
            )
        return "text/plain", "utf8_text"
    if suffix not in BINARY_TYPES:
        raise HTTPException(
            422, "Only TXT, PDF, PNG and JPEG attachments are supported"
        )
    media_type, signature = BINARY_TYPES[suffix]
    if not data.startswith(signature):
        raise HTTPException(422, "Attachment contents do not match its file extension")
    scanner.scan(data)
    return media_type, "clamav"


class AttachmentBodyLimit:
    """Stop oversized multipart streams before the framework spools the complete upload."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").endswith(("/attachments", "/documents"))
        ):
            return await self.app(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > MAX_ATTACHMENT_BYTES + 65536:
                raise HTTPException(413, "Attachment upload exceeds the 2 MiB limit")
            return message

        await self.app(scope, limited_receive, send)
