from pydantic import BaseModel, ConfigDict


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
