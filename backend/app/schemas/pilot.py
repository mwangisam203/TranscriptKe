from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from app.schemas.academic import ShortText, clean_code, clean_text
from app.schemas.auth import StrictInput

EvidenceNote = Annotated[str, BeforeValidator(clean_text), Field(max_length=2000)]
Reason = Annotated[
    str, BeforeValidator(clean_text), Field(min_length=20, max_length=4000)
]


class AcceptanceEvidence(StrictInput):
    authority: EvidenceNote = ""
    privacy: EvidenceNote = ""
    staff_training: EvidenceNote = ""
    support_ownership: EvidenceNote = ""
    payment_acceptance: EvidenceNote = ""
    delivery_acceptance: EvidenceNote = ""
    recovery_and_monitoring: EvidenceNote = ""


class InstitutionCreate(StrictInput):
    name: ShortText
    code: Annotated[
        str,
        BeforeValidator(clean_code),
        Field(min_length=1, max_length=50, pattern=r"^[A-Z0-9_-]+$"),
    ]
    country: Annotated[
        str, BeforeValidator(clean_text), Field(min_length=1, max_length=100)
    ] = "Kenya"


class OnboardingWrite(StrictInput):
    expected_version: int = Field(ge=0, strict=True)
    evidence: AcceptanceEvidence


class OnboardingSubmit(StrictInput):
    expected_version: int = Field(ge=1, strict=True)


class OnboardingReview(OnboardingSubmit):
    decision: Literal["approved", "changes_requested"]
    reason: Reason


class PilotWindow(StrictInput):
    mode: Literal["demo", "live"]
    window_start: AwareDatetime
    window_end: AwareDatetime

    @model_validator(mode="after")
    def valid_window(self):
        if not self.window_start < self.window_end <= datetime.now(timezone.utc):
            raise ValueError(
                "Use a completed window with start before end and no future dates"
            )
        if self.window_end - self.window_start > timedelta(days=366):
            raise ValueError("A pilot cohort window cannot exceed 366 days")
        return self


class EvaluationCreate(PilotWindow):
    evidence: AcceptanceEvidence
    findings: Reason


class EvaluationReview(OnboardingSubmit):
    decision: Literal["continue_pilot", "rework", "expand"]
    reason: Reason
