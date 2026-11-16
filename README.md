# IQRA TCS Calling CRM — Backend API Documentation

Independent Django + Django REST Framework + PostgreSQL backend for the **IQRA TCS Calling CRM** (`iqracentral.com`).

---

## 1. Architecture & Role Model

- **Roles**:
  - `ADMIN`: Organization-wide administrative access. Any user with `role=ADMIN` or `is_superuser=True` has administrator privileges (`user.is_admin == True`).
  - `EMPLOYEE`: Ordinary calling employee (`role=EMPLOYEE` and `is_superuser=False`, `user.is_employee == True`).
- **Superuser Isolation**:
  - Django superusers (`is_superuser=True`), even if stored with `role=EMPLOYEE`, are treated exclusively as administrators (`is_admin=True`, `is_employee=False`).
  - Ordinary employee management endpoints (`/api/v1/auth/employees/...`) strictly exclude `is_superuser=True` accounts from listing, detail retrieval, editing, deactivation, and password reset (`404 Not Found`), and superusers are barred from employee-only write endpoints (`IsEmployeeRole`).
- **Timezone**: `Asia/Kolkata` (`UTC+05:30`).

---

## 2. Authentication, Password Management & Employee Endpoints (`/api/v1/auth/`)

### 2.1 Authentication & Session Lifecycle

