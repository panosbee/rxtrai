"""
RxTrail — Auth router: POST /auth/login, GET /auth/me, POST /auth/change-password.
Η εγγραφή (POST /auth/register) είναι ΚΛΕΙΣΤΗ από default — οι χρήστες
δημιουργούνται μόνο από τον admin (backend.routers.admin).
"""
import time
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from backend.auth.deps import get_current_user, get_org
from backend.auth.security import create_access_token, hash_password, verify_password
from backend.config import settings
from backend.db import get_db, scoped

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Απλό in-memory rate limit για login (anti-bruteforce) ──────────────────
_login_attempts: dict[str, list[float]] = {}
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW_SECONDS = 600  # 10 λεπτά


def _login_limited(key: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    return len(attempts) >= LOGIN_MAX_FAILS


def _login_fail(key: str) -> None:
    _login_attempts.setdefault(key, []).append(time.time())


# ── Request / Response schemas ──────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    name: str = Field(..., min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    user: dict


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    org_id: str


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest):
    """Κλειστή εγγραφή: επιτρέπεται μόνο αν allow_open_registration=True.

    Κανονικά οι χρήστες δημιουργούνται από τον admin.
    """
    if not settings.allow_open_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Η εγγραφή είναι κλειστή. Ο λογαριασμός δημιουργείται από τον διαχειριστή.",
        )

    db = await get_db()

    # Check email uniqueness globally
    existing = await db.users.find_one({"email": req.email})
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Create org
    org_result = await db.orgs.insert_one({
        "name": req.name,
        "settings": {
            "repeat_notify_day": 25,
            "at_risk_day": 35,
            "new_sla_days": 5,
            "assessed_sla_days": 7,
            "prescribed_sla_days": 7,
            "photo_retention_days": 30,
        },
        "created_at": datetime.now(timezone.utc),
    })
    org_id = str(org_result.inserted_id)

    # Create user
    user_result = await db.users.insert_one({
        "email": req.email,
        "password_hash": hash_password(req.password),
        "name": req.name,
        "org_id": org_id,
        "role": "owner",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })
    user_id = str(user_result.inserted_id)

    token = create_access_token(user_id=user_id, org_id=org_id)
    return AuthResponse(access_token=token, user={
        "id": user_id,
        "email": req.email,
        "name": req.name,
        "role": "owner",
        "org_id": org_id,
    })


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    """Authenticate and return JWT."""
    db = await get_db()
    email = req.email.lower()
    client_ip = request.client.host if request.client else "?"
    limit_key = f"{client_ip}:{email}"

    if _login_limited(limit_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Πολλές αποτυχημένες προσπάθειες. Δοκίμασε ξανά σε 10 λεπτά.")

    user = await db.users.find_one({"email": email})
    if user is None or not verify_password(req.password, user.get("password_hash", "")):
        _login_fail(limit_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not user.get("is_active", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")

    _login_attempts.pop(limit_key, None)
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login_at": datetime.now(timezone.utc)}},
    )

    token = create_access_token(user_id=str(user["_id"]), org_id=user["org_id"])
    return AuthResponse(access_token=token, user={
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user.get("role", "member"),
        "org_id": user["org_id"],
        "is_admin": bool(user.get("is_admin")) or user["email"] == settings.admin_email.lower(),
        "must_change_password": bool(user.get("must_change_password", False)),
    })


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """Ο χρήστης αλλάζει τον κωδικό του (απαιτεί τον τρέχοντα)."""
    db = await get_db()

    if not verify_password(req.current_password, current_user.get("password_hash", "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ο τρέχων κωδικός δεν είναι σωστός")

    await db.users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {
            "password_hash": hash_password(req.new_password),
            "must_change_password": False,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {"ok": True, "message": "Ο κωδικός άλλαξε επιτυχώς."}


@router.get("/me")
async def me(request: Request, current_user: dict = Depends(get_current_user)):
    """Return current authenticated user."""
    return {
        "id": str(current_user["_id"]),
        "email": current_user["email"],
        "name": current_user["name"],
        "role": current_user.get("role", "member"),
        "org_id": current_user["org_id"],
        "is_admin": bool(current_user.get("is_admin")) or current_user["email"] == settings.admin_email.lower(),
        "must_change_password": bool(current_user.get("must_change_password", False)),
    }


@router.get("/colleagues")
async def colleagues(current_user: dict = Depends(get_current_user)):
    """Λίστα συναδέλφων (για παράδοση περιστατικού): id, name, email — χωρίς ευαίσθητα στοιχεία."""
    db = await get_db()
    items = []
    async for u in db.users.find({"is_active": True}, {"name": 1, "email": 1}):
        if str(u["_id"]) == str(current_user["_id"]):
            continue
        items.append({
            "id": str(u["_id"]),
            "name": u.get("name", ""),
            "email": u.get("email", ""),
        })
    items.sort(key=lambda x: (x["name"] or x["email"]).lower())
    return {"items": items, "total": len(items)}
