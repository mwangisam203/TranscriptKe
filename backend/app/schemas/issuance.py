from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.orders import Reason, VersionInput


class IssueInput(VersionInput):
    internal_note: Reason
    attested: bool = Field(strict=True)


class RevokeInput(VersionInput):
    reason: Reason


class RecipientEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class DownloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=100)