| Method | Endpoint | Permission | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/auth/login/` | Anonymous (throttled `5/min`) | Authenticate with `username` and `password`; returns `access`, `refresh`, and `user` profile. |
| `POST` | `/api/v1/auth/refresh/` | Anonymous (valid refresh token) | Rotate refresh token and issue a new `access` token. Rejects inactive users with `401`. |
| `POST` | `/api/v1/auth/logout/` | Active authenticated user | Blacklist the caller's own `refresh` token (`200 OK`). |
| `GET` | `/api/v1/auth/me/` | Active authenticated user | Return the authenticated user's profile (`200 OK`). |

### 2.2 Self-Service Password Change

- **Endpoint**: `POST /api/v1/auth/change-password/`
- **Permission**: Active authenticated user (`ADMIN` or `EMPLOYEE`).
- **Request Body**:
  ```json
  {
    "current_password": "OldPassword@123",
    "new_password": "NewSecurePassword@2026",
    "confirm_password": "NewSecurePassword@2026"
  }
  ```
- **Behavior & Status Codes**:
  - Validates `current_password`, checks `new_password == confirm_password`, prevents reusing the current password, and runs Django's configured password validators.
  - Updates the password hash (`200 OK`, `{"detail": "Password updated successfully."}`).
  - **Token Revocation**: Immediately blacklists all outstanding SimpleJWT refresh tokens for the user so existing sessions cannot mint new access tokens.
  - Returns `400 Bad Request` for validation errors or unknown fields, `401 Unauthorized` if unauthenticated.

### 2.3 Administrator Employee Password Reset

- **Endpoint**: `POST /api/v1/auth/employees/<pk>/reset-password/`
- **Permission**: Active administrator (`IsAdminRole`).
- **Target Scope**: Non-superuser employees only (`role=EMPLOYEE` and `is_superuser=False`). Attempting to target an `ADMIN`, a superuser, or a nonexistent user returns `404 Not Found`.
- **Request Body**:
  ```json
  {
    "new_password": "AdminResetPassword@2026",
    "confirm_password": "AdminResetPassword@2026"
  }
  ```
- **Behavior & Status Codes**:
  - Validates `new_password == confirm_password` and runs Django password validators against the target employee user.
  - Updates the employee's password hash (`200 OK`, `{"detail": "Employee password reset successfully."}`).
  - **Token Revocation**: Immediately blacklists all outstanding refresh tokens belonging to the target employee.
  - Returns `400 Bad Request` on invalid/weak passwords or unknown fields, `403 Forbidden` for employees, `404 Not Found` if target is outside non-superuser employee scope.

### 2.4 Employee Management & Workload Visibility

- **Endpoints**:
  - `GET /api/v1/auth/employees/` — List non-superuser employees. Optional query parameter: `is_active` (`true` or `false`). Unknown or repeated query parameters or invalid boolean strings return `400 Bad Request`.
  - `POST /api/v1/auth/employees/` — Create a new employee (`201 Created`).
  - `GET /api/v1/auth/employees/<pk>/` — Retrieve non-superuser employee detail (`200 OK`).
  - `PATCH /api/v1/auth/employees/<pk>/` — Update `first_name`, `last_name`, `email`, `phone`, `designation`, or `is_active` (`200 OK`). Rejects `role` or unknown fields with `400 Bad Request`.
  - `DELETE /api/v1/auth/employees/<pk>/` — Deactivate an active employee (`200 OK`).
- **Workload Counts (No N+1 Queries)**:
  - Employee list, detail, create, update, and deactivation responses include correlated subquery annotations:
    - `assigned_active_leads_count`: count of leads where `assigned_to = employee` and `is_archived = False`
    - `pending_followups_count`: count of follow-ups where `owner = employee` and `status = 'PENDING'`
  - **Example Response** (`GET /api/v1/auth/employees/<pk>/`):
    ```json
    {
      "id": 12,
      "username": "emp_aisha",
      "email": "aisha@iqracentral.com",
      "first_name": "Aisha",
      "last_name": "Khan",
      "full_name": "Aisha Khan",
      "role": "EMPLOYEE",
      "phone": "9876543210",
      "designation": "Senior Caller",
      "is_active": true,
      "assigned_active_leads_count": 18,
      "pending_followups_count": 5,
      "date_joined": "2026-11-15T10:00:00+05:30",
      "last_login": null
    }
    ```

---

## 3. Leads, Search/Filtering & Employee Lead Lifecycle (`/api/v1/leads/`)

### 3.1 Lead List, Search & Filtering (`GET /api/v1/leads/`)

- **Permissions**: Active `ADMIN` (organization-wide) or active `EMPLOYEE` (own active assigned leads only).
- **Supported Query Parameters**:
  - `search`: Case-insensitive substring search across `name`, `phone`, `alternate_phone`, `email`, and `course_interest`.
  - `status`: Valid `Lead.Status` value (`NEW`, `CONTACTED`, `INTERESTED`, `FOLLOW_UP`, `CONVERTED`, `NOT_INTERESTED`, `INVALID`, `CLOSED`).
  - `assigned_to`: *(Admin only)* Employee integer ID (`>= 1`).
  - `unassigned`: *(Admin only)* `true` or `false`.
  - `archived`: *(Admin only)* `true`, `false` (default), or `all`.
  - `page`, `page_size`: Pagination parameters (`page_size` default `20`, max `100`, must be integer `>= 1`).
- **Validation**:
  - Unknown or repeated query parameters return `400 Bad Request`.
  - Employees passing `assigned_to`, `unassigned`, or `archived` receive `400 Bad Request`.
  - Admin lead responses include `assigned_to`, `assigned_to_username`, and `assigned_to_name`.

### 3.2 Employee Lead Lifecycle Update (`POST /api/v1/leads/<pk>/status/`)

- **Permission**: Active non-superuser employee (`IsEmployeeRole`). Administrators and superusers receive `403 Forbidden`.
- **Ownership & Archive Rules**:
  - The lead must exist, be non-archived (`is_archived=False`), and be currently assigned to `request.user`.
  - Unassigned leads, leads assigned to another employee, archived leads, and nonexistent leads all return a generic `404 Not Found` (`{"detail": "Lead not found."}`).
- **Allowed Mutable Fields** (at least one required):
  - `status` (`Lead.Status`)
  - `course_interest`
  - `enquiry_details`
  - `alternate_phone`
  - `email`
- **Rejected Fields**:
  - `name`, `phone`, `assigned_to`, `internal_notes`, `is_archived`, `created_by`, or any unknown field returns `400 Bad Request`.
- **Concurrency & Lock Order**:
  - Acquires row locks in `Lead (SELECT ... FOR UPDATE) → User[employee] (SELECT ... FOR UPDATE)` order inside `transaction.atomic()`, matching lead assignment, call submission, and follow-up creation so concurrent lead reassignment cannot deadlock or allow a stale owner to mutate a reassigned lead.
- **Request Example**:
  ```json
  {
    "status": "INTERESTED",
    "course_interest": "MBA International Business",
    "enquiry_details": "Requested brochure and fee structure.",
    "alternate_phone": "+91 98765 99999",
    "email": "student@example.com"
  }
  ```
- **Response Example (`200 OK`)**:
  ```json
  {
    "id": 42,
    "name": "Rahul Sharma",
    "phone": "9876500001",
    "alternate_phone": "+91 98765 99999",
    "email": "student@example.com",
    "course_interest": "MBA International Business",
    "enquiry_details": "Requested brochure and fee structure.",
    "status": "INTERESTED",
    "created_at": "2026-11-15T09:30:00+05:30",
    "updated_at": "2026-11-15T11:15:00+05:30"
  }
  ```

### 3.3 Admin Lead Creation, Update, Assignment & Import

- `POST /api/v1/leads/` & `PATCH /api/v1/leads/<pk>/` — Admin create/update lead. Rejects unknown fields and rejects assigning archived leads (`is_archived=True` with non-null `assigned_to`) with `400 Bad Request` (unassigning an archived lead via `assigned_to: null` remains permitted).
- `POST /api/v1/leads/bulk-assign/` — Admin atomic bulk assignment (`lead_ids`, `employee_id`, optional `reason`). Rejects unknown fields and archived leads with `400 Bad Request`.
- `GET /api/v1/leads/<pk>/assignment-history/` — Admin paginated assignment history with immutable employee/actor snapshots.
- `POST /api/v1/leads/import/preview/`, `POST /api/v1/leads/import/commit/`, `POST /api/v1/leads/import/<pk>/cancel/` — Admin staged CSV/XLSX import workflow.

---

## 4. Calls & Call Outcomes (`/api/v1/calls/`)

- `POST /api/v1/calls/` — Employee idempotent call submission (`lead_id`, `client_event_id`, `outcome_code`, `started_at`, `ended_at`, `employee_notes`). Returns `201 Created` on initial creation, `200 OK` on exact retry, `409 Conflict` on payload mismatch with the same `client_event_id`.
- `GET /api/v1/calls/` & `GET /api/v1/calls/<pk>/` — Scoped call history.
  - Supports `lead_id`, `outcome_code`, `started_at_from`, `started_at_to`, and admin-only `caller_id`.
  - Rejects unknown or repeated query parameters (including `page` and `page_size`) and invalid `page_size` values with `400 Bad Request`.
  - Administrator responses include `caller_id` alongside `caller_username_snapshot` and `caller_name_snapshot`; employee responses omit `caller_id`.
- `GET /api/v1/calls/outcomes/`, `POST /api/v1/calls/outcomes/`, `PATCH /api/v1/calls/outcomes/<pk>/` — Call outcome catalog management.

---

## 5. Follow-Ups & Follow-Up Event History (`/api/v1/follow-ups/`)

- `POST /api/v1/follow-ups/` — Employee creates follow-up (requires `Idempotency-Key` UUID header).
- `GET /api/v1/follow-ups/` & `GET /api/v1/follow-ups/<pk>/` — Scoped follow-up list and detail.
- `POST /api/v1/follow-ups/<pk>/reschedule/` — Employee reschedules own pending follow-up (requires `Idempotency-Key` UUID header).
- `POST /api/v1/follow-ups/<pk>/complete/` — Employee completes own pending follow-up (`{}`).
- `POST /api/v1/follow-ups/<pk>/cancel/` — Employee cancels own pending follow-up (optional `reason`), or administrator cancels pending follow-up of an inactive employee (nonblank `reason` required).

### 5.1 Follow-Up Event Audit History (`GET /api/v1/follow-ups/<pk>/events/`)

- **Permission**: Active administrator only (`IsActiveFollowUpAdmin`). Ordinary employees receive `403 Forbidden`.
- **Behavior**:
  - Returns `404 Not Found` if follow-up `<pk>` does not exist.
  - Returns chronological (`created_at ASC, id ASC`) paginated `FollowUpEvent` records using `FollowUpPagination` (`page_size` default `20`, max `100`).
  - Never exposes internal `idempotency_key` or `request_fingerprint` fields.
- **Response Example (`200 OK`)**:
  ```json
  {
    "count": 2,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 101,
        "event_type": "CREATED",
        "actor_id": 12,
        "actor_username_snapshot": "emp_aisha",
        "actor_name_snapshot": "Aisha Khan",
        "previous_scheduled_at": null,
        "new_scheduled_at": "2026-11-16T11:00:00+05:30",
        "previous_status": "",
        "new_status": "PENDING",
        "reason": "",
        "created_at": "2026-11-15T10:15:00+05:30"
      },
      {
        "id": 102,
        "event_type": "RESCHEDULED",
        "actor_id": 12,
        "actor_username_snapshot": "emp_aisha",
        "actor_name_snapshot": "Aisha Khan",
        "previous_scheduled_at": "2026-11-16T11:00:00+05:30",
        "new_scheduled_at": "2026-11-18T14:30:00+05:30",
        "previous_status": "PENDING",
        "new_status": "PENDING",
        "reason": "Student requested callback on Wednesday",
        "created_at": "2026-11-15T11:05:00+05:30"
      }
    ]
  }
  ```

---

## 6. Admin Dashboard, Employee Performance & Exports (`/api/v1/reports/`)

### 6.1 Access Control & Role Semantics

- **Permissions**: Active administrators (`role=ADMIN` or `is_superuser=True`) only (`IsAuthenticated`, `IsAdminRole`).
  - Anonymous users receive `401 Unauthorized`.
  - Active or inactive ordinary employees and inactive administrators receive `403 Forbidden`.
- **Non-Superuser Employee Scope**:
  - Superuser accounts (`is_superuser=True`) are never treated as calling employees: they are excluded from `/api/v1/reports/employees/` rows, `workload` employee counts, `employee_performance` exports, and cannot be passed as `employee_id` (`400 Bad Request`).

### 6.2 `Asia/Kolkata` Date Window & Validation Rules

- **Timezone**: All reporting date boundaries and daily trends are evaluated in `Asia/Kolkata` (`UTC+05:30`).
- **Date Parameters**:
  - `date_from` and `date_to` accept strict `YYYY-MM-DD` calendar dates only.
  - When both `date_from` and `date_to` are omitted, the period defaults to the **last 30 local calendar days inclusive** (`today_kolkata - 29 days` through `today_kolkata`, configured via `REPORT_DEFAULT_DATE_RANGE_DAYS = 30`).
  - When only one date (`date_from` or `date_to`) is supplied, that date is used for both boundaries (a single local calendar day).
  - Half-open timestamp interval: `[00:00:00+05:30 on date_from, 00:00:00+05:30 on date_to + 1 day)`.
  - Reversed date ranges (`date_from > date_to`) or windows exceeding `REPORT_MAX_DATE_RANGE_DAYS = 366` inclusive calendar days return `400 Bad Request`.
- **Query Parameter Guardrails**:
  - Unknown or repeated query parameters return `400 Bad Request`.
  - `employee_id` must be a positive integer (`>= 1`) referencing an existing non-superuser `EMPLOYEE` (`role=EMPLOYEE` and `is_superuser=False`), or `400 Bad Request` is returned.

### 6.3 Admin Dashboard Summary (`GET /api/v1/reports/dashboard/`)

- **Supported Query Parameters**: `date_from`, `date_to`, `employee_id`.
- **Metric Sections**:
  - **`period`**: Resolved `timezone`, `date_from`, `date_to`, `start_at`, `end_exclusive_at`, and `employee_id`.
  - **`leads`**:
    - Organization-wide totals (`total_active`, `total_archived`, `assigned_active`, `unassigned_active`, `created_in_period`, `current_active_by_status`, `created_in_period_by_current_status`) always reflect the full organization, with all `Lead.Status` keys present (`0` when empty). Note: `Lead` does not store status-transition history, so `created_in_period_by_current_status` groups leads created in the period by their current `status`.
    - When `employee_id` is supplied, `filtered_employee_leads` provides that employee's `assigned_active`, `assigned_archived`, `created_in_period_assigned_to_employee`, and `current_active_by_status`.
  - **`calls`**:
    - Calls with `started_at` in `[start_at, end_exclusive_at)` (scoped to `caller_id=employee_id` when supplied): `total_calls`, `unique_leads_called`, `total_duration_seconds`, `average_duration_seconds`, `by_outcome` (grouped by immutable `outcome_code_snapshot` and `outcome_label_snapshot`), and chronological `daily_trends` (grouped by `Asia/Kolkata` local date of `started_at`).
  - **`follow_ups`**:
    - Scoped to `owner_id=employee_id` when supplied: current snapshot (`pending_total`, `overdue` `< today 00:00+05:30`, `due_today` in `[today 00:00+05:30, tomorrow 00:00+05:30)`, `upcoming` `>= tomorrow 00:00+05:30`) and period activity (`created_in_period` by `created_at`, `completed_in_period` by `completed_at`, `cancelled_in_period` by `cancelled_at`).
  - **`workload`**:
    - Organization-wide non-superuser employee counts and inactive-owner alerts (`total_employees`, `active_employees`, `inactive_employees`, `inactive_employees_with_active_leads_count`, `inactive_employees_with_pending_followups_count`, `active_leads_with_inactive_owner_count`, `pending_followups_with_inactive_owner_count`, `assignments_changed_in_period` from `LeadAssignmentHistory.changed_at`), plus `filtered_employee_workload` when `employee_id` is supplied.
- **Sample Response (`200 OK`)**:
  ```json
  {
    "period": {
      "timezone": "Asia/Kolkata",
      "date_from": "2026-10-17",
      "date_to": "2026-11-15",
      "start_at": "2026-10-17T00:00:00+05:30",
      "end_exclusive_at": "2026-11-16T00:00:00+05:30",
      "employee_id": null
    },
    "leads": {
      "total_active": 120,
      "total_archived": 5,
      "assigned_active": 105,
      "unassigned_active": 15,
      "created_in_period": 48,
      "current_active_by_status": {
        "NEW": 30,
        "CONTACTED": 25,
        "INTERESTED": 20,
        "NOT_INTERESTED": 27,
        "CONVERTED": 18
      },
      "created_in_period_by_current_status": {
        "NEW": 20,
        "CONTACTED": 10,
        "INTERESTED": 8,
        "NOT_INTERESTED": 7,
        "CONVERTED": 3
      },
      "filtered_employee_leads": null
    },
    "calls": {
      "total_calls": 85,
      "unique_leads_called": 52,
      "total_duration_seconds": 10200,
      "average_duration_seconds": 120.0,
      "by_outcome": [
        {"outcome_code": "INTERESTED", "outcome_label": "Interested", "count": 32},
        {"outcome_code": "NO_ANSWER", "outcome_label": "No Answer", "count": 53}
      ],
      "daily_trends": [
        {
          "date": "2026-11-14",
          "total_calls": 40,
          "unique_leads_called": 28,
          "total_duration_seconds": 4800
        }
      ]
    },
    "follow_ups": {
      "pending_total": 22,
      "overdue": 4,
      "due_today": 6,
      "upcoming": 12,
      "created_in_period": 30,
      "completed_in_period": 19,
      "cancelled_in_period": 3
    },
    "workload": {
      "total_employees": 8,
      "active_employees": 7,
      "inactive_employees": 1,
      "inactive_employees_with_active_leads_count": 1,
      "inactive_employees_with_pending_followups_count": 1,
      "active_leads_with_inactive_owner_count": 3,
      "pending_followups_with_inactive_owner_count": 2,
      "assignments_changed_in_period": 44,
      "filtered_employee_workload": null
    }
  }
  ```

### 6.4 Employee Performance & Workload Report (`GET /api/v1/reports/employees/`)

- **Supported Query Parameters**: `date_from`, `date_to`, `employee_id`, `is_active` (`true` or `false`), `page`, `page_size` (default `20`, max `100`).
- **Ordering**: Deterministic `("-is_active", "username", "id")`.
- **Metric Scopes** (documented in `period.metric_scopes`):
  - **Current Workload Snapshot (`current_workload_fields`)**: `assigned_active_leads_count`, `converted_active_leads_count`, `pending_followups_count`, `overdue_followups_count`.
  - **Period Performance (`period_performance_fields`)**: `calls_count`, `unique_leads_called_count`, `total_call_duration_seconds`, `average_call_duration_seconds`, `followups_completed_count`, `followups_cancelled_count`.
- **Query Performance**: Uses correlated PostgreSQL `Subquery` annotations (2 SQL queries total: `COUNT(*)` + paginated `SELECT`), independent of employee count.

### 6.5 Operational Report Export (`GET /api/v1/reports/export/`)

- **Required Query Parameter**:
  - `report_type`: `employee_performance`, `calls`, `leads`, or `followups`.
- **Optional Format Parameter**:
  - `export_format`: `csv` (default, `text/csv; charset=utf-8`) or `xlsx` (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`).
