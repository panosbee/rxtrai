"""
RxTrail — Auth dependencies for FastAPI: get_current_user, get_org.
"""
from bson import ObjectId
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.security import verify_access_token
from backend.db import get_db

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
):
    """Extract and validate JWT. Accepts Bearer header OR ?token= query param (for img/iframe previews)."""
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        # Allow token via query param (needed for <img>/<iframe> preview of protected files)
        token = request.query_params.get("token")

    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token required")

    try:
        payload = verify_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = payload.get("sub")
    org_id = payload.get("org")
    if not user_id or not org_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")

    db = await get_db()
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if user is None or not user.get("is_active", False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Attach to request state for downstream use
    request.state.user_id = user_id
    request.state.org_id = org_id
    request.state.user = user
    return user


async def get_org(request: Request) -> str:
    """Convenience: returns org_id from request state. Requires get_current_user first."""
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return org_id
