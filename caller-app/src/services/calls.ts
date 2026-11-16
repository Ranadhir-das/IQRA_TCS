import { DEFAULT_LEADS_PAGE_SIZE } from '../constants/config.ts';
import type {
  CallHistoryItem,
  CallHistoryQueryParams,
  CallOutcome,
  CallSubmissionRequest,
  CallSubmissionResponse,
  PaginatedResponse,
} from '../types/index.ts';
import { toExplicitOffsetIsoString } from '../utils/format.ts';
import { isValidUuid } from '../utils/idempotency.ts';
import { apiRequest, apiRequestWithStatus } from './api.ts';

export const MAX_CALL_DURATION_SECONDS = 86_400; // 24 hours per backend MAX_CALL_DURATION
export const MAX_EMPLOYEE_NOTES_LENGTH = 5_000;

function sanitizeCallOutcome(raw: unknown): CallOutcome {
  const record = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  return {
    id: typeof record.id === 'number' ? record.id : Number(record.id ?? 0),
    code: typeof record.code === 'string' ? record.code : '',
    label: typeof record.label === 'string' ? record.label : '',
    is_active: Boolean(record.is_active),
    display_order:
      typeof record.display_order === 'number' ? record.display_order : Number(record.display_order ?? 0),
  };
}

function sanitizeCallHistoryItem(raw: unknown): CallHistoryItem {
  const record = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  return {
    id: typeof record.id === 'number' ? record.id : Number(record.id ?? 0),
    lead_id: typeof record.lead_id === 'number' ? record.lead_id : Number(record.lead_id ?? 0),
    lead_name: typeof record.lead_name === 'string' ? record.lead_name : '',
    caller_username_snapshot:
      typeof record.caller_username_snapshot === 'string' ? record.caller_username_snapshot : '',
    caller_name_snapshot:
      typeof record.caller_name_snapshot === 'string' ? record.caller_name_snapshot : '',
    outcome_code_snapshot:
      typeof record.outcome_code_snapshot === 'string' ? record.outcome_code_snapshot : '',
    outcome_label_snapshot:
      typeof record.outcome_label_snapshot === 'string' ? record.outcome_label_snapshot : '',
    started_at: typeof record.started_at === 'string' ? record.started_at : '',
    ended_at: typeof record.ended_at === 'string' ? record.ended_at : '',
    duration_seconds:
      typeof record.duration_seconds === 'number'
        ? record.duration_seconds
        : Number(record.duration_seconds ?? 0),
    employee_notes: typeof record.employee_notes === 'string' ? record.employee_notes : '',
    created_at: typeof record.created_at === 'string' ? record.created_at : '',
  };
}

/**
 * Loads active call outcomes configured by administrators from `GET /api/v1/call-outcomes/`.
 * Filters to `is_active === true` and sorts by `(display_order, label, id)`.
 */
export async function fetchActiveCallOutcomes(
  signal?: AbortSignal
): Promise<CallOutcome[]> {
  const response = await apiRequest<unknown>('/call-outcomes/', {
    method: 'GET',
    auth: true,
    signal,
  });

  const rawItems: unknown[] = Array.isArray(response)
    ? response
    : response &&
        typeof response === 'object' &&
        Array.isArray((response as Record<string, unknown>).results)
      ? ((response as Record<string, unknown>).results as unknown[])
      : [];

  return rawItems
    .map(sanitizeCallOutcome)
    .filter((item) => item.is_active && Boolean(item.code))
    .sort((a, b) => {
      if (a.display_order !== b.display_order) {
        return a.display_order - b.display_order;
      }
      const labelCompare = a.label.localeCompare(b.label);
      if (labelCompare !== 0) return labelCompare;
      return a.id - b.id;
    });
}

/**
 * Computes timezone-aware ISO-8601 `started_at` and `ended_at` timestamps from an
 * employee-confirmed duration in whole seconds (`0..86400`).
 *
 * Since Android `tel:` dialer handoff does not expose carrier call connection timestamps
 * to unprivileged apps, `ended_at` is anchored to `referenceEndTime` (never in the future)
 * and `started_at` is calculated as `ended_at - durationSeconds`.
 */
