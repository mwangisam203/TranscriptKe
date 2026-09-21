from typing import Literal

from pydantic import Field

from app.schemas.academic import Id
from app.schemas.orders import Reason, VersionInput


class AssignmentInput(VersionInput):
    user_id: Id | None


class HoldInput(VersionInput):
    category: Literal["academic", "identity", "financial", "administrative"]
    student_message: Reason
    internal_note: Reason


class ResolutionInput(VersionInput):
    student_message: Reason
    internal_note: Reason


class ReviewInput(ResolutionInput):
    decision: Literal["approve", "reject", "ready", "reopen"]


class ReleaseConfirmation(VersionInput):
    confirmed: bool = Field(strict=True)
    internal_note: Reason
