import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator, model_validator

from app.schemas.common import StrictInput


def clean(value):
    return " ".join(value.split()) if isinstance(value, str) else value


Name = Annotated[str, BeforeValidator(clean), Field(min_length=1, max_length=100)]
OptionalText = Annotated[str, BeforeValidator(clean), Field(max_length=200)]


class PersonalDetails(StrictInput):
    first_name: Name
    middle_name: OptionalText = ""
    last_name: Name
    date_of_birth: date
    highest_education: Literal[
        "primary",
        "secondary",
        "certificate",
        "diploma",
        "bachelors",
        "masters",
        "doctorate",
        "other",
    ]
    country: Name
    mobile_phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$", max_length=16)
    address_line1: Name
    address_line2: OptionalText = ""
    city: Name
    state_region: OptionalText = ""
    postal_code: OptionalText = ""

    @field_validator("mobile_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        return re.sub(r"[\s().-]", "", value) if isinstance(value, str) else value

    @field_validator("date_of_birth")
    @classmethod
    def real_birth_date(cls, value):
        if value < date(1900, 1, 1) or value > datetime.now(timezone.utc).date():
            raise ValueError("Use a birth date from 1900 through today")
        return value

    @model_validator(mode="after")
    def name_fits_account(self):
        if len(self.full_name) > 255:
            raise ValueError("Your combined name must be at most 255 characters")
        return self

    @property
    def full_name(self):
        return " ".join(
            part for part in [self.first_name, self.middle_name, self.last_name] if part
        )


class ProfileWrite(PersonalDetails):
    expected_version: int = Field(ge=0)


class ProfileRead(PersonalDetails):
    version: int
    updated_at: datetime