- **Supported Filters by `report_type`** (unsupported cross-report filters return `400 Bad Request`):
  - `employee_performance`: `date_from`, `date_to`, `employee_id`, `is_active`
  - `calls`: `date_from`, `date_to`, `employee_id`, `lead_id`, `outcome_code`
  - `leads`: `date_from`, `date_to` (filters `created_at` when supplied), `employee_id`, `status`, `archived` (`true`, `false` default, `all`), `unassigned` (`true` or `false`), `search`
  - `followups`: `date_from`, `date_to` (filters `scheduled_at` when supplied), `employee_id`, `lead_id`, `status`, `due_state` (`today`, `overdue`, `upcoming`)
- **Row Limit & Spreadsheet Security**:
  - Enforces `REPORT_EXPORT_MAX_ROWS = 10000` before building the file. If matching rows exceed the limit, returns `400 Bad Request` with a clear message instead of silently truncating.
  - **Formula-Injection Protection**: Any string cell starting with `=`, `+`, `-`, `@`, `\t`, or `\r` (including after leading spaces/newlines) is prefixed with `'` in both CSV and XLSX exports, while genuine numeric values (including negative numbers) remain unprefixed numeric cells.
  - **Sensitive Field Exclusion**: Never exports password hashes, tokens, `Lead.internal_notes`, `FollowUpEvent.idempotency_key`, `FollowUpEvent.request_fingerprint`, or private employee `email` addresses.

