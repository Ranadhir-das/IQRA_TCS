/**
 * IQRA TCS Employee Caller App — Domain & API Contract Types
 * Aligned with `/Users/apple/Projects/iqra-tcs/consultancy-crm` (`/api/v1/`).
 */

export type UserRole = 'ADMIN' | 'EMPLOYEE';

/**
 * Subset of user fields returned inside `POST /api/v1/auth/login/` response.
 */
export interface LoginUserSummary {
  id: number;
  username: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
}

/**
 * Full employee profile returned by `GET /api/v1/auth/me/` (`UserProfileSerializer`).
 */
export interface UserProfile {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  full_name: string;
  role: UserRole;
  phone: string;
  designation: string;
  is_active: boolean;
  date_joined: string;
  last_login: string | null;
}

/**
 * Request body for `POST /api/v1/auth/login/` (`LoginSerializer`).
 */
export interface LoginRequest {
  username: string;
  password: string;
}

/**
 * Response body from `POST /api/v1/auth/login/` (`LoginView`).
 */
export interface LoginResponse {
  access: string;
  refresh: string;
  user: LoginUserSummary;
}

/**
 * Response body from `POST /api/v1/auth/refresh/` (`CustomTokenRefreshView`).
 * Note: Backend sets `ROTATE_REFRESH_TOKENS=True` and `BLACKLIST_AFTER_ROTATION=True`,
 * so `refresh` is rotated on every refresh call.
 */
export interface TokenRefreshResponse {
  access: string;
  refresh?: string;
}

/**
 * Request body for `POST /api/v1/auth/change-password/` (`SelfChangePasswordSerializer`).
 */
export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

export interface ApiMessageResponse {
  detail: string;
}

/**
 * Lead status choices defined in `apps/leads/models.py` (`Lead.Status`).
 */
export type LeadStatus =
  | 'NEW'
  | 'CONTACTED'
  | 'INTERESTED'
  | 'NOT_INTERESTED'
  | 'CONVERTED';

export type LeadStatusFilter = 'ALL' | LeadStatus;

/**
 * Employee-facing lead representation returned by `GET /api/v1/leads/`
 * and `GET /api/v1/leads/<id>/` (`EmployeeLeadSerializer`).
 * Excludes admin-only `internal_notes` and assignment metadata.
 */
export interface EmployeeLead {
  id: number;
  name: string;
  phone: string;
  alternate_phone: string;
  email: string;
  course_interest: string;
  enquiry_details: string;
  status: LeadStatus;
  created_at: string;
  updated_at: string;
}

/**
 * Standard DRF `PageNumberPagination` response envelope.
 */
export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/**
 * Supported query parameters on `GET /api/v1/leads/` for `EMPLOYEE` users.
 */
export interface AssignedLeadsQueryParams {
  page?: number;
  page_size?: number;
  search?: string;
  status?: LeadStatus;
}

// ─── Phase 2: Call Outcomes, Call Submissions & Call History ────────────────

/**
 * Administrator-configured call outcome returned by `GET /api/v1/call-outcomes/`
 * (`CallOutcomeSerializer`).
 */
export interface CallOutcome {
  id: number;
  code: string;
  label: string;
  is_active: boolean;
  display_order: number;
}

/**
 * Request body for `POST /api/v1/calls/` (`CallSubmissionSerializer`).
 * Note: Only these 6 fields are accepted by the backend serializer.
 * `duration_seconds` is computed server-side from `started_at` and `ended_at`.
 */
export interface CallSubmissionRequest {
  lead_id: number;
  client_event_id: string;
  outcome_code: string;
  started_at: string;
  ended_at: string;
  employee_notes?: string;
}

/**
 * Response body from `POST /api/v1/calls/` (`CallSubmissionResponseSerializer`).
 * Returned with HTTP 201 on new creation or HTTP 200 on exact idempotent retry.
 */
export interface CallSubmissionResponse {
  id: number;
  lead_id: number;
  client_event_id: string;
  outcome_code_snapshot: string;
  outcome_label_snapshot: string;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  employee_notes: string;
  created_at: string;
}

/**
 * Employee call history item returned by `GET /api/v1/calls/` and `GET /api/v1/calls/<id>/`
 * (`CallHistorySerializer`).
 */
export interface CallHistoryItem {
  id: number;
  lead_id: number;
  lead_name: string;
  caller_username_snapshot: string;
  caller_name_snapshot: string;
  outcome_code_snapshot: string;
  outcome_label_snapshot: string;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  employee_notes: string;
  created_at: string;
}

/**
 * Supported query parameters on `GET /api/v1/calls/` for `EMPLOYEE` users
 * (`CallHistoryFilterSerializer`).
 */
export interface CallHistoryQueryParams {
  lead_id?: number;
  outcome_code?: string;
  started_at_from?: string;
  started_at_to?: string;
  page?: number;
  page_size?: number;
}

// ─── Phase 2: Follow-Ups & Lifecycle Operations ─────────────────────────────

export type FollowUpStatus = 'PENDING' | 'COMPLETED' | 'CANCELLED';

export type FollowUpDueState = 'today' | 'overdue' | 'upcoming';

/**
 * Employee follow-up representation returned by `GET /api/v1/follow-ups/`,
 * `POST /api/v1/follow-ups/`, and lifecycle endpoints (`EmployeeFollowUpSerializer`).
 * `lead_phone` is optionally enriched client-side from the employee's assigned lead cache.
 */
export interface EmployeeFollowUp {
  id: number;
  lead_id: number;
  lead_name: string;
  lead_phone?: string;
  call_id: number | null;
  scheduled_at: string;
  status: FollowUpStatus;
  notes: string;
  completed_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Request body for `POST /api/v1/follow-ups/` (`FollowUpCreateSerializer`).
 * Requires `Idempotency-Key: <UUID>` request header.
 */
export interface FollowUpCreateRequest {
  lead_id: number;
  call_id?: number | null;
  scheduled_at: string;
  notes?: string;
}

/**
 * Request body for `POST /api/v1/follow-ups/<id>/reschedule/` (`FollowUpRescheduleSerializer`).
 * Requires `Idempotency-Key: <UUID>` request header.
 */
export interface FollowUpRescheduleRequest {
  scheduled_at: string;
  notes?: string;
  reason?: string;
}

/**
 * Request body for `POST /api/v1/follow-ups/<id>/cancel/` (`FollowUpCancelSerializer`).
 */
export interface FollowUpCancelRequest {
  reason?: string;
}

/**
 * Supported query parameters on `GET /api/v1/follow-ups/` for `EMPLOYEE` users
 * (`FollowUpFilterSerializer`).
 */
export interface FollowUpListQueryParams {
  status?: FollowUpStatus;
  lead_id?: number;
  scheduled_from?: string;
  scheduled_to?: string;
  due_state?: FollowUpDueState;
  page?: number;
  page_size?: number;
}
