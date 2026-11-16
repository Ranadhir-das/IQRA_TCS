import { DEFAULT_LEADS_PAGE_SIZE } from '../constants/config.ts';
import { LEAD_STATUS_META } from '../constants/leads.ts';
import type {
  AssignedLeadsQueryParams,
  EmployeeLead,
  LeadStatus,
  PaginatedResponse,
} from '../types/index.ts';
import { apiRequest } from './api.ts';
import { cacheLeadPhone } from './followUps.ts';

function normalizeLeadStatus(rawStatus: unknown): LeadStatus {
  if (typeof rawStatus === 'string' && rawStatus in LEAD_STATUS_META) {
    return rawStatus as LeadStatus;
  }
  return 'NEW';
}

/**
 * Sanitizes a raw lead object into the strict `EmployeeLead` contract (`EmployeeLeadSerializer`),
 * ensuring admin-only fields such as `internal_notes` are never retained.
 */
export function sanitizeEmployeeLead(raw: unknown): EmployeeLead {
  const record = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const lead: EmployeeLead = {
    id: typeof record.id === 'number' ? record.id : Number(record.id ?? 0),
    name: typeof record.name === 'string' ? record.name : '',
    phone: typeof record.phone === 'string' ? record.phone : '',
    alternate_phone: typeof record.alternate_phone === 'string' ? record.alternate_phone : '',
    email: typeof record.email === 'string' ? record.email : '',
    course_interest: typeof record.course_interest === 'string' ? record.course_interest : '',
    enquiry_details: typeof record.enquiry_details === 'string' ? record.enquiry_details : '',
    status: normalizeLeadStatus(record.status),
    created_at: typeof record.created_at === 'string' ? record.created_at : '',
    updated_at: typeof record.updated_at === 'string' ? record.updated_at : '',
  };
  if (lead.id > 0 && lead.phone) {
    cacheLeadPhone(lead.id, lead.phone);
  }
  return lead;
}

/**
 * Builds query string for `GET /api/v1/leads/` using only parameters permitted for `EMPLOYEE` users.
 * Never includes admin-only parameters (`assigned_to`, `unassigned`, `archived`).
 */
export function buildAssignedLeadsQueryString(params: AssignedLeadsQueryParams = {}): string {
  const searchParams = new URLSearchParams();

  const page = params.page && params.page >= 1 ? Math.floor(params.page) : 1;
  const pageSize =
    params.page_size && params.page_size >= 1
      ? Math.min(Math.floor(params.page_size), 100)
      : DEFAULT_LEADS_PAGE_SIZE;

  searchParams.set('page', String(page));
  searchParams.set('page_size', String(pageSize));

  const trimmedSearch = params.search?.trim();
  if (trimmedSearch) {
    searchParams.set('search', trimmedSearch);
  }

  if (params.status && params.status in LEAD_STATUS_META) {
    searchParams.set('status', params.status);
  }

  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

/**
 * Fetches paginated leads assigned to the authenticated employee (`GET /api/v1/leads/`).
 */
export async function fetchAssignedLeads(
  params: AssignedLeadsQueryParams = {},
  signal?: AbortSignal
): Promise<PaginatedResponse<EmployeeLead>> {
  const queryString = buildAssignedLeadsQueryString(params);
  const response = await apiRequest<PaginatedResponse<unknown>>(`/leads/${queryString}`, {
    method: 'GET',
    auth: true,
    signal,
  });

  const results = Array.isArray(response?.results)
    ? response.results.map(sanitizeEmployeeLead)
    : [];

  return {
    count: typeof response?.count === 'number' ? response.count : results.length,
    next: typeof response?.next === 'string' ? response.next : null,
    previous: typeof response?.previous === 'string' ? response.previous : null,
    results,
  };
}

/**
 * Fetches a single assigned lead's details (`GET /api/v1/leads/<id>/`).
 */
export async function fetchAssignedLeadDetail(
  leadId: number | string,
  signal?: AbortSignal
): Promise<EmployeeLead> {
  const numericId = Number(leadId);
  if (!Number.isInteger(numericId) || numericId <= 0) {
    throw new Error('Invalid lead ID.');
  }

  const response = await apiRequest<unknown>(`/leads/${encodeURIComponent(String(numericId))}/`, {
    method: 'GET',
    auth: true,
    signal,
  });

  return sanitizeEmployeeLead(response);
}
