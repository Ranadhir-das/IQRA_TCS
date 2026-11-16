import { DEFAULT_LEADS_PAGE_SIZE } from '../constants/config.ts';
import type {
  EmployeeFollowUp,
  FollowUpCreateRequest,
  FollowUpDueState,
  FollowUpListQueryParams,
  FollowUpRescheduleRequest,
  FollowUpStatus,
  PaginatedResponse,
} from '../types/index.ts';
import { isValidUuid } from '../utils/idempotency.ts';
import { apiRequest, apiRequestWithStatus } from './api.ts';
import { fetchAssignedLeadDetail } from './leads.ts';

export const MAX_FOLLOWUP_NOTES_LENGTH = 5_000;
export const MAX_FOLLOWUP_REASON_LENGTH = 1_000;

const VALID_STATUSES: Set<FollowUpStatus> = new Set([
  'PENDING',
  'COMPLETED',
  'CANCELLED',
]);

const VALID_DUE_STATES: Set<FollowUpDueState> = new Set([
  'today',
  'overdue',
  'upcoming',
]);

/**
 * In-memory cache of lead phone numbers keyed by `lead_id` so the My Follow-Ups
 * screen can display the lead's phone number alongside `lead_name` without N+1 refetching.
 */
const leadPhoneCache = new Map<number, string>();

export function cacheLeadPhone(leadId: number, phone: string): void {
  if (leadId > 0 && phone.trim()) {
    leadPhoneCache.set(leadId, phone.trim());
  }
}

export function clearLeadPhoneCache(): void {
  leadPhoneCache.clear();
}

export function sanitizeEmployeeFollowUp(raw: unknown): EmployeeFollowUp {
  const record = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const leadId = typeof record.lead_id === 'number' ? record.lead_id : Number(record.lead_id ?? 0);
  const rawStatus = typeof record.status === 'string' ? record.status : 'PENDING';
  const status: FollowUpStatus = VALID_STATUSES.has(rawStatus as FollowUpStatus)
    ? (rawStatus as FollowUpStatus)
    : 'PENDING';

  const directPhone =
    typeof record.lead_phone === 'string' && record.lead_phone.trim()
      ? record.lead_phone.trim()
      : leadPhoneCache.get(leadId);

  return {
    id: typeof record.id === 'number' ? record.id : Number(record.id ?? 0),
    lead_id: leadId,
    lead_name: typeof record.lead_name === 'string' ? record.lead_name : '',
    lead_phone: directPhone,
    call_id:
      typeof record.call_id === 'number'
        ? record.call_id
        : record.call_id != null
          ? Number(record.call_id)
          : null,
    scheduled_at: typeof record.scheduled_at === 'string' ? record.scheduled_at : '',
    status,
    notes: typeof record.notes === 'string' ? record.notes : '',
    completed_at: typeof record.completed_at === 'string' ? record.completed_at : null,
    cancelled_at: typeof record.cancelled_at === 'string' ? record.cancelled_at : null,
    created_at: typeof record.created_at === 'string' ? record.created_at : '',
    updated_at: typeof record.updated_at === 'string' ? record.updated_at : '',
  };
}

/**
 * Validates that a scheduled_at string is a valid timezone-aware ISO timestamp in the future.
 */
export function validateFutureFollowUpSchedule(
  scheduledAtIso: string,
  nowMs: number = Date.now()
): Date {
  if (!scheduledAtIso || typeof scheduledAtIso !== 'string') {
    throw new Error('Please select a date and time for the follow-up.');
  }

  // Must include explicit timezone offset ('Z' or '+HH:MM' / '-HH:MM')
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(scheduledAtIso.trim())) {
    throw new Error(
      'Follow-up timestamp must include an explicit timezone offset.'
    );
  }

  const parsed = new Date(scheduledAtIso);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error('Invalid follow-up date and time.');
  }

  if (parsed.getTime() <= nowMs) {
    throw new Error('Follow-up date and time must be in the future.');
  }

  return parsed;
}

export interface CreateFollowUpResult {
  followUp: EmployeeFollowUp;
  created: boolean;
  status: number;
}

/**
 * Creates a follow-up via `POST /api/v1/follow-ups/` with required `Idempotency-Key` UUID header.
 */
