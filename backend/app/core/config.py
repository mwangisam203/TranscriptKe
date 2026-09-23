from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    APP_NAME: str = "TranscriptsKE"
    APP_ENV: Literal["development", "test", "production"] = "development"
    DATABASE_URL: str
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1)
    EMAIL_VERIFICATION_EXPIRE_MINUTES: int = Field(default=60, ge=1)
    PASSWORD_RESET_EXPIRE_MINUTES: int = Field(default=30, ge=1)
    INVITATION_EXPIRE_MINUTES: int = Field(default=1440, ge=1)
    MAIL_BACKEND: Literal["file", "smtp"] = "file"
    MAIL_DIRECTORY: Path = BASE_DIR / ".mailbox"
    MAIL_FROM: str = "no-reply@transcriptske.example"
    SMTP_HOST: str | None = None
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_STARTTLS: bool = True
    ATTACHMENT_SCANNER: Literal["disabled", "clamav"] = "disabled"
    CLAMAV_COMMAND: str = "clamscan"
    ISSUANCE_ENABLED: bool = False
    ISSUANCE_MODE: Literal["demo", "live"] = "demo"
    ISSUANCE_PUBLIC_URL: str = "http://127.0.0.1:8000"
    DELIVERY_EXPIRE_DAYS: int = Field(default=7, ge=1, le=30)

    PAYMENTS_ENABLED: bool = False
    PAYMENT_MODE: Literal["test", "live"] = "test"
    PAYMENT_INSTITUTION_ID: int | None = Field(default=None, ge=1)
    PAYMENT_PUBLIC_URL: str = "http://127.0.0.1:8000"
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    MPESA_CONSUMER_KEY: str | None = None
    MPESA_CONSUMER_SECRET: str | None = None
    MPESA_SHORTCODE: str | None = None
    MPESA_PASSKEY: str | None = None
    MPESA_INITIATOR: str | None = None
    MPESA_SECURITY_CREDENTIAL: str | None = None

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.ISSUANCE_ENABLED:
            from urllib.parse import urlsplit

            origin = urlsplit(self.ISSUANCE_PUBLIC_URL)
            if (
                origin.scheme not in ("http", "https")
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.query
                or origin.fragment
                or origin.path not in ("", "/")
            ):
                raise ValueError(
                    "ISSUANCE_PUBLIC_URL must be an origin without a path or credentials"
                )
            if self.ISSUANCE_MODE == "live" and (
                origin.scheme != "https"
                or self.ATTACHMENT_SCANNER != "clamav"
                or self.MAIL_BACKEND != "smtp"
                or not self.SMTP_STARTTLS
            ):
                raise ValueError(
                    "Live issuance requires HTTPS, ClamAV and SMTP with STARTTLS"
                )
            if self.APP_ENV == "production" and self.ISSUANCE_MODE != "live":
                raise ValueError("Production cannot issue demo documents")
        if self.PAYMENTS_ENABLED:
            if len(self.SECRET_KEY) < 32 or self.SECRET_KEY == "change-this-secret-key":
                raise ValueError(
                    "Payments require a random SECRET_KEY of at least 32 characters"
                )
            from urllib.parse import urlsplit

            url = urlsplit(self.PAYMENT_PUBLIC_URL)
            if not self.PAYMENT_INSTITUTION_ID:
                raise ValueError(
                    "Set PAYMENT_INSTITUTION_ID for the authorized pilot merchant"
                )
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
            ):
                raise ValueError(
                    "PAYMENT_PUBLIC_URL must be a public origin without credentials or a path"
                )
            if self.PAYMENT_MODE == "live" and url.scheme != "https":
                raise ValueError("Live payments require HTTPS")
            if self.STRIPE_SECRET_KEY and not self.STRIPE_SECRET_KEY.startswith(
                "sk_live_" if self.PAYMENT_MODE == "live" else "sk_test_"
            ):
                raise ValueError("Stripe key must match PAYMENT_MODE")
            if self.APP_ENV == "production" and self.PAYMENT_MODE != "live":
                raise ValueError("Production payments must use live credentials")
        if self.MAIL_BACKEND == "smtp" and not self.SMTP_HOST:
            raise ValueError("SMTP_HOST is required for SMTP delivery")
        if self.APP_ENV == "production":
            if len(self.SECRET_KEY) < 32 or self.SECRET_KEY == "change-this-secret-key":
                raise ValueError(
                    "Production requires a random SECRET_KEY of at least 32 characters"
                )
            if self.MAIL_BACKEND != "smtp" or not self.SMTP_STARTTLS:
                raise ValueError("Production requires SMTP delivery with STARTTLS")
        return self

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
