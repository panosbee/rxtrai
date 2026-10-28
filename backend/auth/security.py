"""
RxTrail — Auth security primitives: bcrypt hashing, JWT create/verify.
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from backend.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, org_id: str) -> str:
    payload = {
        "sub": user_id,
        "org": org_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_access_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_access_token(token: str) -> dict:
    """Returns decoded payload. Raises JWT errors on invalid/expired token."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
