import type {
  ApiMessageResponse,
  ChangePasswordRequest,
  LoginResponse,
  LoginUserSummary,
  UserProfile,
} from '../types/index.ts';
import {
  ApiError,
  apiRequest,
  clearSessionTokens,
  getAccessToken,
  refreshAccessTokenSingleFlight,
  setAccessToken,
} from './api.ts';
import {
  getStoredRefreshToken,
  setStoredRefreshToken,
} from './secureStorage.ts';
import { clearLeadPhoneCache } from './followUps.ts';

export const NON_EMPLOYEE_ROLE_MESSAGE =
  'This mobile application is for IQRA TCS calling employees only. Administrators should sign in to the web CRM.';
export const INACTIVE_ACCOUNT_MESSAGE =
  'This employee account is inactive. Please contact your administrator.';

/**
 * Verifies that the authenticated user is an active EMPLOYEE (`role === 'EMPLOYEE' && is_active === true`).
 */
export function assertActiveEmployeeUser(
  user: Pick<LoginUserSummary | UserProfile, 'role' | 'is_active'> | null | undefined
): void {
  if (!user) {
    throw new ApiError('Unable to verify employee account details.', 401);
  }
  if (!user.is_active) {
    throw new ApiError(INACTIVE_ACCOUNT_MESSAGE, 403);
  }
  if (user.role !== 'EMPLOYEE') {
    throw new ApiError(NON_EMPLOYEE_ROLE_MESSAGE, 403);
  }
}

/**
 * Best-effort server token revocation (`POST /api/v1/auth/logout/`).
 */
async function revokeRefreshTokenBestEffort(
  refreshToken: string,
  accessToken: string | null
): Promise<void> {
  if (!refreshToken || !accessToken) return;
  try {
    await apiRequest<ApiMessageResponse>('/auth/logout/', {
      method: 'POST',
      auth: true,
      accessTokenOverride: accessToken,
      skipAuthRefresh: true,
      timeoutMs: 5_000,
      body: { refresh: refreshToken },
    });
  } catch {
    // Ignore network or token expiry errors during best-effort revocation.
  }
}

/**
 * Fetches the authenticated employee's full profile from `GET /api/v1/auth/me/`.
 */
export async function fetchCurrentUser(): Promise<UserProfile> {
  const profile = await apiRequest<UserProfile>('/auth/me/', {
    method: 'GET',
    auth: true,
  });
  assertActiveEmployeeUser(profile);
  return profile;
}

/**
 * Authenticates an employee against `POST /api/v1/auth/login/`, verifies `EMPLOYEE` role
 * and `is_active` status, stores the refresh token in Android secure storage, keeps the
 * access token in memory, and loads the complete employee profile from `GET /api/v1/auth/me/`.
 */
export async function loginEmployee(
  username: string,
  password: string
): Promise<UserProfile> {
  const normalizedUsername = username.trim();
  const response = await apiRequest<LoginResponse>('/auth/login/', {
    method: 'POST',
    auth: false,
    skipAuthRefresh: true,
    body: {
      username: normalizedUsername,
      password,
    },
  });

  if (!response?.access || !response?.refresh || !response?.user) {
    throw new ApiError('Unexpected authentication response from server.', 500);
  }

  try {
    assertActiveEmployeeUser(response.user);
  } catch (roleError) {
    await revokeRefreshTokenBestEffort(response.refresh, response.access);
    await clearSessionTokens();
    throw roleError;
  }

  setAccessToken(response.access);
  await setStoredRefreshToken(response.refresh);

  try {
    const fullProfile = await fetchCurrentUser();
    return fullProfile;
  } catch (error) {
    await revokeRefreshTokenBestEffort(response.refresh, response.access);
    await clearSessionTokens();
    throw error;
  }
}

/**
 * Restores an existing employee session on app launch using the refresh token
 * stored in Android secure storage (`POST /api/v1/auth/refresh/` + `GET /api/v1/auth/me/`).
 */
export async function restoreEmployeeSession(): Promise<UserProfile | null> {
  const storedRefresh = await getStoredRefreshToken();
  if (!storedRefresh) {
    await clearSessionTokens();
    return null;
  }

  try {
    await refreshAccessTokenSingleFlight();
    const profile = await fetchCurrentUser();
    return profile;
  } catch (error) {
    await clearSessionTokens();
    if (error instanceof ApiError && (error.status === 401 || error.status === 403 || error.status === 400)) {
      return null;
    }
    throw error;
  }
}

/**
 * Logs out the current employee (`POST /api/v1/auth/logout/`) and always clears
 * in-memory and SecureStore credentials.
 */
export async function logoutEmployee(): Promise<void> {
  const refreshToken = await getStoredRefreshToken();
  const accessToken = getAccessToken();
  try {
    if (refreshToken && accessToken) {
      await revokeRefreshTokenBestEffort(refreshToken, accessToken);
    }
  } finally {
    clearLeadPhoneCache();
    await clearSessionTokens();
  }
}

/**
 * Changes the authenticated employee's password via `POST /api/v1/auth/change-password/`.
 * Note: The backend blacklists all active refresh tokens upon password change, so local
 * tokens are cleared after success and the employee signs in with the new password.
 */
export async function changeEmployeePassword(
  payload: ChangePasswordRequest
): Promise<string> {
  const response = await apiRequest<ApiMessageResponse>('/auth/change-password/', {
    method: 'POST',
    auth: true,
    body: payload,
  });
  await clearSessionTokens();
  return response?.detail || 'Password has been updated successfully.';
}
