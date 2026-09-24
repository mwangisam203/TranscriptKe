from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime, model_validator

from app.schemas.orders import Reason, VersionInput
from app.services.orders import utcnow

QueueKind = Literal[
    "all",
    "overdue",
    "assignment_required",
    "holds",
    "awaiting_student",
    "payments",
    "deliveries",
    "cancellations",
    "ready",
    "follow_up",
    "missing_target",
]


class FollowUpInput(VersionInput):
    note: Reason
    follow_up_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def reasonable_follow_up(self):
        if (
            self.follow_up_at
            and not utcnow() < self.follow_up_at <= utcnow() + timedelta(days=365)
        ):
            raise ValueError("Follow-up must be in the future and within one year")
        return self
