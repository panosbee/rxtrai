"""
RxTrail — Nightly scheduler (APScheduler).

Runs at 03:00 and 06:30 daily:
  1. Repeat clock: check ACTIVE cases for overdue repeats → AT_RISK
  2. SLA timers: create SLA notifications for overdue cases
  3. Queue scoring: generate daily queue cache
  4. Photo retention: delete expired photos
  5. Metrics refresh: doctor rx_count_90d, source aggregates
"""
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config import settings
from backend.core.clocks import at_risk_at, days_overdue, repeat_notify_at
from backend.db import get_db

scheduler = AsyncIOScheduler()


async def _get_org_settings(db) -> dict:
    """Load default org settings. In multi-tenant, load per-org — here we use the first org's."""
    org = await db.orgs.find_one()
    if not org:
        return {
            "repeat_notify_day": 25,
            "at_risk_day": 35,
            "new_sla_days": 5,
            "assessed_sla_days": 7,
            "prescribed_sla_days": 7,
            "photo_retention_days": 30,
        }
    return org.get("settings", {})


# ── Job 1: Repeat clock ─────────────────────────────────────────────────────

async def check_repeat_clock():
    """For every ACTIVE case: check if repeat is due or overdue → AT_RISK."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    orgs = await db.orgs.find().to_list(length=100)

    for org in orgs:
        org_id = org["_id"]
        oid_str = str(org_id)
        settings_dict = org.get("settings", {})
        at_risk_days = settings_dict.get("at_risk_day", 35)
        notify_day = settings_dict.get("repeat_notify_day", 25)

        async for case in db.cases.find({"org_id": oid_str, "status": "ACTIVE"}):
            # REQ-02: Skip no_repeat cases — they don't have repeat clocks
            if case.get("no_repeat"):
                continue
            if case.get("next_repeat_due") is None:
                continue

            # Find last fulfilment
            last_fulfil = await db.fulfilments.find_one(
                {"case_id": str(case["_id"]), "org_id": oid_str},
                sort=[("fulfilled_at", -1)],
            )
            if not last_fulfil:
                continue

            fulfilled_at = last_fulfil.get("fulfilled_at")
            if not fulfilled_at:
                continue

            # Check AT_RISK
            risk_date = at_risk_at(fulfilled_at, at_risk_days)
            if now >= risk_date:
                # Transition to AT_RISK
                from backend.core.state_machine import transition
                try:
                    await transition(
                        db=db,
                        case_id=str(case["_id"]),
                        action="at_risk",
                        payload={},
                        user_id="scheduler",
                    )
                except Exception:
                    pass  # Logged in audit_log

            # Check REPEAT_DUE notification
            notify_date = repeat_notify_at(fulfilled_at, 30, notify_day)  # 30-day default duration
            if now >= notify_date:
                # Check if notification already exists
                existing = await db.notifications.find_one({
                    "org_id": oid_str,
                    "ref_id": str(case["_id"]),
                    "kind": "REPEAT_DUE",
                    "seen": False,
                })
                if not existing:
                    await db.notifications.insert_one({
                        "org_id": oid_str,
                        "kind": "REPEAT_DUE",
                        "ref_type": "case",
                        "ref_id": str(case["_id"]),
                        "due_at": notify_date,
                        "seen": False,
                        "dismissed": False,
                        "created_at": now,
                    })


# ── Job 2: SLA timers ───────────────────────────────────────────────────────

async def check_sla_timers():
    """Create SLA notifications for NEW, ASSESSED, PRESCRIBED cases past their SLA."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    orgs = await db.orgs.find().to_list(length=100)

    for org in orgs:
        org_id = str(org["_id"])
        s = org.get("settings", {})
        new_sla = s.get("new_sla_days", 5)
        assessed_sla = s.get("assessed_sla_days", 7)
        prescribed_sla = s.get("prescribed_sla_days", 7)

        # NEW cases
        async for case in db.cases.find({"org_id": org_id, "status": "NEW"}):
            created = case.get("created_at")
            if created and days_overdue(created, now) >= new_sla:
                existing = await db.notifications.find_one({
                    "org_id": org_id, "ref_id": str(case["_id"]), "kind": "SLA_NEW", "seen": False,
                })
                if not existing:
                    await db.notifications.insert_one({
                        "org_id": org_id, "kind": "SLA_NEW", "ref_type": "case",
                        "ref_id": str(case["_id"]), "due_at": now,
                        "seen": False, "dismissed": False, "created_at": now,
                    })

        # ASSESSED cases
        async for case in db.cases.find({"org_id": org_id, "status": "ASSESSED"}):
            for entry in case.get("status_history", []):
                if entry.get("to") == "ASSESSED":
                    entered = entry.get("at")
                    if entered and days_overdue(entered, now) >= assessed_sla:
                        existing = await db.notifications.find_one({
                            "org_id": org_id, "ref_id": str(case["_id"]), "kind": "SLA_ASSESSED", "seen": False,
                        })
                        if not existing:
                            await db.notifications.insert_one({
                                "org_id": org_id, "kind": "SLA_ASSESSED", "ref_type": "case",
                                "ref_id": str(case["_id"]), "due_at": now,
                                "seen": False, "dismissed": False, "created_at": now,
                            })
                    break

        # PRESCRIBED cases without fulfilment
        async for case in db.cases.find({"org_id": org_id, "status": "PRESCRIBED"}):
            for entry in case.get("status_history", []):
                if entry.get("to") == "PRESCRIBED":
                    entered = entry.get("at")
                    if entered and days_overdue(entered, now) >= prescribed_sla:
                        existing = await db.notifications.find_one({
                            "org_id": org_id, "ref_id": str(case["_id"]), "kind": "SLA_PRESCRIBED", "seen": False,
                        })
                        if not existing:
                            await db.notifications.insert_one({
                                "org_id": org_id, "kind": "SLA_PRESCRIBED", "ref_type": "case",
                                "ref_id": str(case["_id"]), "due_at": now,
                                "seen": False, "dismissed": False, "created_at": now,
                            })
                    break


