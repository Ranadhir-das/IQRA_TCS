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
