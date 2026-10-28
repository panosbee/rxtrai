"""
RxTrail — CLOSED silence test: ZERO TOLERANCE for notifications after CLOSED.

This is the single most critical test in the system.
When a case is CLOSED (patient deceased or recovered):
  - ALL notifications referencing this case MUST be deleted
  - ALL notifications referencing its fulfilments MUST be deleted
  - ALL notifications referencing its prescriptions MUST be deleted
  - NO notification must ever be returned for a CLOSED case

The spec explicitly states:
  "Αν το σύστημα βγάλει 'κάλεσε για επανάληψη' σε σπίτι πενθούντων,
   ο χρήστης κάηκε στη γειτονιά."
"""
import pytest
from datetime import datetime, timezone
from bson import ObjectId

# Valid 24-char hex for mock IDs
MOCK_ID = "aaaaaaaaaaaaaaaaaaaaaaaa"
MOCK_OID = ObjectId(MOCK_ID)


class TestClosedSilence:
    """Verify that CLOSED cases generate ZERO notifications — ever."""

    @pytest.mark.asyncio
    async def test_close_purges_all_notifications(self):
        """When a case transitions to CLOSED, all its notifications are deleted."""
        # Build mock DB with an ACTIVE case that has notifications
        notif_deleted = []

        class MockNotifications:
            async def delete_many(self, query):
                notif_deleted.append(query)
                return _FakeResult(deleted=1)

            async def find_one(self, *args, **kwargs):
                return None

            async def insert_one(self, doc):
                pass

        db = _build_mock_db(case_status="ACTIVE")
        db.notifications = MockNotifications()

        from backend.core.state_machine import transition

        await transition(
            db=db,
            case_id=MOCK_ID,
            action="close",
            payload={"closed_reason": "DECEASED"},
            user_id="user1",
        )

        # Must have deleted notifications for the case
        assert len(notif_deleted) >= 1, "No delete_many was called on notifications!"
        case_deletes = [d for d in notif_deleted if d.get("ref_id") == MOCK_ID]
        assert len(case_deletes) >= 1, "Notifications for the case itself were NOT deleted"

    @pytest.mark.asyncio
    async def test_lost_purges_all_notifications(self):
        """When a case transitions to LOST, all its notifications are deleted."""
        notif_deleted = []

        class MockNotifications:
            async def delete_many(self, query):
                notif_deleted.append(query)
                return _FakeResult(deleted=1)

            async def find_one(self, *args, **kwargs):
                return None

            async def insert_one(self, doc):
                pass

        db = _build_mock_db(case_status="ASSESSED")  # ASSESSED can lose
        db.notifications = MockNotifications()

        from backend.core.state_machine import transition

        await transition(
            db=db,
            case_id=MOCK_ID,
            action="lose",
            payload={"lost_reason": "PRICE", "lost_reason_text": "Too expensive"},
            user_id="user1",
        )

        assert len(notif_deleted) >= 1, "No delete_many for LOST transition!"
        case_deletes = [d for d in notif_deleted if d.get("ref_id") == MOCK_ID]
        assert len(case_deletes) >= 1, "Notifications for the LOST case were NOT deleted"

    @pytest.mark.asyncio
    async def test_active_case_does_not_purge_notifications(self):
        """Non-terminal transitions must NOT delete notifications."""
        notif_deleted = []

        class MockNotifications:
            async def delete_many(self, query):
                notif_deleted.append(query)
                return _FakeResult(deleted=0)

            async def find_one(self, *args, **kwargs):
                return None

            async def insert_one(self, doc):
                pass

        db = _build_mock_db(case_status="NEW")
        db.notifications = MockNotifications()

        from backend.core.state_machine import transition

        await transition(
            db=db,
            case_id=MOCK_ID,
            action="assess",
            payload={"need_categories": ["WOUND"]},
            user_id="user1",
        )

        # ASSESS transition should NOT delete notifications
        assert len(notif_deleted) == 0, "Non-terminal transition deleted notifications!"

    @pytest.mark.asyncio
    async def test_closed_reason_is_required(self):
        """Closing a case without closed_reason must fail."""
        db = _build_mock_db(case_status="ACTIVE")

        from backend.core.state_machine import transition, TransitionError

        with pytest.raises(TransitionError):
            await transition(
                db=db,
                case_id=MOCK_ID,
                action="close",
                payload={},  # Missing closed_reason
                user_id="user1",
            )

    @pytest.mark.asyncio
    async def test_lost_reason_is_required(self):
        """Losing a case without lost_reason must fail (spec: 400)."""
        db = _build_mock_db(case_status="ACTIVE")

        from backend.core.state_machine import transition, TransitionError

        with pytest.raises(TransitionError):
            await transition(
                db=db,
                case_id=MOCK_ID,
                action="lose",
                payload={},  # Missing lost_reason
                user_id="user1",
            )

    @pytest.mark.asyncio
    async def test_no_transition_from_closed(self):
        """CLOSED is terminal — no transitions allowed."""
        db = _build_mock_db(case_status="CLOSED")

        from backend.core.state_machine import transition, TransitionError

        with pytest.raises(TransitionError):
            await transition(
                db=db,
                case_id=MOCK_ID,
                action="assess",
                payload={"need_categories": ["WOUND"]},
                user_id="user1",
            )

    @pytest.mark.asyncio
    async def test_no_transition_from_lost(self):
        """LOST is terminal — no transitions allowed."""
        db = _build_mock_db(case_status="LOST")

        from backend.core.state_machine import transition, TransitionError

        with pytest.raises(TransitionError):
            await transition(
                db=db,
                case_id=MOCK_ID,
                action="assess",
                payload={"need_categories": ["WOUND"]},
                user_id="user1",
            )

    @pytest.mark.asyncio
    async def test_closed_silence_integration(self):
        """Full integration: create ACTIVE case with a notification, close it,
        verify notification is deleted."""
        notif_store = []  # Simulate notification collection

        class MockNotifications:
            async def delete_many(self, query):
                nonlocal notif_store
                notif_store = [n for n in notif_store if not (
                    n.get("ref_id") == query.get("ref_id")
                    and n.get("org_id") == query.get("org_id")
                )]
                return _FakeResult(deleted=1)

            async def find_one(self, *args, **kwargs):
                return None

            async def insert_one(self, doc):
                notif_store.append(doc)
                return _FakeResult(inserted_id="n1")

        db = _build_mock_db(case_status="ACTIVE")
        db.notifications = MockNotifications()

        # First, simulate an existing REPEAT_DUE notification for the case
        notif_store.append({
            "_id": "notif1",
            "org_id": "org1",
            "kind": "REPEAT_DUE",
            "ref_type": "case",
            "ref_id": MOCK_ID,
            "due_at": datetime.now(timezone.utc),
            "seen": False,
            "dismissed": False,
        })

        from backend.core.state_machine import transition

        # Close the case
        await transition(
            db=db,
            case_id=MOCK_ID,
            action="close",
            payload={"closed_reason": "DECEASED"},
            user_id="user1",
        )

        # After closing, NO notifications referencing case1 should remain
        remaining = [n for n in notif_store if n.get("ref_id") == MOCK_ID]
        assert len(remaining) == 0, (
            f"ΜΗΔΕΝΙΚΗ ΑΝΟΧΗ: {len(remaining)} notifications still exist for CLOSED case. "
            "Αν το σύστημα βγάλει ειδοποίηση σε σπίτι πενθούντων, ο χρήστης κάηκε."
        )


