import os
import smtplib
import ssl
from email.message import EmailMessage
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import settings


class Mailer:
    def send_token(self, email: str, purpose: str, token: str, minutes: int) -> None:
        message = EmailMessage()
        message["From"] = settings.MAIL_FROM
        message["To"] = email
        message["Subject"] = f"TranscriptsKE: {purpose.replace('_', ' ')}"
        # The UI will consume these codes when its verification screens are built.
        message.set_content(
            f"Your TranscriptsKE {purpose.replace('_', ' ')} code is:\n\n"
            f"{token}\n\nIt expires in {minutes} minutes and can be used once.\n"
            "If you did not expect this message, you can ignore it.\n"
        )
        try:
            if settings.MAIL_BACKEND == "file":
                settings.MAIL_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = settings.MAIL_DIRECTORY / f"{uuid4().hex}.eml"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(message.as_bytes())
            else:
                with smtplib.SMTP(
                    settings.SMTP_HOST, settings.SMTP_PORT, timeout=10
                ) as smtp:
                    if settings.SMTP_STARTTLS:
                        smtp.starttls(context=ssl.create_default_context())
                    if settings.SMTP_USERNAME:
                        smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
                    smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # Do not put addresses, codes or SMTP credentials in API errors/logs.
            raise HTTPException(
                503, "Email delivery is unavailable; please try again"
            ) from exc


def get_mailer() -> Mailer:
    return Mailer()
