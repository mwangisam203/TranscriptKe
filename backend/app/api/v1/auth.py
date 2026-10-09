from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    get_current_user,
    get_password_hash,
    get_verified_user,
    verify_password,
)
from app.db.session import get_db
from app.models.access import AccessEvent
from app.models.user import User, UserRole
from app.schemas.auth import (
    EmailRequest,
    Message,
    PasswordChange,
    PasswordReset,
    Token,
    TokenConfirmation,
    UserLogin,
    UserRead,
    UserRegister,
)
from app.services.mail import Mailer, get_mailer
from app.services.profiles import create_profile
from app.services.throttle import enforce_rate_limit
from app.services.tokens import (
    consume_token,
    find_token,
    invalidate_user_tokens,
    issue_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
GENERIC_EMAIL_MESSAGE = Message(
    message="If the account is eligible, an email with instructions will be sent."
)


@router.post("/register", response_model=UserRead, status_code=201)
def register_user(
    payload: UserRegister,
    request: Request,
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
):
    enforce_rate_limit(
        db, request, "register", payload.email, account_limit=5, ip_limit=30
    )
    if db.scalar(select(User.id).where(User.email == payload.email)) is not None:
        raise HTTPException(409, "A user with this email already exists")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=get_password_hash(payload.password),
        is_email_verified=False,
        role=UserRole.STUDENT,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A user with this email already exists") from exc
    if payload.profile is not None:
        create_profile(db, user, payload.profile)
    issue_token(
        db,
        mailer,
        purpose="email_verification",
        email=user.email,
        user_id=user.id,
        minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES,
    )
    db.commit()
    db.refresh(user)
    return user


def set_session_cookie(response: Response, token: str, request: Request):
    response.set_cookie(
        "transcriptske_session",
        token,
        httponly=True,
        secure=settings.APP_ENV == "production" or request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/session/refresh", response_model=Token)
def refresh_active_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    from urllib.parse import urlsplit

    if request.headers.get("origin"):
        origin = urlsplit(request.headers["origin"])
        if origin.scheme != request.url.scheme or origin.netloc != request.url.netloc:
            raise HTTPException(403, "A same-origin request is required")
    enforce_rate_limit(
        db, request, "session_refresh", str(user.id), account_limit=60, ip_limit=180
    )
    token = create_access_token(str(user.id), user.token_version)
    set_session_cookie(response, token, request)
    return Token(access_token=token)


@router.post("/login", response_model=Token)
def login_user(
    payload: UserLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    from urllib.parse import urlsplit

    if request.headers.get("origin"):
        origin = urlsplit(request.headers["origin"])
        if origin.scheme != request.url.scheme or origin.netloc != request.url.netloc:
            raise HTTPException(403, "A same-origin request is required")
    enforce_rate_limit(db, request, "login", payload.email)
    user = db.scalar(select(User).where(User.email == payload.email))
    correct = verify_password(
        payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if user is None or not correct:
        raise HTTPException(
            401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"}
        )
    if not user.is_email_verified:
        raise HTTPException(403, "Verify your email before signing in")
    token = create_access_token(str(user.id), user.token_version)
    set_session_cookie(response, token, request)
    return Token(access_token=token)


@router.get("/session", response_model=Token)
def restore_session(request: Request, user: User = Depends(get_current_user)):
    # Return the existing token without extending its expiry. It remains in JS memory.
    return Token(
        access_token=request.headers.get("authorization", "").removeprefix("Bearer ")
        or request.cookies.get("transcriptske_session")
    )


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/email-verifications", response_model=Message, status_code=202)
def request_verification(
    payload: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
):
    enforce_rate_limit(
        db, request, "email_verification", payload.email, account_limit=5, ip_limit=30
    )
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if user and not user.is_email_verified:
        issue_token(
            db,
            mailer,
            purpose="email_verification",
            email=user.email,
            user_id=user.id,
            minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES,
        )
    db.commit()
    return GENERIC_EMAIL_MESSAGE


@router.post("/email-verifications/confirm", response_model=Message)
def confirm_verification(
    payload: TokenConfirmation, request: Request, db: Session = Depends(get_db)
):
    enforce_rate_limit(db, request, "confirm_verification")
    record = find_token(db, payload.token, "email_verification")
    user = db.scalar(select(User).where(User.id == record.user_id).with_for_update())
    if user is None or user.email != record.email:
        raise HTTPException(400, "Invalid or expired code")
    consume_token(db, record)
    user.is_email_verified = True
    invalidate_user_tokens(db, user.id, "email_verification")
    db.add(AccessEvent(actor_id=user.id, action="email_verified", subject_id=user.id))
    db.commit()
    return Message(message="Email verified. You can now sign in.")


@router.post("/password-reset-requests", response_model=Message, status_code=202)
def request_password_reset(
    payload: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
):
    enforce_rate_limit(
        db, request, "password_reset", payload.email, account_limit=5, ip_limit=30
    )
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if user:
        issue_token(
            db,
            mailer,
            purpose="password_reset",
            email=user.email,
            user_id=user.id,
            minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES,
        )
    db.commit()
    return GENERIC_EMAIL_MESSAGE


@router.post("/password-resets", response_model=Message)
def reset_password(
    payload: PasswordReset, request: Request, db: Session = Depends(get_db)
):
    enforce_rate_limit(db, request, "confirm_reset")
    record = find_token(db, payload.token, "password_reset")
    user = db.scalar(select(User).where(User.id == record.user_id).with_for_update())
    if user is None or user.email != record.email:
        raise HTTPException(400, "Invalid or expired code")
    consume_token(db, record)
    user.password_hash = get_password_hash(payload.password)
    user.token_version += 1
    invalidate_user_tokens(db, user.id, "password_reset")
    db.add(AccessEvent(actor_id=user.id, action="password_reset", subject_id=user.id))
    db.commit()
    return Message(message="Password reset. Sign in with your new password.")


@router.put("/password", response_model=Message)
def change_password(
    payload: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    enforce_rate_limit(db, request, "password_change", current_user.email)
    user = db.scalar(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = get_password_hash(payload.password)
    user.token_version += 1
    invalidate_user_tokens(db, user.id, "password_reset")
    db.add(AccessEvent(actor_id=user.id, action="password_changed", subject_id=user.id))
    db.commit()
    return Message(message="Password changed. Sign in again on your devices.")


@router.post("/logout", response_model=Message)
def logout_all(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalar(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user.token_version += 1
    db.commit()
    response.delete_cookie("transcriptske_session", path="/api/v1")
    return Message(message="Signed out on all devices.")
