import threading
import uuid
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models.query import QuerySet
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.leads.models import Lead
from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import (
    FollowUpConflict,
    FollowUpIdempotencyConflict,
    cancel_follow_up,
    complete_follow_up,
    create_follow_up,
    reschedule_follow_up,
)


@skipUnless(connection.vendor == "postgresql", "Concurrent follow-up transitions require PostgreSQL")
class FollowUpTransitionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create(
            username="followup_concurrent_owner",
            role=User.Role.EMPLOYEE,
        )
        self.lead = Lead.objects.create(
            name="Concurrent Follow-up Lead",
            phone="9876508771",
            assigned_to=self.owner,
        )
        self.follow_up = create_follow_up(
            actor=self.owner,
            lead_id=self.lead.pk,
            scheduled_at=timezone.now(),
        )

    def test_concurrent_completion_and_cancellation_have_one_winner(self):
        ready = threading.Barrier(2)
        both_at_lock = threading.Event()
        first_lock_returned = threading.Event()
        release_first = threading.Event()
        guard = threading.Lock()
        entered = set()
        returned = []
        thread_local = threading.local()
        outcomes = {}
        failures = {}
        worker_connections = {}
        original_get = QuerySet.get

        def synchronized_get(queryset, *args, **kwargs):
            thread_name = threading.current_thread().name
            is_followup_lock = (
                queryset.model is FollowUp
                and queryset.query.select_for_update
                and not getattr(thread_local, "followup_lock_seen", False)
            )
            if not is_followup_lock:
                return original_get(queryset, *args, **kwargs)

            ready.wait(timeout=5)
            with guard:
                entered.add(thread_name)
                if len(entered) == 2:
                    both_at_lock.set()

            follow_up = original_get(queryset, *args, **kwargs)
            with guard:
                returned.append(thread_name)
                is_first = not first_lock_returned.is_set()
                if is_first:
                    first_lock_returned.set()
            thread_local.followup_lock_seen = True
            if is_first and not release_first.wait(timeout=5):
                raise TimeoutError("Timed out waiting to release the first follow-up lock")
            return follow_up

        def worker(action):
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                worker_connections[action] = connections["default"].connection
                if action == "complete":
                    result = complete_follow_up(
                        follow_up_id=self.follow_up.pk,
                        actor=self.owner,
                    )
                else:
                    result = cancel_follow_up(
                        follow_up_id=self.follow_up.pk,
                        actor=self.owner,
                    )
                outcomes[action] = ("success", result.status)
            except FollowUpConflict:
                outcomes[action] = ("conflict", None)
            except BaseException as exc:
                failures[action] = exc
            finally:
                connections.close_all()

        workers = [
            threading.Thread(target=worker, args=(action,), name=f"followup-{action}", daemon=True)
            for action in ("complete", "cancel")
        ]
        try:
            with patch.object(QuerySet, "get", new=synchronized_get):
                for thread in workers:
                    thread.start()
                self.assertTrue(first_lock_returned.wait(timeout=5))
                self.assertTrue(both_at_lock.wait(timeout=5))
                self.assertFalse(release_first.wait(timeout=0.2))
                self.assertEqual(len(returned), 1)
        finally:
            release_first.set()
            for thread in workers:
                if thread.ident is not None:
                    thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertFalse(failures, f"Unexpected worker errors: {failures!r}")
        self.assertEqual(len(worker_connections), 2)
        self.assertIsNot(worker_connections["complete"], worker_connections["cancel"])
        self.assertCountEqual([result[0] for result in outcomes.values()], ["success", "conflict"])

        self.follow_up.refresh_from_db()
        self.assertIn(
            self.follow_up.status,
            {FollowUp.Status.COMPLETED, FollowUp.Status.CANCELLED},
        )
        self.assertEqual(
            FollowUpEvent.objects.filter(
                follow_up=self.follow_up,
                event_type__in=("COMPLETED", "CANCELLED"),
            ).count(),
            1,
        )
        if self.follow_up.status == FollowUp.Status.COMPLETED:
            self.assertIsNotNone(self.follow_up.completed_at)
            self.assertIsNone(self.follow_up.cancelled_at)
        else:
            self.assertIsNone(self.follow_up.completed_at)
            self.assertIsNotNone(self.follow_up.cancelled_at)

    def test_concurrent_identical_create_requests_produce_one_record_and_one_event(self):
        idempotency_key = uuid.uuid4()
        scheduled_at = timezone.now() + timedelta(days=1)
        ready = threading.Barrier(2)
        both_at_lock = threading.Event()
        first_lock_returned = threading.Event()
        release_first = threading.Event()
        guard = threading.Lock()
        entered = set()
        returned = []
        thread_local = threading.local()
        results = {}
        failures = {}
        worker_connections = {}
        original_get = QuerySet.get

        def synchronized_get(queryset, *args, **kwargs):
            thread_name = threading.current_thread().name
            is_lead_lock = (
                queryset.model is Lead
                and queryset.query.select_for_update
                and not getattr(thread_local, "lead_lock_seen", False)
            )
            if not is_lead_lock:
                return original_get(queryset, *args, **kwargs)

            ready.wait(timeout=5)
            with guard:
                entered.add(thread_name)
                if len(entered) == 2:
                    both_at_lock.set()

            lead = original_get(queryset, *args, **kwargs)
            with guard:
                returned.append(thread_name)
                is_first = not first_lock_returned.is_set()
                if is_first:
                    first_lock_returned.set()
            thread_local.lead_lock_seen = True
            if is_first and not release_first.wait(timeout=5):
                raise TimeoutError("Timed out waiting to release the first lead lock")
            return lead

        def worker(label):
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                worker_connections[label] = connections["default"].connection
                follow_up = create_follow_up(
                    actor=self.owner,
                    lead_id=self.lead.pk,
                    scheduled_at=scheduled_at,
                    notes="Concurrent create",
                    idempotency_key=idempotency_key,
                )
                results[label] = (follow_up.pk, getattr(follow_up, "_created", None))
            except BaseException as exc:
                failures[label] = exc
            finally:
                connections.close_all()

        workers = [
            threading.Thread(target=worker, args=(label,), name=label, daemon=True)
            for label in ("create-1", "create-2")
        ]
        try:
            with patch.object(QuerySet, "get", new=synchronized_get):
                for thread in workers:
                    thread.start()
                self.assertTrue(first_lock_returned.wait(timeout=5))
                self.assertTrue(both_at_lock.wait(timeout=5))
                self.assertFalse(release_first.wait(timeout=0.2))
                self.assertEqual(len(returned), 1)
        finally:
            release_first.set()
            for thread in workers:
                if thread.ident is not None:
                    thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertFalse(failures, f"Unexpected worker errors: {failures!r}")
        self.assertIsNot(worker_connections["create-1"], worker_connections["create-2"])
        pks = [pk for pk, _created in results.values()]
        created_flags = [created for _pk, created in results.values()]
        self.assertEqual(pks[0], pks[1])
        self.assertCountEqual(created_flags, [True, False])
        self.assertEqual(
            FollowUpEvent.objects.filter(
                actor=self.owner,
                idempotency_key=idempotency_key,
            ).count(),
            1,
        )
        self.assertEqual(
            FollowUp.objects.filter(pk=pks[0]).count(),
            1,
        )

    def test_concurrent_reschedule_requests_serialize_correctly(self):
        shared_key = uuid.uuid4()
        next_schedule = timezone.now() + timedelta(days=2)
        ready = threading.Barrier(2)
        both_at_lock = threading.Event()
        first_lock_returned = threading.Event()
        release_first = threading.Event()
        guard = threading.Lock()
        entered = set()
        returned = []
        thread_local = threading.local()
        results = {}
        failures = {}
        original_get = QuerySet.get

        def synchronized_get(queryset, *args, **kwargs):
            thread_name = threading.current_thread().name
            is_followup_lock = (
                queryset.model is FollowUp
                and queryset.query.select_for_update
                and not getattr(thread_local, "followup_lock_seen", False)
            )
            if not is_followup_lock:
                return original_get(queryset, *args, **kwargs)

            ready.wait(timeout=5)
            with guard:
                entered.add(thread_name)
                if len(entered) == 2:
                    both_at_lock.set()

            follow_up = original_get(queryset, *args, **kwargs)
            with guard:
                returned.append(thread_name)
                is_first = not first_lock_returned.is_set()
                if is_first:
                    first_lock_returned.set()
            thread_local.followup_lock_seen = True
            if is_first and not release_first.wait(timeout=5):
                raise TimeoutError("Timed out waiting to release the first follow-up lock")
            return follow_up

        def worker(label, notes):
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                rescheduled = reschedule_follow_up(
                    follow_up_id=self.follow_up.pk,
                    actor=self.owner,
                    scheduled_at=next_schedule,
                    notes=notes,
                    reason="Concurrent reschedule",
                    idempotency_key=shared_key,
                )
                results[label] = ("ok", rescheduled.scheduled_at)
            except FollowUpIdempotencyConflict:
                results[label] = ("conflict", None)
            except BaseException as exc:
                failures[label] = exc
            finally:
                connections.close_all()

        # First test identical concurrent reschedules -> both succeed, 1 RESCHEDULED event
        workers = [
            threading.Thread(
                target=worker,
                args=(label, "Same notes"),
                name=label,
                daemon=True,
            )
            for label in ("resched-1", "resched-2")
        ]
        try:
            with patch.object(QuerySet, "get", new=synchronized_get):
                for thread in workers:
                    thread.start()
                self.assertTrue(first_lock_returned.wait(timeout=5))
                self.assertTrue(both_at_lock.wait(timeout=5))
                self.assertFalse(release_first.wait(timeout=0.2))
                self.assertEqual(len(returned), 1)
        finally:
            release_first.set()
            for thread in workers:
                if thread.ident is not None:
                    thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertFalse(failures, f"Unexpected worker errors: {failures!r}")
        self.assertEqual([status for status, _ in results.values()], ["ok", "ok"])
        self.assertEqual(
            FollowUpEvent.objects.filter(
                follow_up=self.follow_up,
                event_type=FollowUpEvent.EventType.RESCHEDULED,
            ).count(),
            1,
        )
