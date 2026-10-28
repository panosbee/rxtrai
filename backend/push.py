"""
RxTrail — Web Push (VAPID) αποστολή ειδοποιήσεων.

- Ο χρήστης εγγράφεται από το browser (service worker) → push_subs collection.
- Ο scheduler στέλνει push για επαναλήψεις/κίνδυνο κάθε πρωί.
- Αν μια συνδρομή είναι νεκρή (410/404), σβήνεται.
"""
import asyncio
import json
import logging

from backend.config import settings

logger = logging.getLogger("rxtrail.push")

PUSH_TITLE_MAX = 60
PUSH_BODY_MAX = 160


async def send_push(subscription: dict, title: str, body: str, url: str = "/app") -> bool:
    """Στέλνει Web Push σε μία συνδρομή. Επιστρέφει False αν η συνδρομή είναι νεκρή."""
    if not settings.vapid_private_key:
        logger.warning("VAPID keys missing — push disabled")
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("pywebpush not installed")
        return False

    payload = json.dumps({
        "title": title[:PUSH_TITLE_MAX],
        "body": body[:PUSH_BODY_MAX],
        "url": url,
        "tag": "rxtrail",
    })

    def _send():
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            timeout=15,
        )

    try:
        await asyncio.get_event_loop().run_in_executor(None, _send)
        return True
    except WebPushException as e:
        code = getattr(e.response, "status_code", 0) if getattr(e, "response", None) is not None else 0
        if code in (404, 410):
            logger.info(f"Push subscription gone ({code}) — will be removed")
            return False
        logger.warning(f"Push failed ({code}): {e}")
        return True  # κρατάμε τη συνδρομή — το σφάλμα δεν είναι μόνιμο
    except Exception as e:
        logger.exception(f"Push exception: {e}")
        return True


async def notify_user(db, user_id: str, title: str, body: str, url: str = "/app") -> bool:
    """Στέλνει push στον χρήστη αν έχει εγγραφεί. Διαγράφει νεκρές συνδρομές."""
    sub = await db.push_subs.find_one({"user_id": user_id})
    if not sub or not sub.get("subscription"):
        return False
    ok = await send_push(sub["subscription"], title, body, url)
    if not ok:
        await db.push_subs.delete_one({"user_id": user_id})
    return ok


async def notify_org(db, org_id: str, title: str, body: str, url: str = "/app") -> int:
    """Στέλνει push σε ΟΛΟΥΣ τους χρήστες ενός οργανισμού. Επιστρέφει πλήθος σταλμένων."""
    sent = 0
    async for u in db.users.find({"org_id": org_id, "is_active": True}, {"_id": 1}):
        if await notify_user(db, str(u["_id"]), title, body, url):
            sent += 1
    return sent
