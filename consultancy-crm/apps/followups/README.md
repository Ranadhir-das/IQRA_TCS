# Follow-up lifecycle storage

Follow-ups are created and changed through `apps.followups.services`. Ordinary
`FollowUp.objects.create()`, instance `save()`, queryset updates, bulk writes,
and deletion are rejected. The same ordinary ORM protections apply to event
creation and mutation. Lifecycle services use an instance-scoped `ContextVar`
authorization for one exact save; the token is reset in a `finally` block on
success or failure. The internal helper names are implementation conventions,
not a security boundary against arbitrary Python code running in the process.

Each lifecycle service wraps the FollowUp write and its corresponding event
insert in one transaction. A failed write or event insert rolls the transaction
back. On PostgreSQL, migration `0001_initial` also installs a trigger that
rejects direct SQL `UPDATE` and `DELETE` statements against the event table.
Reversing the migration drops the trigger and its function before dropping the
tables. The trigger does not restrict privileged `INSERT` or `TRUNCATE`
operations; database administrators and privileged direct SQL remain outside
the ordinary ORM write guard.

On database backends other than PostgreSQL, the migration skips the trigger;
model and manager protections still apply, but privileged direct SQL can alter
or delete event rows. The application targets PostgreSQL for transaction and
locking guarantees. These controls ensure the approved application write paths
pair each successful lifecycle change with one audit event; they do not claim
protection against database administrators or arbitrary privileged SQL.

The initial lifecycle does not transfer ownership when a lead is reassigned or
an employee is deactivated. An active administrator can cancel a pending
follow-up for a deactivated employee with a required reason. Ownership remains
unchanged and the cancellation is recorded as an event.

## Follow-up REST API (`/api/v1/follow-ups/`)

- `POST /api/v1/follow-ups/` — Create a follow-up for an assigned active lead (active non-superuser `EMPLOYEE` only; requires `Idempotency-Key` UUID header).
- `GET /api/v1/follow-ups/` — List follow-ups (employee sees own; administrator sees organization-wide with `owner_id`, `owner_username_snapshot`, and `owner_name_snapshot`). Supports `status`, `lead_id`, `scheduled_from`, `scheduled_to`, `due_state` (`today`, `overdue`, `upcoming` in `Asia/Kolkata`), and admin-only `employee_id`.
- `GET /api/v1/follow-ups/<pk>/` — Retrieve follow-up detail.
- `POST /api/v1/follow-ups/<pk>/reschedule/` — Reschedule a pending follow-up (owner employee only; requires `Idempotency-Key` UUID header).
- `POST /api/v1/follow-ups/<pk>/complete/` — Mark a pending follow-up completed (owner employee only; empty JSON body `{}`).
- `POST /api/v1/follow-ups/<pk>/cancel/` — Cancel a pending follow-up (owner employee with optional reason, or administrator with required nonblank reason when the owner employee is inactive).
- `GET /api/v1/follow-ups/<pk>/events/` — Administrator-only chronological audit history of `FollowUpEvent` records (`id`, `event_type`, `actor_id`, `actor_username_snapshot`, `actor_name_snapshot`, `previous_scheduled_at`, `new_scheduled_at`, `previous_status`, `new_status`, `reason`, `created_at`). Internal `idempotency_key` and `request_fingerprint` fields are never exposed.
