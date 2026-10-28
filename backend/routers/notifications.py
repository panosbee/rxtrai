"""
RxTrail — Notifications router: GET (unread) + dismiss.
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


@router.get("")
async def list_notifications(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    seen: bool = Query(False),
    kind: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    """List notifications. Default: unseen only."""
    db = await get_db()
    filt: dict = {"org_id": org_id, "dismissed": False}
    if not seen:
        filt["seen"] = False
    if kind:
        filt["kind"] = kind

    cursor = db.notifications.find(filt).sort("due_at", 1).limit(limit)
    items = [_serialize(d) async for d in cursor]
    total = await db.notifications.count_documents(filt)
    return {"items": items, "total": total}


@router.get("/count")
async def notification_count(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Return count of unseen, undismissed notifications that are due."""
    db = await get_db()
    count = await db.notifications.count_documents({
        "org_id": org_id,
        "seen": False,
        "dismissed": False,
        "due_at": {"$lte": datetime.now(timezone.utc)},
    })
    return {"count": count}


@router.post("/{notification_id}/dismiss")
async def dismiss_notification(
    notification_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Mark a notification as dismissed."""
    db = await get_db()
    result = await db.notifications.update_one(
        {"_id": ObjectId(notification_id), "org_id": org_id},
        {"$set": {"dismissed": True, "seen": True, "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Notification not found")
    return {"ok": True}


@router.post("/mark-seen")
async def mark_all_seen(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Mark all notifications as seen."""
    db = await get_db()
    await db.notifications.update_many(
        {"org_id": org_id, "seen": False},
        {"$set": {"seen": True, "updated_at": datetime.now(timezone.utc)}},
    )
    return {"ok": True}


# ── Web Push (VAPID) ──────────────────────────────────────────────────────

@router.get("/push/public-key")
async def push_public_key(current_user: dict = Depends(get_current_user)):
    """Το VAPID δημόσιο κλειδί (b64url) για pushManager.subscribe()."""
    from backend.config import settings
    if not settings.vapid_public_key_b64url:
        raise HTTPException(503, "Push notifications are not configured")
    return {"public_key": settings.vapid_public_key_b64url}


@router.post("/push/subscribe")
async def push_subscribe(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Αποθήκευση συνδρομής push του χρήστη."""
    db = await get_db()
    body = await request.json()
    subscription = body.get("subscription")
    if not subscription or not subscription.get("endpoint"):
        raise HTTPException(400, "Invalid subscription")
    await db.push_subs.update_one(
        {"user_id": str(current_user["_id"])},
        {"$set": {
            "user_id": str(current_user["_id"]),
            "subscription": subscription,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"ok": True, "message": "Ειδοποιήσεις ενεργοποιήθηκαν 🔔"}


@router.post("/push/unsubscribe")
async def push_unsubscribe(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Διαγραφή συνδρομής push."""
    db = await get_db()
    await db.push_subs.delete_one({"user_id": str(current_user["_id"])})
    return {"ok": True, "message": "Ειδοποιήσεις απενεργοποιήθηκαν"}


@router.get("/push/status")
async def push_status(current_user: dict = Depends(get_current_user)):
    """Έχει ο χρήστης ενεργό push;"""
    db = await get_db()
    sub = await db.push_subs.find_one({"user_id": str(current_user["_id"])})
    return {"enabled": bool(sub)}


@router.post("/push/test")
async def push_test(current_user: dict = Depends(get_current_user)):
    """Στέλνει δοκιμαστικό push στον ίδιο τον χρήστη."""
    from backend.push import notify_user
    db = await get_db()
    sent = await notify_user(
        db, str(current_user["_id"]),
        title="RxTrail 🔔 Δοκιμή",
        body="Οι ειδοποιήσεις δουλεύουν! Θα σε ειδοποιούμε για επαναλήψεις που λήγουν.",
    )
    if not sent:
        raise HTTPException(400, "Δεν έχεις ενεργές ειδοποιήσεις — ενεργοποίησέ τες πρώτα")
    return {"ok": True, "message": "Στάλθηκε δοκιμαστική ειδοποίηση 🔔"}
