from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.access import MembershipRole
from app.schemas.auth import EmailRequest


class InvitationCreate(EmailRequest):
    role: MembershipRole = MembershipRole.STAFF


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    institution_id: int
    membership_role: MembershipRole
    expires_at: datetime
    consumed_at: datetime | None


class MembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    institution_id: int
    user_id: int
    role: MembershipRole
    is_active: bool
    created_at: datetime
