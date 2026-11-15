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
