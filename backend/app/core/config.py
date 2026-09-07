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

    @model_validator(mode="after")
    def validate_deployment(self):
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
