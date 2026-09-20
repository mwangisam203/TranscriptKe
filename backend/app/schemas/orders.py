from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.models.academic import DeliveryMethod
from app.schemas.academic import Id, ShortText, clean_text
from app.schemas.auth import NormalizedEmail, StrictInput

Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
Reason = Annotated[
    str, BeforeValidator(clean_text), Field(min_length=1, max_length=2000)
]


class VersionInput(StrictInput):
    expected_version: int = Field(ge=1)


class OrderCreate(StrictInput):
    academic_record_link_id: Id


class PostalAddress(StrictInput):
    line1: ShortText
    line2: str = Field(default="", max_length=255)
    city: ShortText
    postal_code: str = Field(min_length=1, max_length=30)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class RecipientInput(StrictInput):
    key: Key
    name: ShortText
    organization: str = Field(default="", max_length=255)
    email: NormalizedEmail | None = Field(default=None, max_length=255)
    delivery_method: DeliveryMethod
    postal_address: PostalAddress | None = None
    application_reference: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def destination_details(self):
        if self.delivery_method == "secure_electronic" and self.email is None:
            raise ValueError("Electronic delivery requires a recipient email")
        if self.delivery_method == "post" and self.postal_address is None:
            raise ValueError("Postal delivery requires a complete address")
        if self.delivery_method != "post" and self.postal_address is not None:
            raise ValueError("Postal addresses are only collected for postal delivery")
        return self


class ItemInput(StrictInput):
    key: Key
    service_id: Id
    recipient_key: Key
    quantity: int = Field(default=1, ge=1, le=10, strict=True)


class DraftInput(VersionInput):
    purpose: str = Field(default="", max_length=1000)
    release_when: Literal["now", "after_grades", "after_graduation"] = "now"
    release_instruction: str = Field(default="", max_length=255)
    recipients: list[RecipientInput] = Field(default_factory=list, max_length=10)
    items: list[ItemInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent_keys(self):
        recipient_keys = [r.key for r in self.recipients]
        item_keys = [item.key for item in self.items]
        if len(set(recipient_keys)) != len(recipient_keys) or len(
            set(item_keys)
        ) != len(item_keys):
            raise ValueError("Recipient and item keys must be unique within an order")
        if any(item.recipient_key not in recipient_keys for item in self.items):
            raise ValueError("Each item must belong to a recipient in this order")
        pairs = [(item.service_id, item.recipient_key) for item in self.items]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "Use quantity instead of duplicate service/recipient items"
            )
        return self


class RecipientMutation(RecipientInput, VersionInput):
    pass


class ItemMutation(ItemInput, VersionInput):
    pass


class ConsentInput(StrictInput):
    quote_id: UUID
    text_version: str = Field(max_length=30)
    accepted: Literal[True]


class SubmitInput(VersionInput):
    quote_id: UUID
    consent_id: Id


class CancellationInput(VersionInput):
    reason: Reason


class CancellationDecision(VersionInput):
    decision: Literal["approved", "rejected"]
    reason: Reason


class MessageInput(StrictInput):
    body: Reason
    in_reply_to_id: Id | None = None


class StaffMessageInput(MessageInput):
    requires_response: bool = False


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    media_type: str
    size: int
    sha256: str
    scan_method: str
    created_at: datetime


class QuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    order_id: int
    order_version: int
    snapshot: dict
    total_minor: int
    currency: str
    expires_at: datetime


class ConsentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    quote_id: str
    text_version: str
    text: str
    accepted_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: int
    kind: str
    message: str
    created_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    author_role: str
    body: str
    requires_response: bool
    in_reply_to_id: int | None
    answered_at: datetime | None
    created_at: datetime


class CancellationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reason: str
    status: str
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None


class OrderRead(BaseModel):
    id: int
    reference: str
    institution_id: int
    academic_record_link_id: int
    status: str
    payment_status: str
    version: int
    purpose: str
    release_when: str
    release_instruction: str
    recipients: list[RecipientInput]
    items: list[dict]
    attachments: list[AttachmentRead]
    cancellations: list[CancellationRead]
    unanswered_questions: int
    submitted_snapshot: dict | None
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None
