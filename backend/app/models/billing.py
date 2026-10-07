from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InstitutionBilling(Base):
    __tablename__ = "institution_billing"
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
