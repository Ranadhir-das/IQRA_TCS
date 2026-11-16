import { API_BASE_URL, DEFAULT_REQUEST_TIMEOUT_MS } from '../constants/config.ts';
import type { TokenRefreshResponse } from '../types/index.ts';
import {
  clearStoredRefreshToken,
  getStoredRefreshToken,
  setStoredRefreshToken,
} from './secureStorage.ts';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiRequestOptions {
  method?: HttpMethod;
  body?: unknown;
  /**
   * Additional HTTP headers (e.g. `Idempotency-Key`). Preserved across 401 token refresh retries.
   */
  headers?: Record<string, string>;
  /**
   * Whether to attach `Authorization: Bearer <accessToken>` and attempt
   * automatic token refresh on HTTP 401. Defaults to `true`.
   */
  auth?: boolean;
  /**
   * Optional explicit access token override (e.g. during initial login verification).
   */
  accessTokenOverride?: string;
  /**
   * Disables automatic 401 token refresh for this request (e.g. logout / refresh).
   */
  skipAuthRefresh?: boolean;
  timeoutMs?: number;
  signal?: AbortSignal;
  _retried?: boolean;
}

export interface ApiResponseWithStatus<T> {
  data: T;
  status: number;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly fieldErrors: Record<string, string[]>;
  public readonly isNetworkError: boolean;
  public readonly isTimeout: boolean;
  public readonly isConflict: boolean;

  constructor(
    message: string,
    status: number,
    options: {
      fieldErrors?: Record<string, string[]>;
      isNetworkError?: boolean;
      isTimeout?: boolean;
    } = {}
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fieldErrors = options.fieldErrors ?? {};
    this.isNetworkError = options.isNetworkError ?? false;
    this.isTimeout = options.isTimeout ?? false;
    this.isConflict = status === 409;
  }
}

/**
 * Access token is held strictly in memory and never persisted to disk.
 */
let inMemoryAccessToken: string | null = null;

/**
 * Single-flight mutex promise for token refresh to prevent concurrent 401 responses
 * from racing against `ROTATE_REFRESH_TOKENS=True` + `BLACKLIST_AFTER_ROTATION=True`.
 */
let activeRefreshPromise: Promise<string> | null = null;

type SessionInvalidatedListener = (reason: string) => void;
let sessionInvalidatedListener: SessionInvalidatedListener | null = null;

export function setSessionInvalidatedListener(listener: SessionInvalidatedListener | null): void {
  sessionInvalidatedListener = listener;
}

export function getAccessToken(): string | null {
  return inMemoryAccessToken;
}

export function setAccessToken(token: string | null): void {
  inMemoryAccessToken = token ? token.trim() : null;
}

export async function clearSessionTokens(): Promise<void> {
  inMemoryAccessToken = null;
  await clearStoredRefreshToken();
}

function notifySessionInvalidated(reason: string): void {
  if (sessionInvalidatedListener) {
    sessionInvalidatedListener(reason);
  }
}

/**
 * Extracts normalized, user-friendly error messages and per-field errors from DRF JSON responses.
 */
export function parseDrfErrorPayload(
  data: unknown,
  status: number
): { message: string; fieldErrors: Record<string, string[]> } {
  const fieldErrors: Record<string, string[]> = {};

  if (status === 429) {
    const detail =
      data && typeof data === 'object' && 'detail' in data && typeof (data as Record<string, unknown>).detail === 'string'
        ? ((data as Record<string, unknown>).detail as string)
        : 'Too many requests. Please wait a moment and try again.';
    return { message: detail, fieldErrors };
  }

  if (!data || typeof data !== 'object') {
    if (status === 401) {
      return { message: 'Your session has expired. Please sign in again.', fieldErrors };
    }
    if (status === 403) {
      return { message: 'You do not have permission to perform this action.', fieldErrors };
    }
    if (status === 404) {
      return { message: 'The requested record was not found.', fieldErrors };
    }
    if (status === 409) {
      return { message: 'This action conflicts with the current record state.', fieldErrors };
    }
    if (status >= 500) {
      return { message: 'The IQRA TCS server encountered an error. Please try again shortly.', fieldErrors };
    }
    return { message: `Request failed (HTTP ${status}).`, fieldErrors };
  }

  const record = data as Record<string, unknown>;

  for (const [key, value] of Object.entries(record)) {
    if (key === 'detail') continue;
    if (Array.isArray(value)) {
      const messages = value.filter((item): item is string => typeof item === 'string');
      if (messages.length > 0) {
        fieldErrors[key] = messages;
      }
    } else if (typeof value === 'string') {
      fieldErrors[key] = [value];
    }
  }

  if (typeof record.detail === 'string' && record.detail.trim()) {
    return { message: record.detail.trim(), fieldErrors };
  }

  if (fieldErrors.non_field_errors?.length) {
    return { message: fieldErrors.non_field_errors[0], fieldErrors };
  }

  const firstFieldEntry = Object.entries(fieldErrors)[0];
  if (firstFieldEntry && firstFieldEntry[1].length > 0) {
    return { message: firstFieldEntry[1][0], fieldErrors };
  }

  return { message: `Request failed (HTTP ${status}).`, fieldErrors };
}

/**
 * Performs a fetch with a bounded timeout and merged AbortSignal.
 */
