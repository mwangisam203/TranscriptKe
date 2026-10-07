from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)
# Unknown accounts still perform the same password-hash operation.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-user-password", bcrypt.gensalt()).decode()


def verify_password(plain_password: str, password_hash: str) -> bool:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > 72:
        bcrypt.checkpw(b"invalid-long-password", DUMMY_PASSWORD_HASH.encode())
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(subject: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "ver": token_version,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    error = HTTPException(
        401, "Could not validate credentials", headers={"WWW-Authenticate": "Bearer"}
    )
    token = (
        credentials.credentials
        if credentials
        else request.cookies.get("transcriptske_session")
    )
    if not token:
        raise error
    if credentials is None and request.method not in ("GET", "HEAD", "OPTIONS"):
        from urllib.parse import urlsplit

        origin = urlsplit(request.headers.get("origin", ""))
        if origin.scheme != request.url.scheme or origin.netloc != request.url.netloc:
            raise HTTPException(403, "A same-origin request is required")
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require_exp": True, "require_sub": True, "require_iat": True},
        )
        user_id = int(payload["sub"])
        if not 1 <= user_id <= 2_147_483_647:
            raise error
        if payload.get("type") != "access" or type(payload.get("ver")) is not int:
            raise error
    except (JWTError, ValueError, TypeError, KeyError) as exc:
        raise error from exc
    user = db.get(User, user_id)
    if user is None or user.token_version != payload["ver"]:
        raise error
    return user


def get_verified_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_email_verified:
        raise HTTPException(403, "Verify your email before continuing")
    return user
