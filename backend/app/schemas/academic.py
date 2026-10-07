from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.models.academic import DeliveryMethod, DocumentType, MatchingField, MatchStatus
from app.schemas.auth import StrictInput


def clean_text(value):
    return " ".join(value.split()) if isinstance(value, str) else value


def clean_code(value):
    return clean_text(value).upper() if isinstance(value, str) else value


ShortText = Annotated[
    str, BeforeValidator(clean_text), Field(min_length=1, max_length=255)
]
RecordIdentifier = Annotated[
    str, BeforeValidator(clean_code), Field(min_length=1, max_length=100)
]
Id = Annotated[int, Field(gt=0, le=2_147_483_647)]


class ServiceWrite(StrictInput):
    code: Annotated[
        str,
        BeforeValidator(clean_code),
        Field(min_length=1, max_length=50, pattern=r"^[A-Z0-9_-]+$"),
    ]
    name: ShortText
    document_type: DocumentType
    description: str = Field(default="", max_length=2000)
    fee_minor: int = Field(ge=0, le=1_000_000_000, strict=True)
    currency: Literal["KES"] = "KES"
    processing_days_min: int = Field(ge=0, le=365)
    processing_days_max: int = Field(ge=0, le=365)
    delivery_methods: list[DeliveryMethod] = Field(min_length=1, max_length=3)
    required_fields: list[MatchingField] = Field(default_factory=list, max_length=5)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_options(self):
        if self.processing_days_max < self.processing_days_min:
            raise ValueError("Maximum processing days cannot precede the minimum")
        if len(set(self.delivery_methods)) != len(self.delivery_methods) or len(
            set(self.required_fields)
        ) != len(self.required_fields):
            raise ValueError("Options must not contain duplicates")
        return self


class ServiceUpdate(ServiceWrite):
    expected_version: int = Field(ge=1)


class ServiceRead(ServiceWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    institution_id: int
    version: int
    created_at: datetime


class PolicyWrite(StrictInput):
    expected_version: int = Field(ge=0)
    accepting_requests: bool
    student_instructions: str = Field(default="", max_length=4000)
    required_fields: list[MatchingField] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_fields(self):
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("Required fields must not contain duplicates")
        return self


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    institution_id: int
    accepting_requests: bool
    student_instructions: str
    required_fields: list[MatchingField]
    version: int


class ApprovalWrite(StrictInput):
    approved: bool
    expected_approved: bool
    reason: Annotated[
        str, BeforeValidator(clean_text), Field(min_length=1, max_length=1000)
    ]


class RecordSubmission(StrictInput):
    id_number_type: Literal["national_id", "passport"] = "national_id"
    identity_document_type: Literal["national_id", "driving_licence"] | None = None
    service_id: Id
    admission_number: RecordIdentifier | None = None
    id_number: (
        Annotated[
            str,
            BeforeValidator(clean_code),
            Field(min_length=4, max_length=32, pattern=r"^[A-Z0-9-]+$"),
        ]
        | None
    ) = None
    currently_enrolled: bool | None = Field(default=None, strict=True)
    name_on_record: ShortText
    program: ShortText | None = None
    attendance_start_year: int | None = Field(default=None, ge=1900)
    attendance_end_year: int | None = Field(default=None, ge=1900)
    attendance_start_month: int | None = Field(default=None, ge=1, le=12, strict=True)
    attendance_end_month: int | None = Field(default=None, ge=1, le=12, strict=True)
    previous_names: list[ShortText] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_attendance(self):
        if self.id_number and self.id_number_type == "national_id":
            import re

            if not re.fullmatch(r"[0-9]{7,8}", self.id_number):
                raise ValueError("National ID must contain 7 or 8 digits")
        if not self.admission_number and not self.id_number:
            raise ValueError(
                "Provide an admission number or a National ID/passport number for institutional review"
            )
        if self.currently_enrolled is True and (
            self.attendance_end_year is not None
            or self.attendance_end_month is not None
        ):
            raise ValueError(
                "Currently enrolled students must leave the end date blank"
            )
        if self.currently_enrolled is False and (
            self.attendance_end_year is None or self.attendance_end_month is None
        ):
            raise ValueError("Provide the month and year you graduated or left")
        now = datetime.now(timezone.utc)
        current_year = now.year
        for side in ("start", "end"):
            month = getattr(self, f"attendance_{side}_month")
            year = getattr(self, f"attendance_{side}_year")
            if (month is None) != (year is None):
                raise ValueError("Provide both month and year for each attendance date")
            if month is not None and year == current_year and month > now.month:
                raise ValueError("Attendance dates cannot be in the future")
        if (
            self.attendance_start_year == self.attendance_end_year
            and self.attendance_start_month is not None
            and self.attendance_end_month is not None
            and self.attendance_end_month < self.attendance_start_month
        ):
            raise ValueError("Attendance end month cannot precede the start month")
        if any(
            year is not None and year > current_year
            for year in [self.attendance_start_year, self.attendance_end_year]
        ):
            raise ValueError("Attendance years cannot be in the future")
        if (
            self.attendance_start_year
            and self.attendance_end_year
            and self.attendance_end_year < self.attendance_start_year
        ):
            raise ValueError("Attendance end year cannot precede the start year")
        return self


class RecordCreate(RecordSubmission):
    institution_id: Id


class RecordResubmit(RecordSubmission):
    expected_version: int = Field(ge=1)


class MatchDecision(StrictInput):
    expected_version: int = Field(ge=1)
    decision: Literal["matched", "rejected", "needs_information"]
    student_message: Annotated[
        str, BeforeValidator(clean_text), Field(min_length=1, max_length=2000)
    ]
    record_reference: RecordIdentifier | None = None
    internal_note: (
        Annotated[
            str, BeforeValidator(clean_text), Field(min_length=1, max_length=2000)
        ]
        | None
    ) = None
    ownership_confirmed: bool = False

    @model_validator(mode="after")
    def require_ownership_evidence(self):
        if self.decision == "matched":
            if (
                not self.record_reference
                or not self.ownership_confirmed
                or len(self.internal_note or "") < 20
            ):
                raise ValueError(
                    "Matching requires an institutional record reference, ownership confirmation, and an evidence note of at least 20 characters"
                )
        elif self.record_reference is not None or self.ownership_confirmed:
            raise ValueError(
                "Only a matched decision may confirm ownership or assign a record reference"
            )
        return self


class IdentityImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    side: Literal["front", "back"]
    document_type: Literal["national_id", "driving_licence"]
    size_bytes: int
    created_at: datetime


class RecordRead(BaseModel):
    checkout_required: bool = False
    model_config = ConfigDict(from_attributes=True)
    id: int
    institution_id: int
    service_id: int
    admission_number: str | None
    id_number_type: Literal["national_id", "passport"] | None
    identity_masked: str | None
    identity_document_type: Literal["national_id", "driving_licence"] | None
    identity_images: list[IdentityImageRead]
    name_on_record: str
    currently_enrolled: bool | None
    program: str | None
    attendance_start_year: int | None
    attendance_end_year: int | None
    attendance_start_month: int | None
    attendance_end_month: int | None
    previous_names: list[str]
    requirements_snapshot: dict
    status: MatchStatus
    student_message: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class StaffRecordRead(RecordRead):
    user_id: int
    record_reference: str | None


class IdentityRead(BaseModel):
    id_number: str


class RecordEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: int
    status: MatchStatus
    student_message: str | None
    submission_snapshot: dict
    created_at: datetime


class StaffRecordEventRead(RecordEventRead):
    actor_id: int
    internal_note: str | None
    record_reference: str | None
