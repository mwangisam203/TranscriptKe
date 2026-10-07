"""Private recovery copies may be incomplete; submission validates the real order."""

import json
from typing import Annotated

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.orders import Order
from app.models.user import User
from app.models.workspace_draft import WorkspaceDraft
from app.schemas.common import StrictInput
from app.services.profiles import profile_cipher
from app.services.tokens import utcnow

router = APIRouter(prefix="/me/workspace-drafts", tags=["draft recovery"])
DraftKey = Annotated[
    str,
    Field(pattern=r"^(enrollment|checkout-profile|order-start|order-[1-9][0-9]{0,9})$"),
]


class RecoveryInput(StrictInput):
    expected_version: int = Field(ge=0)
    data: dict

    @model_validator(mode="after")
    def bounded_form_data(self):
        encoded = json.dumps(self.data, allow_nan=False)
        if len(encoded.encode()) > 32768:
            raise ValueError("Draft exceeds 32 KiB")

        def check(value, depth=0):
            if depth > 5:
                raise ValueError("Draft is too deeply nested")
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in {
                        "password",
                        "current_password",
                        "confirm_password",
                        "access_token",
                        "token",
                        "signature",
                        "file",
                    }:
                        raise ValueError(
                            "Passwords, tokens and signatures cannot be saved in drafts"
                        )
                    check(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    check(item, depth + 1)
            elif not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("Unsupported draft value")

        check(self.data)
        return self


def owned_key(db, user, key):
    if key.startswith("order-") and key != "order-start":
        order = db.get(Order, int(key[6:]))
        if order is None or order.user_id != user.id:
            raise HTTPException(404, "Order not found")
        if order.status != "draft":
            raise HTTPException(409, "Only draft orders have recovery copies")


def read(row):
    if row is None:
        return {"version": 0, "data": {}}
    try:
        data = json.loads(profile_cipher().decrypt(row.details_ciphertext.encode()))
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(503, "Draft unavailable; contact support") from exc
    return {"version": row.version, "data": data}


@router.get("/{key}")
def get_draft(
    key: DraftKey,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    owned_key(db, user, key)
    return read(db.get(WorkspaceDraft, (user.id, key)))


@router.put("/{key}")
def save_draft(
    key: DraftKey,
    payload: RecoveryInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    # Serialize writes per account, including first creation, and refuse stale tabs.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    owned_key(db, user, key)
    row = db.get(WorkspaceDraft, (user.id, key))
    if payload.expected_version != (row.version if row else 0):
        raise HTTPException(409, "Draft changed in another tab. Reload before editing.")
    encrypted = (
        profile_cipher()
        .encrypt(json.dumps(payload.data, allow_nan=False).encode())
        .decode()
    )
    if row is None:
        row = WorkspaceDraft(user_id=user.id, key=key, details_ciphertext=encrypted)
        db.add(row)
    else:
        row.details_ciphertext = encrypted
        row.version += 1
        row.updated_at = utcnow()
    db.commit()
    return {"version": row.version, "data": payload.data}