async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  externalSignal?: AbortSignal
): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;

  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener('abort', onExternalAbort);
    }
  }

  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(url, {
      ...init,
      signal: controller.signal,
    });
  } catch (error) {
    if (timedOut) {
      throw new ApiError(
        'The request timed out. Please check your internet connection and try again.',
        0,
        { isNetworkError: true, isTimeout: true }
      );
    }
    if (externalSignal?.aborted) {
      throw error;
    }
    throw new ApiError(
      'Unable to reach the IQRA TCS server. Please check your internet connection and try again.',
      0,
      { isNetworkError: true }
    );
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', onExternalAbort);
  }
}

/**
 * Single-flight token refresh using `POST /api/v1/auth/refresh/`.
 * Persists rotated refresh token in `expo-secure-store` and updates in-memory access token.
 */
export async function refreshAccessTokenSingleFlight(): Promise<string> {
  if (activeRefreshPromise) {
    return activeRefreshPromise;
  }

  activeRefreshPromise = (async () => {
    const storedRefresh = await getStoredRefreshToken();
    if (!storedRefresh) {
      await clearSessionTokens();
      throw new ApiError('Your session has expired. Please sign in again.', 401);
    }

    const url = `${API_BASE_URL}/auth/refresh/`;
    let response: Response;
    try {
      response = await fetchWithTimeout(
        url,
        {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
          },
          body: JSON.stringify({ refresh: storedRefresh }),
        },
        DEFAULT_REQUEST_TIMEOUT_MS
      );
    } catch (error) {
      // Do not wipe stored refresh token on transient offline/network error
      throw error;
    }

    let data: unknown = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      if (response.status === 401 || response.status === 400 || response.status === 403) {
        await clearSessionTokens();
        const parsed = parseDrfErrorPayload(data, response.status);
        notifySessionInvalidated(parsed.message);
        throw new ApiError(parsed.message, response.status, { fieldErrors: parsed.fieldErrors });
      }
      const parsed = parseDrfErrorPayload(data, response.status);
      throw new ApiError(parsed.message, response.status, { fieldErrors: parsed.fieldErrors });
    }

    const payload = data as TokenRefreshResponse | null;
    if (!payload || typeof payload.access !== 'string' || !payload.access.trim()) {
      await clearSessionTokens();
      notifySessionInvalidated('Invalid session response from server.');
      throw new ApiError('Your session has expired. Please sign in again.', 401);
    }

    if (typeof payload.refresh === 'string' && payload.refresh.trim()) {
      await setStoredRefreshToken(payload.refresh);
    }
    setAccessToken(payload.access);
    return payload.access;
  })();

  try {
    return await activeRefreshPromise;
  } finally {
    activeRefreshPromise = null;
  }
}

/**
 * Centralized API request function returning both parsed data and HTTP status code
 * (e.g. to distinguish HTTP 201 Created from HTTP 200 OK idempotent retries).
 */
export async function apiRequestWithStatus<T>(
  endpoint: string,
  options: ApiRequestOptions = {}
): Promise<ApiResponseWithStatus<T>> {
  const {
    method = 'GET',
    body,
    headers: customHeaders,
    auth = true,
    accessTokenOverride,
    skipAuthRefresh = false,
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    signal,
    _retried = false,
  } = options;

  if (!endpoint.startsWith('/')) {
    throw new Error(`API endpoint must start with "/": received "${endpoint}"`);
  }

  let tokenToUse = accessTokenOverride ?? (auth ? inMemoryAccessToken : null);

  if (auth && !tokenToUse && !skipAuthRefresh) {
    tokenToUse = await refreshAccessTokenSingleFlight();
  }

  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    Pragma: 'no-cache',
    ...(customHeaders ?? {}),
  };

  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  if (tokenToUse) {
    headers.Authorization = `Bearer ${tokenToUse}`;
  }

  const response = await fetchWithTimeout(
    `${API_BASE_URL}${endpoint}`,
    {
      method,
      headers,
      body: body !== undefined ? (typeof body === 'string' ? body : JSON.stringify(body)) : undefined,
    },
    timeoutMs,
    signal
  );

  if (response.status === 401 && auth && !skipAuthRefresh && !_retried) {
    const refreshedAccessToken = await refreshAccessTokenSingleFlight();
    return apiRequestWithStatus<T>(endpoint, {
      ...options,
      accessTokenOverride: refreshedAccessToken,
      _retried: true,
    });
  }

  let data: unknown = null;
  if (response.status !== 204) {
    try {
      data = await response.json();
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const parsed = parseDrfErrorPayload(data, response.status);
    if (
      response.status === 401 &&
      auth &&
      !skipAuthRefresh
    ) {
      await clearSessionTokens();
      notifySessionInvalidated(parsed.message);
    }
    throw new ApiError(parsed.message, response.status, {
      fieldErrors: parsed.fieldErrors,
    });
  }

  return {
    data: data as T,
    status: response.status,
  };
}

/**
 * Centralized API request function for all IQRA TCS `/api/v1` endpoints.
 */
export async function apiRequest<T>(
  endpoint: string,
  options: ApiRequestOptions = {}
): Promise<T> {
  const result = await apiRequestWithStatus<T>(endpoint, options);
  return result.data;
}
