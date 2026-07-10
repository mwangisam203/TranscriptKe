from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InstitutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    country: str
    is_active: bool
    created_at: datetime