export async function createFollowUp(
  payload: FollowUpCreateRequest,
  idempotencyKey: string,
  signal?: AbortSignal
): Promise<CreateFollowUpResult> {
  if (!Number.isInteger(payload.lead_id) || payload.lead_id <= 0) {
    throw new Error('A valid assigned lead is required to schedule a follow-up.');
  }
  if (!isValidUuid(idempotencyKey)) {
    throw new Error('A valid UUID Idempotency-Key is required.');
  }

  validateFutureFollowUpSchedule(payload.scheduled_at);

  const normalizedNotes = (payload.notes ?? '').trim();
  if (normalizedNotes.length > MAX_FOLLOWUP_NOTES_LENGTH) {
    throw new Error(
      `Follow-up notes cannot exceed ${MAX_FOLLOWUP_NOTES_LENGTH} characters.`
    );
  }

  const body: Record<string, unknown> = {
    lead_id: payload.lead_id,
    scheduled_at: payload.scheduled_at.trim(),
    notes: normalizedNotes,
  };

  if (payload.call_id !== undefined && payload.call_id !== null) {
    body.call_id = payload.call_id;
  }

  const { data, status } = await apiRequestWithStatus<unknown>('/follow-ups/', {
    method: 'POST',
    auth: true,
    headers: {
      'Idempotency-Key': idempotencyKey.trim().toLowerCase(),
    },
    body,
    signal,
  });

  return {
    followUp: sanitizeEmployeeFollowUp(data),
    created: status === 201,
    status,
  };
}

/**
 * Reschedules a pending follow-up via `POST /api/v1/follow-ups/<id>/reschedule/`
 * with required `Idempotency-Key` UUID header.
 */
export async function rescheduleFollowUp(
  followUpId: number,
  payload: FollowUpRescheduleRequest,
  idempotencyKey: string,
  signal?: AbortSignal
): Promise<EmployeeFollowUp> {
  if (!Number.isInteger(followUpId) || followUpId <= 0) {
    throw new Error('Invalid follow-up ID.');
  }
  if (!isValidUuid(idempotencyKey)) {
    throw new Error('A valid UUID Idempotency-Key is required.');
  }

  validateFutureFollowUpSchedule(payload.scheduled_at);

  const body: Record<string, unknown> = {
    scheduled_at: payload.scheduled_at.trim(),
  };

  if (payload.notes !== undefined) {
    const trimmedNotes = payload.notes.trim();
    if (trimmedNotes.length > MAX_FOLLOWUP_NOTES_LENGTH) {
      throw new Error(
        `Follow-up notes cannot exceed ${MAX_FOLLOWUP_NOTES_LENGTH} characters.`
      );
    }
    body.notes = trimmedNotes;
  }

  if (payload.reason !== undefined) {
    const trimmedReason = payload.reason.trim();
    if (trimmedReason.length > MAX_FOLLOWUP_REASON_LENGTH) {
      throw new Error(
        `Reschedule reason cannot exceed ${MAX_FOLLOWUP_REASON_LENGTH} characters.`
      );
    }
    body.reason = trimmedReason;
  }

  const response = await apiRequest<unknown>(
    `/follow-ups/${encodeURIComponent(String(followUpId))}/reschedule/`,
    {
      method: 'POST',
      auth: true,
      headers: {
        'Idempotency-Key': idempotencyKey.trim().toLowerCase(),
      },
      body,
      signal,
    }
  );

  return sanitizeEmployeeFollowUp(response);
}

/**
 * Marks a pending follow-up completed via `POST /api/v1/follow-ups/<id>/complete/`.
 * Note: `FollowUpCompleteSerializer` rejects any non-empty request body fields, so `{}` is sent.
 */
export async function completeFollowUp(
  followUpId: number,
  signal?: AbortSignal
): Promise<EmployeeFollowUp> {
  if (!Number.isInteger(followUpId) || followUpId <= 0) {
    throw new Error('Invalid follow-up ID.');
  }

  const response = await apiRequest<unknown>(
    `/follow-ups/${encodeURIComponent(String(followUpId))}/complete/`,
    {
      method: 'POST',
      auth: true,
      body: {},
      signal,
    }
  );

  return sanitizeEmployeeFollowUp(response);
}

/**
 * Cancels a pending follow-up via `POST /api/v1/follow-ups/<id>/cancel/`
 * with an optional reason (`FollowUpCancelSerializer`).
 */
export async function cancelFollowUp(
  followUpId: number,
  reason = '',
  signal?: AbortSignal
): Promise<EmployeeFollowUp> {
  if (!Number.isInteger(followUpId) || followUpId <= 0) {
    throw new Error('Invalid follow-up ID.');
  }

  const trimmedReason = reason.trim();
  if (trimmedReason.length > MAX_FOLLOWUP_REASON_LENGTH) {
    throw new Error(
      `Cancellation reason cannot exceed ${MAX_FOLLOWUP_REASON_LENGTH} characters.`
    );
  }

  const response = await apiRequest<unknown>(
    `/follow-ups/${encodeURIComponent(String(followUpId))}/cancel/`,
    {
      method: 'POST',
      auth: true,
      body: { reason: trimmedReason },
      signal,
    }
  );

  return sanitizeEmployeeFollowUp(response);
}

