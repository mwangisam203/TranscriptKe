from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.user import UserRole
from app.schemas.common import StrictInput
from app.schemas.profile import PersonalDetails


def normalize_email(value):
    return value.strip().lower() if isinstance(value, str) else value


NormalizedEmail = Annotated[EmailStr, BeforeValidator(normalize_email)]


class EmailRequest(StrictInput):
    email: NormalizedEmail = Field(max_length=255)


class NewPassword(StrictInput):
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer")
        return value


class UserRegister(EmailRequest, NewPassword):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    profile: PersonalDetails | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_name(self):
        if self.profile is not None:
            self.full_name = self.profile.full_name
        if not self.full_name or len(self.full_name) > 255:
            raise ValueError("Provide a full name of at most 255 characters")
        return self


class UserLogin(EmailRequest):
    password: str = Field(min_length=1, max_length=1024)


class TokenConfirmation(StrictInput):
    token: str = Field(min_length=32, max_length=256)


class PasswordReset(TokenConfirmation, NewPassword):
    pass


class PasswordChange(NewPassword):
    current_password: str = Field(min_length=1, max_length=1024)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_email_verified: bool
    full_name: str
    role: UserRole
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Message(BaseModel):
    message: str
