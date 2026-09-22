import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.orders import Reason, VersionInput


class PaymentCreate(VersionInput):
    provider: Literal["stripe", "mpesa"]
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value):
        if value is None:
            return None
        value = value.strip().replace(" ", "")
        if value.startswith("+"):
            value = value[1:]
        if re.fullmatch(r"0[17]\d{8}", value):
            value = "254" + value[1:]
        if not re.fullmatch(r"254[17]\d{8}", value):
            raise ValueError("Use a Kenyan mobile number, for example 0712345678")
        return value

    @model_validator(mode="after")
    def provider_details(self):
        if self.provider == "mpesa" and not self.phone:
            raise ValueError("M-Pesa requires a mobile number")
        if self.provider == "stripe" and self.phone:
            raise ValueError("A mobile number is only collected for M-Pesa")
        return self


class RefundInput(VersionInput):
    reason: Reason