# ── Job 3: Photo retention ──────────────────────────────────────────────────

async def cleanup_expired_photos():
    """Delete prescription photos past their retention period."""
    import os
    db = await get_db()
    now = datetime.now(timezone.utc)

    async for presc in db.prescriptions.find({
        "photo_delete_at": {"$lte": now},
        "photo_path": {"$exists": True},
    }):
        path = presc.get("photo_path", "")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        await db.prescriptions.update_one(
            {"_id": presc["_id"]},
            {"$unset": {"photo_path": "", "photo_delete_at": ""}},
        )


# ── Job 4: Metrics refresh ──────────────────────────────────────────────────

async def refresh_metrics():
    """Refresh doctor rx_count_90d and source aggregates."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)

    orgs = await db.orgs.find().to_list(length=100)
    for org in orgs:
        org_id = str(org["_id"])

        # Doctor RX count (last 90 days)
        async for doctor in db.doctors.find({"org_id": org_id}):
            count = await db.prescriptions.count_documents({
                "org_id": org_id,
                "doctor_id": str(doctor["_id"]),
                "created_at": {"$gte": datetime.now(timezone.utc).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                ) - timedelta(days=90)},
            })
            await db.doctors.update_one(
                {"_id": doctor["_id"]},
                {"$set": {"rx_count_90d": count, "updated_at": now}},
            )

        # Source aggregates
        async for source in db.sources.find({"org_id": org_id}):
            active = await db.cases.count_documents({"org_id": org_id, "source_id": str(source["_id"]), "status": "ACTIVE"})
            lost = await db.cases.count_documents({"org_id": org_id, "source_id": str(source["_id"]), "status": "LOST"})
            closed = await db.cases.count_documents({"org_id": org_id, "source_id": str(source["_id"]), "status": "CLOSED"})
            brought = await db.cases.count_documents({"org_id": org_id, "source_id": str(source["_id"])})
            await db.sources.update_one(
                {"_id": source["_id"]},
                {"$set": {
                    "cases_brought": brought, "cases_active": active,
                    "cases_lost": lost, "cases_closed": closed, "updated_at": now,
                }},
            )


# ── Job 5: Morning push notifications ───────────────────────────────────────

async def send_morning_push():
    """Πρωινό push: επαναλήψεις που λήγουν + περιστατικά σε κίνδυνο.

    Στέλνεται σε κάθε χρήστη που έχει ενεργοποιήσει ειδοποιήσεις και
    έχει πραγματικά κάτι να δει σήμερα.
    """
    from backend.push import notify_org
    db = await get_db()
    now = datetime.now(timezone.utc)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    orgs = await db.orgs.find().to_list(length=100)
    for org in orgs:
        org_id = str(org["_id"])

        at_risk = await db.cases.count_documents({
            "org_id": org_id, "status": "AT_RISK", "no_repeat": {"$ne": True},
        })
        repeat_soon = await db.cases.count_documents({
            "org_id": org_id, "status": "ACTIVE", "no_repeat": {"$ne": True},
            "next_repeat_due": {"$lte": today_end},
        })
        unseen = await db.notifications.count_documents({
            "org_id": org_id, "seen": False, "dismissed": False,
            "due_at": {"$lte": now},
        })

        if not at_risk and not repeat_soon and not unseen:
            continue

        parts = []
        if at_risk:
            parts.append(f"{at_risk} σε κίνδυνο 🚨")
        if repeat_soon:
            parts.append(f"{repeat_soon} επανάληψη λήγει 🔁")
        if unseen:
            parts.append(f"{unseen} εκκρεμότητα")
        body = "Σήμερα: " + ", ".join(parts) + ". Άνοιξε το RxTrail."

        sent = await notify_org(
            db, org_id,
            title="RxTrail — Πλάνο ημέρας",
            body=body,
            url="/app",
        )
        if sent:
            import logging
            logging.getLogger("rxtrail.scheduler").info(
                f"Morning push sent to {sent} user(s) of org {org_id[:8]}: {body}")


# ── Startup / shutdown ──────────────────────────────────────────────────────

def start_scheduler():
    """Register all jobs with APScheduler and start."""
    # Repeat clock + SLA: 03:00 daily
    scheduler.add_job(
        check_repeat_clock, "cron", hour=3, minute=0,
        id="repeat_clock", replace_existing=True,
    )
    scheduler.add_job(
        check_sla_timers, "cron", hour=3, minute=5,
        id="sla_timers", replace_existing=True,
    )
    # Second morning check: 06:30
    scheduler.add_job(
        check_repeat_clock, "cron", hour=6, minute=30,
        id="repeat_clock_morning", replace_existing=True,
    )
    scheduler.add_job(
        check_sla_timers, "cron", hour=6, minute=35,
        id="sla_timers_morning", replace_existing=True,
    )
    # Photo cleanup: 04:00 daily
    scheduler.add_job(
        cleanup_expired_photos, "cron", hour=4, minute=0,
        id="photo_cleanup", replace_existing=True,
    )
    # Metrics refresh: 02:00 Sundays
    scheduler.add_job(
        refresh_metrics, "cron", day_of_week="sun", hour=2, minute=0,
        id="metrics_refresh", replace_existing=True,
    )
    # Morning push: 06:45 (≈08:45 Ελλάδα)
    scheduler.add_job(
        send_morning_push, "cron", hour=6, minute=45,
        id="morning_push", replace_existing=True,
    )

    scheduler.start()


def stop_scheduler():
    """Shutdown APScheduler gracefully."""
    scheduler.shutdown(wait=False)