export function computeCallTimestampsFromDuration(
  durationSeconds: number,
  referenceEndTime: Date = new Date()
): { started_at: string; ended_at: string; duration_seconds: number } {
  if (!Number.isFinite(durationSeconds) || durationSeconds < 0) {
    throw new Error('Call duration cannot be negative.');
  }
  const wholeSeconds = Math.floor(durationSeconds);
  if (wholeSeconds > MAX_CALL_DURATION_SECONDS) {
    throw new Error('Call duration cannot exceed 24 hours (86,400 seconds).');
  }

  const nowMs = Date.now();
  const endMs = Math.min(referenceEndTime.getTime(), nowMs);
  const startMs = endMs - wholeSeconds * 1000;

  return {
    started_at: toExplicitOffsetIsoString(new Date(startMs)),
    ended_at: toExplicitOffsetIsoString(new Date(endMs)),
    duration_seconds: wholeSeconds,
  };
}

export interface SubmitCallResult {
  call: CallSubmissionResponse;
  created: boolean;
  status: number;
}

/**
 * Submits a call log to `POST /api/v1/calls/` using the exact `CallSubmissionSerializer` payload.
 * Preserves `client_event_id` (and attaches `Idempotency-Key` header) for idempotent retry safety.
 */
export async function submitCallOutcome(
  payload: CallSubmissionRequest,
  signal?: AbortSignal
): Promise<SubmitCallResult> {
  if (!Number.isInteger(payload.lead_id) || payload.lead_id <= 0) {
    throw new Error('A valid assigned lead is required to log a call.');
  }
  if (!isValidUuid(payload.client_event_id)) {
    throw new Error('A valid UUID client_event_id is required.');
  }
  const outcomeCode = payload.outcome_code?.trim();
  if (!outcomeCode) {
    throw new Error('Please select a call outcome.');
  }
  if (!payload.started_at || !payload.ended_at) {
    throw new Error('Call start and end timestamps are required.');
  }

  const normalizedNotes = (payload.employee_notes ?? '').trim();
  if (normalizedNotes.length > MAX_EMPLOYEE_NOTES_LENGTH) {
    throw new Error(
      `Call notes cannot exceed ${MAX_EMPLOYEE_NOTES_LENGTH} characters.`
    );
  }

  const normalizedEventId = payload.client_event_id.trim().toLowerCase();

  // Construct strict body with only the 6 fields accepted by CallSubmissionSerializer
  const requestBody: CallSubmissionRequest = {
    lead_id: payload.lead_id,
    client_event_id: normalizedEventId,
    outcome_code: outcomeCode,
    started_at: payload.started_at,
    ended_at: payload.ended_at,
    employee_notes: normalizedNotes,
  };

  const { data, status } = await apiRequestWithStatus<CallSubmissionResponse>(
    '/calls/',
    {
      method: 'POST',
      auth: true,
      headers: {
        'Idempotency-Key': normalizedEventId,
      },
      body: requestBody,
      signal,
    }
  );

  return {
    call: data,
    created: status === 201,
    status,
  };
}

/**
 * Builds query string for `GET /api/v1/calls/` using only parameters permitted for `EMPLOYEE` users.
 * Never includes admin-only `caller_id`.
 */
export function buildCallHistoryQueryString(
  params: CallHistoryQueryParams = {}
): string {
  const searchParams = new URLSearchParams();

  const page = params.page && params.page >= 1 ? Math.floor(params.page) : 1;
  const pageSize =
    params.page_size && params.page_size >= 1
      ? Math.min(Math.floor(params.page_size), 100)
      : DEFAULT_LEADS_PAGE_SIZE;

  searchParams.set('page', String(page));
  searchParams.set('page_size', String(pageSize));

  if (params.lead_id && params.lead_id >= 1) {
    searchParams.set('lead_id', String(Math.floor(params.lead_id)));
  }
  if (params.outcome_code?.trim()) {
    searchParams.set('outcome_code', params.outcome_code.trim());
  }
  if (params.started_at_from?.trim()) {
    searchParams.set('started_at_from', params.started_at_from.trim());
  }
  if (params.started_at_to?.trim()) {
    searchParams.set('started_at_to', params.started_at_to.trim());
  }

  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

/**
 * Fetches paginated call history for the authenticated employee (`GET /api/v1/calls/`).
 */
export async function fetchMyCallHistory(
  params: CallHistoryQueryParams = {},
  signal?: AbortSignal
): Promise<PaginatedResponse<CallHistoryItem>> {
  const queryString = buildCallHistoryQueryString(params);
  const response = await apiRequest<PaginatedResponse<unknown>>(
    `/calls/${queryString}`,
    {
      method: 'GET',
      auth: true,
      signal,
    }
  );

  const results = Array.isArray(response?.results)
    ? response.results.map(sanitizeCallHistoryItem)
    : [];

  return {
    count: typeof response?.count === 'number' ? response.count : results.length,
    next: typeof response?.next === 'string' ? response.next : null,
    previous: typeof response?.previous === 'string' ? response.previous : null,
    results,
  };
}