# ── Mock DB helpers ──────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, matched=1, modified=1, inserted_id=None, deleted=1):
        self.matched_count = matched
        self.modified_count = modified
        self.inserted_id = inserted_id or MOCK_OID
        self.deleted_count = deleted


class MockColl:
    def __init__(self, initial_data=None):
        self._data = dict(initial_data) if initial_data else {}
        self._updates = {}
        self._history = []

    async def find_one(self, query, *args, **kwargs):
        qid = query.get("_id")
        if qid is not None and self._data.get("_id") == qid:
            return dict(self._data)
        if qid is not None:
            return None
        return dict(self._data) if self._data else None

    async def update_one(self, query, update, *args, **kwargs):
        if "$set" in update:
            self._data.update(update["$set"])
            self._updates.update(update["$set"])
        if "$push" in update:
            for k, v in update["$push"].items():
                lst = self._data.setdefault(k, [])
                lst.append(v)
                self._history.append(v)
        return _FakeResult(matched=1, modified=1)

    async def insert_one(self, doc):
        return _FakeResult(inserted_id=MOCK_OID)

    def find(self, *args, **kwargs):
        return _AsyncIterator([dict(self._data)] if self._data else [])

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, length):
        return []

    async def count_documents(self, query):
        return 0


class _AsyncIterator:
    def __init__(self, items):
        self._items = items
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    async def to_list(self, length):
        return self._items[:length]


def _build_mock_db(case_status="NEW"):
    db = type('MockDB', (), {})()
    db.cases = MockColl({
        "_id": MOCK_OID,
        "org_id": "org1",
        "status": case_status,
        "source_id": MOCK_ID,
        "alias": "Test",
        "status_history": [],
        "created_at": datetime.now(timezone.utc),
    })
    db.sources = MockColl()
    db.doctors = MockColl()
    db.patients = MockColl()
    db.notifications = MockColl()
    db.prescriptions = MockColl()
    db.fulfilments = MockColl()
    db.audit_log = MockColl()
    return db