/**
 * Builds query string for `GET /api/v1/follow-ups/` using only employee-permitted parameters.
 * Never includes admin-only `employee_id`.
 */
export function buildFollowUpsQueryString(
  params: FollowUpListQueryParams = {}
): string {
  const searchParams = new URLSearchParams();

  const page = params.page && params.page >= 1 ? Math.floor(params.page) : 1;
  const pageSize =
    params.page_size && params.page_size >= 1
      ? Math.min(Math.floor(params.page_size), 100)
      : DEFAULT_LEADS_PAGE_SIZE;

  searchParams.set('page', String(page));
  searchParams.set('page_size', String(pageSize));

  if (params.status && VALID_STATUSES.has(params.status)) {
    searchParams.set('status', params.status);
  }
  if (params.lead_id && params.lead_id >= 1) {
    searchParams.set('lead_id', String(Math.floor(params.lead_id)));
  }
  if (params.scheduled_from?.trim()) {
    searchParams.set('scheduled_from', params.scheduled_from.trim());
  }
  if (params.scheduled_to?.trim()) {
    searchParams.set('scheduled_to', params.scheduled_to.trim());
  }
  if (params.due_state && VALID_DUE_STATES.has(params.due_state)) {
    searchParams.set('due_state', params.due_state);
  }

  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

/**
 * Enriches follow-ups with lead phone numbers from `GET /api/v1/leads/<id>/` if not yet cached.
 */
async function enrichFollowUpsWithLeadPhones(
  items: EmployeeFollowUp[],
  signal?: AbortSignal
): Promise<EmployeeFollowUp[]> {
  const missingLeadIds = Array.from(
    new Set(
      items
        .filter((item) => item.lead_id > 0 && !item.lead_phone && !leadPhoneCache.has(item.lead_id))
        .map((item) => item.lead_id)
    )
  );

  if (missingLeadIds.length > 0) {
    await Promise.all(
      missingLeadIds.map(async (leadId) => {
        try {
          const lead = await fetchAssignedLeadDetail(leadId, signal);
          if (lead.phone) {
            cacheLeadPhone(leadId, lead.phone);
          }
        } catch {
          // Ignore if lead was later reassigned or archived.
        }
      })
    );
  }

  return items.map((item) => ({
    ...item,
    lead_phone: item.lead_phone || leadPhoneCache.get(item.lead_id),
  }));
}

/**
 * Fetches paginated follow-ups for the authenticated employee (`GET /api/v1/follow-ups/`).
 */
export async function fetchMyFollowUps(
  params: FollowUpListQueryParams = {},
  options: { signal?: AbortSignal; enrichLeadPhones?: boolean } = {}
): Promise<PaginatedResponse<EmployeeFollowUp>> {
  const { signal, enrichLeadPhones = true } = options;
  const queryString = buildFollowUpsQueryString(params);
  const response = await apiRequest<PaginatedResponse<unknown>>(
    `/follow-ups/${queryString}`,
    {
      method: 'GET',
      auth: true,
      signal,
    }
  );

  let results = Array.isArray(response?.results)
    ? response.results.map(sanitizeEmployeeFollowUp)
    : [];

  if (enrichLeadPhones && results.length > 0) {
    results = await enrichFollowUpsWithLeadPhones(results, signal);
  }

  return {
    count: typeof response?.count === 'number' ? response.count : results.length,
    next: typeof response?.next === 'string' ? response.next : null,
    previous: typeof response?.previous === 'string' ? response.previous : null,
    results,
  };
}

/**
 * Classifies a follow-up's schedule relative to `now` for UI badges.
 */
export function getFollowUpTimingCategory(
  followUp: Pick<EmployeeFollowUp, 'status' | 'scheduled_at'>,
  now: Date = new Date()
): 'overdue' | 'today' | 'upcoming' | 'completed' | 'cancelled' {
  if (followUp.status === 'COMPLETED') return 'completed';
  if (followUp.status === 'CANCELLED') return 'cancelled';

  const scheduled = new Date(followUp.scheduled_at);
  if (Number.isNaN(scheduled.getTime())) return 'upcoming';

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfTomorrow = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate() + 1
  );

  if (scheduled.getTime() < startOfToday.getTime()) {
    return 'overdue';
  }
  if (scheduled.getTime() < startOfTomorrow.getTime()) {
    return scheduled.getTime() < now.getTime() ? 'overdue' : 'today';
  }
  return 'upcoming';
}
