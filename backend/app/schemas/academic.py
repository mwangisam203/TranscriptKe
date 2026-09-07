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
    required_fields: list[MatchingField] = Field(default_factory=list, max_length=4)
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
    required_fields: list[MatchingField] = Field(default_factory=list, max_length=4)

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
    service_id: Id
    admission_number: RecordIdentifier
    name_on_record: ShortText
    program: ShortText | None = None
    attendance_start_year: int | None = Field(default=None, ge=1900)
    attendance_end_year: int | None = Field(default=None, ge=1900)
    previous_names: list[ShortText] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_attendance(self):
        current_year = datetime.now(timezone.utc).year
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


class RecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    institution_id: int
    service_id: int
    admission_number: str
    name_on_record: str
    program: str | None
    attendance_start_year: int | None
    attendance_end_year: int | None
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