---

## 7. Admin CRM Web Interface (`/crm/`) — Phase 4.2

The responsive Django + Bootstrap 5 + HTMX Admin CRM web interface is served under `/crm/` (`apps/webcrm`) while preserving all `/api/v1/` endpoints for the Android employee calling app and `/admin/` for Django maintenance.

### 7.1 Authentication, Rate Limiting & Authorization
- **Session Login (`GET/POST /crm/login/`)**: Dedicated administrator sign-in page. Only active `ADMIN` users and active `superuser` accounts may sign in.
- **PostgreSQL Shared Atomic Rate Limiting (`PostgresAtomicCache`)**:
  - Enforces **5 failed attempts per 60 seconds** per normalized `(username, client_ip)` combination (`CRM_LOGIN_MAX_ATTEMPTS=5`, `CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS=60`) and an IP-wide threshold across all usernames (`CRM_LOGIN_MAX_IP_ATTEMPTS=5`), returning `429 Too Many Requests` when exceeded.
  - Uses shared, worker-safe atomic storage via `CACHES["crm_ratelimit"]` (`apps.webcrm.rate_limit.PostgresAtomicCache` backed by the primary PostgreSQL database table `iqra_shared_cache` by default) so counters are shared atomically across multiple Gunicorn/Daphne workers without requiring Redis. Setting `CACHE_URL` configures `CACHES["default"]` (for DRF throttling) and never implicitly changes `CACHES["crm_ratelimit"]`.
  - **Consistent PostgreSQL Database Clock**: All counter expirations and comparisons are evaluated directly in SQL against PostgreSQL's `STATEMENT_TIMESTAMP()`, avoiding Python clock drift or `time.time()` / `timezone.now()` mismatches across workers.
  - **Atomic Pre-Authentication Admission & Counter Semantics**:
    - Before password verification (`AdminWebLoginForm` / PBKDF2) runs, `LoginRateLimiter.admit_attempt()` updates both the `(client_ip, normalized_username)` counter (first) and the IP-wide `client_ip` counter (second) inside a **single PostgreSQL transaction** (`PostgresAtomicCache.run_in_transaction()`) using a consistent lock order (`user_ip_key` -> `ip_key`).
    - **No database transaction, row lock, or cursor is held open while PBKDF2 executes.**
    - **Rejected Admissions Retain No Increments**: If either counter would exceed its threshold (`> CRM_LOGIN_MAX_ATTEMPTS` or `> CRM_LOGIN_MAX_IP_ATTEMPTS`), the admission transaction marks itself for rollback (`transaction.set_rollback(True, using=db_alias)`), rolling back **both** updates so neither counter retains an increment from the rejected request, and returns `admitted=False` (HTTP `429 Too Many Requests`) before password verification runs.
    - If either counter update raises a database error during admission, the enclosing transaction rolls back both updates and fails closed with HTTP `503 Service Unavailable`.
    - **Failed Attempts & Abandoned Reservations**: For admitted requests (`admitted=True`), if password verification subsequently fails (or if a worker crashes mid-request), the committed `+1` admission reservation remains recorded and automatically expires at the end of the `CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS` window.
    - **Selected Successful-Login Policy & Atomic Paired Reconciliation**: When credentials are verified valid, `LoginRateLimiter.reconcile_successful_attempt()` executes both window-bound atomic slot releases (`WHERE "cache_key" = %s AND "expires" = %s AND "expires" > STATEMENT_TIMESTAMP()`) inside a **single PostgreSQL transaction** using the same `(user_ip_key, ip_key)` lock order:
      1. Legitimate successful sign-ins consume `0` net attempt budget.
      2. A successful sign-in **never** erases prior or concurrent failed attempts on `(client_ip, normalized_username)` or on the IP-wide `client_ip` counter, and never decrements a subsequent window if the original window expired during PBKDF2.
      3. Because rejected requests (`admitted=False`) roll back their increments at admission, a valid login admitted before a concurrent rejected request still succeeds when actual admitted failures have not saturated the threshold (`remaining_count < limit`).
      4. If either slot release fails with a database error during reconciliation, the enclosing transaction rolls back both releases and `login_view` fails closed with `503 Service Unavailable` without establishing a session.
      5. `LoginRateLimiter.clear_attempts()` deletes only the per-`(client_ip, normalized_username)` counter and preserves the IP-wide counter until it expires naturally.
    - **Bounded Reservation Expiry Metadata**: `PostgresAtomicCache` pops per-key reservation expiry metadata immediately during `admit_attempt()` and caps its thread-local fallback `OrderedDict` at `MAX_TRACKED_EXPIRES_KEYS = 8` entries so long-lived worker threads never accumulate unbounded key metadata.
  - **Bounded Expired-Row Cleanup**:
    - Every committed atomic counter write opportunistically prunes at most `128` expired rows from `iqra_shared_cache` using an index scan on `iqra_shared_cache_expires_idx` (`ORDER BY "expires" ASC LIMIT 128 FOR UPDATE SKIP LOCKED`), preventing unbounded table growth without scanning the full table during login requests and without blocking concurrent workers.
    - Under extreme sustained distributed brute-force traffic (where new distinct keys arrive faster than 128 per login attempt), operators may also invoke `caches["crm_ratelimit"].prune_expired(batch_size=1000)` periodically from a scheduled cron/management job.
  - **Strict Backend Validation & Fail-Closed Behavior**:
    - `LoginRateLimiter` rejects `LocMemCache`, `DummyCache`, Django's `RedisCache`, and any custom backend that does not explicitly declare `is_shared_backend = True` and provide a callable `atomic_increment` method.
    - `CRM_LOGIN_MAX_ATTEMPTS >= 1`, `CRM_LOGIN_MAX_IP_ATTEMPTS >= 1`, `CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS >= 1`, `CRM_TRUSTED_PROXY_IPS` (valid IPv4/IPv6 addresses or CIDR networks), and the `CRM_SHARED_CACHE_TABLE` identifier allowlist (`[A-Za-z_][A-Za-z0-9_]{0,45}`) are validated at startup (`ImproperlyConfigured`) and at runtime.
    - If the shared rate-limit backend is unreachable, misconfigured, or errors during `POST /crm/login/`, the view fails closed with `503 Service Unavailable` (`"Sign-in is temporarily unavailable. Please try again shortly."`) and logs only the exception class name without exposing credentials, usernames, or IP addresses.
  - **Safe Concurrent Table Initialization & Trusted Reverse-Proxy Forwarding Headers**:
    - `PostgresAtomicCache._ensure_table()` serializes DDL initialization across workers using a PostgreSQL transaction-scoped advisory lock (`pg_advisory_xact_lock`) and re-checks relation existence via `to_regclass` before executing `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. Run `python manage.py migrate` prior to starting multi-worker processes so the table is provisioned during deployment.
    - **Default Mode (`CRM_TRUST_X_FORWARDED_FOR=False`)**: `X-Forwarded-For` is ignored completely and `REMOTE_ADDR` is used.
    - **Hardened Trusted-Proxy Mode (`CRM_TRUST_X_FORWARDED_FOR=True` + `CRM_TRUSTED_PROXY_IPS`)**:
      - `X-Forwarded-For` is **never** trusted unless `CRM_TRUST_X_FORWARDED_FOR=True`, `CRM_TRUSTED_PROXY_IPS` is non-empty, **and** the immediate peer `REMOTE_ADDR` matches an explicitly configured proxy IP or CIDR in `CRM_TRUSTED_PROXY_IPS`.
      - When `REMOTE_ADDR` is a trusted proxy, `resolve_client_ip()` walks `X-Forwarded-For` hops from **right to left** (matching standard reverse-proxy append order) and selects the rightmost valid non-trusted IP address, preventing clients from spoofing leftmost `X-Forwarded-For` values. If any inspected hop is empty or malformed, resolution fails safely back to `REMOTE_ADDR`.
      - Example Nginx reverse-proxy and static-file configuration (when Nginx runs on `127.0.0.1` and forwards to Gunicorn/Daphne):
        ```nginx
        location /static/ {
            alias /path/to/consultancy-crm/staticfiles/;
            expires 30d;
            add_header Cache-Control "public, no-transform";
        }

        location / {
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            client_max_body_size 6m;
            proxy_pass http://127.0.0.1:8000;
        }
        ```
        With matching environment configuration: `CRM_TRUST_X_FORWARDED_FOR=True` and `CRM_TRUSTED_PROXY_IPS=127.0.0.1`.
        In production (`DEBUG=False`), run `python manage.py collectstatic --noinput` so Nginx serves `/static/` directly from `STATIC_ROOT` (`staticfiles/`). In local development (`DEBUG=True`), Django serves `/static/` via `staticfiles_urlpatterns()`.
- **Session Logout (`POST /crm/logout/`)**: POST-only CSRF-protected logout endpoint (`GET` returns `405 Method Not Allowed`).
- **Role Enforcement**: Active ordinary `EMPLOYEE` accounts and inactive accounts are rejected at `/crm/login/` and receive `403 Forbidden` on all `/crm/` views.

### 7.3 Deployment & Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgres://apple@localhost:5432/iqra_tcs_crm` | Primary PostgreSQL connection URL for IQRA TCS CRM. |
| `CACHE_URL` | *(empty)* | Optional cache URL for `CACHES["default"]` (DRF API throttling). Never overrides `CACHES["crm_ratelimit"]`. |
| `CRM_SHARED_CACHE_TABLE` | `iqra_shared_cache` | PostgreSQL table name used by `PostgresAtomicCache` (validated against `[A-Za-z_][A-Za-z0-9_]{0,45}`). |
| `CRM_LOGIN_MAX_ATTEMPTS` | `5` | Maximum failed `/crm/login/` attempts per normalized `(username, client_ip)` within the window (`>= 1`). |
| `CRM_LOGIN_MAX_IP_ATTEMPTS` | `5` | Maximum failed `/crm/login/` attempts per `client_ip` across all usernames within the window (`>= 1`). |
| `CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window duration in seconds for `/crm/login/` (`>= 1`). |
| `CRM_TRUST_X_FORWARDED_FOR` | `False` | Enable `X-Forwarded-For` evaluation only when `REMOTE_ADDR` is in `CRM_TRUSTED_PROXY_IPS`. |
| `CRM_TRUSTED_PROXY_IPS` | *(empty)* | Comma-separated list of trusted reverse-proxy IPs or CIDRs (e.g. `127.0.0.1,10.0.0.2`). Required for `X-Forwarded-For` to be trusted. |

### 7.2 Web CRM Routes
- **Dashboard (`/crm/`)**: Live organization workload snapshot, period performance metrics (`Asia/Kolkata`), current lead status breakdown, call outcome snapshot distribution, daily call trend bars, and employee workload/performance overview (with HTMX partial updates).
- **Lead Management (`/crm/leads/`)**:
  - `GET /crm/leads/` — Search, filter (`status`, `assigned_to`, `unassigned`, `archived`), paginate, and atomic bulk-assign leads.
  - `GET/POST /crm/leads/create/` & `GET/POST /crm/leads/<id>/edit/` — Create and update leads via `AdminLeadSerializer` (lead edits modify non-assignment fields only; assignment changes are performed exclusively via `/crm/leads/<id>/assign/`).
  - `GET /crm/leads/<id>/` — Lead detail, manual assignment (`POST /crm/leads/<id>/assign/`), archive/unarchive (`POST /crm/leads/<id>/archive/`), assignment audit history, call history, and follow-ups.
  - `GET /crm/leads/import/`, `POST /crm/leads/import/preview/`, `POST /crm/leads/import/commit/`, `POST /crm/leads/import/<id>/cancel/` — Staged CSV/XLSX import preview (guarded by `LeadImportUploadLimitMiddleware` at `LEAD_IMPORT_MAX_REQUEST_SIZE = 6 MB`; production reverse proxies such as Nginx should also configure `client_max_body_size 6m;`), explicit confirmation checkbox (`confirm_commit="yes"`), commit, and cancellation.
- **Employee Management (`/crm/employees/`)**:
  - `GET /crm/employees/`, `GET/POST /crm/employees/create/`, `GET /crm/employees/<id>/` — List, create, and inspect non-superuser calling employees (superusers are excluded and return `404`).
  - `POST /crm/employees/<id>/edit/`, `POST /crm/employees/<id>/toggle-active/`, `POST /crm/employees/<id>/reset-password/` — Update employee profile, activate/deactivate (revoking active JWT refresh tokens on deactivation), and reset password (revoking active JWT refresh tokens).
- **Calls & Outcomes (`/crm/calls/`, `/crm/call-outcomes/`)**:
  - `GET /crm/calls/` & `GET /crm/calls/<id>/` — Strictly read-only call audit log rendering immutable caller and outcome snapshots.
  - `GET/POST /crm/call-outcomes/` & `POST /crm/call-outcomes/<id>/update/` — Create and update call outcome catalog entries (enforcing immutable `code`).
- **Follow-ups (`/crm/follow-ups/`)**:
  - `GET /crm/follow-ups/` & `GET /crm/follow-ups/<id>/` — Filter follow-ups (`overdue`, `today`, `upcoming`, `COMPLETED`, `CANCELLED`), inspect `FollowUpEvent` audit history, and cancel pending follow-ups for inactive employees with a mandatory nonblank reason (`POST /crm/follow-ups/<id>/cancel/`).
- **Reports & Exports (`/crm/reports/`, `/crm/reports/export/`)**:
  - Interactive reporting center and CSV/XLSX download launcher backed by `apps.reports`.
