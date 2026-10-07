from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.profile import UserProfile
from app.models.user import User
from app.schemas.profile import ProfileRead, ProfileWrite
from app.services.profiles import read_details, update_profile

router = APIRouter(tags=["personal details"])


@router.get("/me/profile", response_model=ProfileRead | None)
def my_profile(db: Session = Depends(get_db), user: User = Depends(get_verified_user)):
    return read_details(db.get(UserProfile, user.id))


@router.put("/me/profile", response_model=ProfileRead)
def save_my_profile(
    payload: ProfileWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    # The account lock serializes creation and edits without accepting another user's ID.
    db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    profile = update_profile(db, user, payload)
    db.commit()
    return read_details(profile)
