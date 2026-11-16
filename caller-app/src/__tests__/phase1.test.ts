import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import {
  DEFAULT_DEVELOPMENT_API_BASE_URL,
  DEFAULT_PRODUCTION_API_BASE_URL,
  resolveApiBaseUrl,
} from '../constants/config.ts';
import {
  ApiError,
  apiRequest,
  clearSessionTokens,
  getAccessToken,
  setAccessToken,
  setSessionInvalidatedListener,
} from '../services/api.ts';
import {
  INACTIVE_ACCOUNT_MESSAGE,
  NON_EMPLOYEE_ROLE_MESSAGE,
  changeEmployeePassword,
  loginEmployee,
  logoutEmployee,
  restoreEmployeeSession,
} from '../services/auth.ts';
import {
  buildAssignedLeadsQueryString,
  fetchAssignedLeadDetail,
  fetchAssignedLeads,
  sanitizeEmployeeLead,
} from '../services/leads.ts';
import {
  getStoredRefreshToken,
  setSecureStoreAdapter,
  setStoredRefreshToken,
} from '../services/secureStorage.ts';
import {
  validateChangePasswordForm,
  validateLoginForm,
} from '../utils/validation.ts';

function createMemorySecureStore() {
  const map = new Map<string, string>();
  return {
    map,
    adapter: {
      async getItemAsync(key: string) {
        return map.get(key) ?? null;
      },
      async setItemAsync(key: string, value: string) {
        map.set(key, value);
      },
      async deleteItemAsync(key: string) {
        map.delete(key);
      },
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Phase 1 — API Configuration', () => {
  it('uses development default when APP_ENV is development and no override is set', () => {
    const url = resolveApiBaseUrl({ APP_ENV: 'development' });
    assert.equal(url, DEFAULT_DEVELOPMENT_API_BASE_URL);
  });

  it('uses https://iqracentral.com/api/v1 in production by default', () => {
    const url = resolveApiBaseUrl({ APP_ENV: 'production' });
    assert.equal(url, DEFAULT_PRODUCTION_API_BASE_URL);
  });

  it('strips trailing slashes from EXPO_PUBLIC_API_BASE_URL override', () => {
    const url = resolveApiBaseUrl({
      APP_ENV: 'development',
      EXPO_PUBLIC_API_BASE_URL: 'http://192.168.1.25:8000/api/v1///',
    });
    assert.equal(url, 'http://192.168.1.25:8000/api/v1');
  });

  it('rejects non-HTTPS EXPO_PUBLIC_API_BASE_URL in production mode', () => {
    assert.throws(
      () =>
        resolveApiBaseUrl({
          APP_ENV: 'production',
          EXPO_PUBLIC_API_BASE_URL: 'http://iqracentral.com/api/v1',
        }),
      /Production API base URL must use HTTPS/
    );
  });

  it('honors explicit development APP_ENV / EXPO_PUBLIC_APP_ENV even when NODE_ENV is production (expo export:embed)', () => {
    const url = resolveApiBaseUrl({
      EXPO_PUBLIC_APP_ENV: 'development',
      NODE_ENV: 'production',
      EXPO_PUBLIC_API_BASE_URL: 'http://127.0.0.1:8000/api/v1',
    });
    assert.equal(url, 'http://127.0.0.1:8000/api/v1');
  });
});

describe('Phase 1 — Authentication, Role Enforcement & Single-Flight Refresh', () => {
  const originalFetch = globalThis.fetch;
  let memoryStore: ReturnType<typeof createMemorySecureStore>;

  beforeEach(async () => {
    memoryStore = createMemorySecureStore();
    setSecureStoreAdapter(memoryStore.adapter);
    await clearSessionTokens();
    setSessionInvalidatedListener(null);
  });

  afterEach(async () => {
    globalThis.fetch = originalFetch;
    await clearSessionTokens();
    setSecureStoreAdapter(null);
    setSessionInvalidatedListener(null);
  });

  it('authenticates an active EMPLOYEE, stores refresh token in SecureStore, keeps access token in memory, and loads /auth/me/', async () => {
    const calls: { url: string; authHeader: string | null }[] = [];

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);
      calls.push({ url, authHeader: headers.get('Authorization') });

      if (url.endsWith('/auth/login/')) {
        return jsonResponse({
          access: 'access-token-1',
          refresh: 'refresh-token-1',
          user: {
            id: 7,
            username: 'caller01',
            email: 'caller01@iqracentral.com',
            full_name: 'Amina Caller',
            role: 'EMPLOYEE',
            is_active: true,
          },
        });
      }

      if (url.endsWith('/auth/me/')) {
        return jsonResponse({
          id: 7,
          username: 'caller01',
          email: 'caller01@iqracentral.com',
          first_name: 'Amina',
          last_name: 'Caller',
          full_name: 'Amina Caller',
          role: 'EMPLOYEE',
          phone: '+919876543210',
          designation: 'Telecaller',
          is_active: true,
          date_joined: '2026-01-10T09:00:00Z',
          last_login: '2026-02-01T10:00:00Z',
        });
      }

      throw new Error(`Unexpected URL: ${url}`);
    };

    const profile = await loginEmployee('  caller01 ', 'SecretPass#123');
    assert.equal(profile.username, 'caller01');
    assert.equal(profile.role, 'EMPLOYEE');
    assert.equal(profile.designation, 'Telecaller');
    assert.equal(getAccessToken(), 'access-token-1');
    assert.equal(await getStoredRefreshToken(), 'refresh-token-1');
    assert.equal(calls.length, 2);
    assert.equal(calls[1].authHeader, 'Bearer access-token-1');
  });

  it('rejects ADMIN users from signing into the employee caller app and revokes their token', async () => {
    let logoutCalled = false;

    globalThis.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/auth/login/')) {
        return jsonResponse({
          access: 'admin-access',
          refresh: 'admin-refresh',
          user: {
            id: 1,
            username: 'crmadmin',
            email: 'admin@iqracentral.com',
            full_name: 'Admin User',
            role: 'ADMIN',
            is_active: true,
          },
        });
      }
      if (url.endsWith('/auth/logout/')) {
        logoutCalled = true;
        return jsonResponse({ detail: 'Successfully logged out.' });
      }
      throw new Error(`Unexpected URL: ${url}`);
    };

    await assert.rejects(
      () => loginEmployee('crmadmin', 'AdminPass#123'),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 403);
        assert.equal(err.message, NON_EMPLOYEE_ROLE_MESSAGE);
        return true;
      }
    );

    assert.equal(logoutCalled, true);
    assert.equal(getAccessToken(), null);
    assert.equal(await getStoredRefreshToken(), null);
  });

  it('rejects inactive employee accounts', async () => {
    globalThis.fetch = async () =>
      jsonResponse({
        access: 'inactive-access',
        refresh: 'inactive-refresh',
        user: {
          id: 9,
          username: 'former_emp',
          email: '',
          full_name: 'Former Employee',
          role: 'EMPLOYEE',
          is_active: false,
        },
      });

    await assert.rejects(
      () => loginEmployee('former_emp', 'Pass#123456'),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 403);
        assert.equal(err.message, INACTIVE_ACCOUNT_MESSAGE);
        return true;
      }
    );
    assert.equal(getAccessToken(), null);
    assert.equal(await getStoredRefreshToken(), null);
  });

  it('coordinates concurrent 401 responses through a single-flight refresh call and saves rotated refresh token', async () => {
    setAccessToken('expired-access-token');
    await setStoredRefreshToken('initial-refresh-token');

    let refreshCallCount = 0;
    const leadAttemptAuthHeaders: (string | null)[] = [];

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);
      const authHeader = headers.get('Authorization');

      if (url.endsWith('/auth/refresh/')) {
        refreshCallCount += 1;
        const body = JSON.parse(String(init?.body ?? '{}'));
        assert.equal(body.refresh, 'initial-refresh-token');
        await new Promise((resolve) => setTimeout(resolve, 25));
        return jsonResponse({
          access: 'rotated-access-token-2',
          refresh: 'rotated-refresh-token-2',
        });
      }

      if (url.includes('/leads/')) {
        leadAttemptAuthHeaders.push(authHeader);
        if (authHeader === 'Bearer expired-access-token') {
          return jsonResponse({ detail: 'Given token not valid for any token type' }, 401);
        }
        if (authHeader === 'Bearer rotated-access-token-2') {
          return jsonResponse({
            count: 1,
            next: null,
            previous: null,
            results: [
              {
                id: 101,
                name: 'Zaid Khan',
                phone: '+919900112233',
                alternate_phone: '',
                email: 'zaid@example.com',
                course_interest: 'MBA',
                enquiry_details: 'Interested in weekend batch',
                status: 'NEW',
                created_at: '2026-03-01T10:00:00Z',
                updated_at: '2026-03-01T10:00:00Z',
              },
            ],
          });
        }
      }

      throw new Error(`Unexpected request: ${url}`);
    };

    const [resA, resB] = await Promise.all([
      apiRequest('/leads/?page=1'),
      apiRequest('/leads/?page=2'),
    ]);

    assert.equal(refreshCallCount, 1);
    assert.equal(getAccessToken(), 'rotated-access-token-2');
    assert.equal(await getStoredRefreshToken(), 'rotated-refresh-token-2');
    assert.ok(resA);
    assert.ok(resB);
    assert.equal(leadAttemptAuthHeaders.length, 4);
  });

  it('clears session and notifies listener when refresh token is expired or blacklisted', async () => {
    setAccessToken('expired-access');
    await setStoredRefreshToken('blacklisted-refresh');

    let invalidatedReason: string | null = null;
    setSessionInvalidatedListener((reason) => {
      invalidatedReason = reason;
    });

    globalThis.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/auth/refresh/')) {
        return jsonResponse({ detail: 'Token is blacklisted' }, 401);
      }
      return jsonResponse({ detail: 'Token expired' }, 401);
    };

    await assert.rejects(
      () => apiRequest('/leads/'),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 401);
        return true;
      }
    );

    assert.equal(getAccessToken(), null);
    assert.equal(await getStoredRefreshToken(), null);
    assert.equal(invalidatedReason, 'Token is blacklisted');
  });

  it('restores session via refresh + /auth/me/ and clears session on logout or password change', async () => {
    await setStoredRefreshToken('stored-refresh-token');

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/auth/refresh/')) {
        return jsonResponse({
          access: 'restored-access-token',
          refresh: 'restored-refresh-token',
        });
      }
      if (url.endsWith('/auth/me/')) {
        return jsonResponse({
          id: 12,
          username: 'emp12',
          email: 'emp12@iqracentral.com',
          first_name: 'Sara',
          last_name: 'Ali',
          full_name: 'Sara Ali',
          role: 'EMPLOYEE',
          phone: '+919123456780',
          designation: 'Senior Caller',
          is_active: true,
          date_joined: '2026-01-01T00:00:00Z',
          last_login: null,
        });
      }
      if (url.endsWith('/auth/change-password/')) {
        const body = JSON.parse(String(init?.body ?? '{}'));
        assert.equal(body.current_password, 'OldPassword#1');
        assert.equal(body.new_password, 'NewPassword#2');
        return jsonResponse({ detail: 'Password has been updated successfully.' });
      }
      if (url.endsWith('/auth/logout/')) {
        return jsonResponse({ detail: 'Successfully logged out.' });
      }
      throw new Error(`Unexpected URL: ${url}`);
    };

    const restored = await restoreEmployeeSession();
    assert.ok(restored);
    assert.equal(restored.username, 'emp12');
    assert.equal(getAccessToken(), 'restored-access-token');
    assert.equal(await getStoredRefreshToken(), 'restored-refresh-token');

    const msg = await changeEmployeePassword({
      current_password: 'OldPassword#1',
      new_password: 'NewPassword#2',
      confirm_password: 'NewPassword#2',
    });
    assert.equal(msg, 'Password has been updated successfully.');
    assert.equal(getAccessToken(), null);
    assert.equal(await getStoredRefreshToken(), null);

    // Also test logout cleanup
    setAccessToken('temp-access');
    await setStoredRefreshToken('temp-refresh');
    await logoutEmployee();
    assert.equal(getAccessToken(), null);
    assert.equal(await getStoredRefreshToken(), null);
  });
});

describe('Phase 1 — Assigned Leads Service & Sanitization', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    setAccessToken('valid-employee-access');
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setAccessToken(null);
  });

  it('builds employee-permitted query strings and strips admin-only fields via sanitizeEmployeeLead', async () => {
    const query = buildAssignedLeadsQueryString({
      page: 2,
      page_size: 20,
      search: '  nursing ',
      status: 'CONTACTED',
    });
    assert.equal(query, '?page=2&page_size=20&search=nursing&status=CONTACTED');

    const sanitized = sanitizeEmployeeLead({
      id: 42,
      name: 'Fatima Noor',
      phone: '+919811223344',
      alternate_phone: '',
      email: 'fatima@example.com',
      course_interest: 'B.Sc Nursing',
      enquiry_details: 'Wants hostel details',
      status: 'INTERESTED',
      internal_notes: 'SECRET ADMIN NOTE SHOULD NEVER BE INCLUDED',
      assigned_to: { id: 7, username: 'caller01' },
      created_at: '2026-03-10T08:00:00Z',
      updated_at: '2026-03-11T09:30:00Z',
    });

    assert.equal(sanitized.id, 42);
    assert.equal(sanitized.name, 'Fatima Noor');
    assert.equal(sanitized.status, 'INTERESTED');
    assert.equal('internal_notes' in sanitized, false);
    assert.equal('assigned_to' in sanitized, false);

    globalThis.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/leads/?page=1&page_size=20')) {
        return jsonResponse({
          count: 1,
          next: null,
          previous: null,
          results: [sanitized],
        });
      }
      if (url.endsWith('/leads/42/')) {
        return jsonResponse(sanitized);
      }
      throw new Error(`Unexpected URL: ${url}`);
    };

    const listResponse = await fetchAssignedLeads({ page: 1, page_size: 20 });
    assert.equal(listResponse.count, 1);
    assert.equal(listResponse.results[0].name, 'Fatima Noor');

    const detailResponse = await fetchAssignedLeadDetail(42);
    assert.equal(detailResponse.id, 42);
    assert.equal(detailResponse.course_interest, 'B.Sc Nursing');
  });
});

describe('Phase 1 — Form Validation', () => {
  it('validates login and change-password inputs cleanly', () => {
    const loginErrors = validateLoginForm('   ', '');
    assert.ok(loginErrors.username);
    assert.ok(loginErrors.password);
    assert.deepEqual(validateLoginForm('caller01', 'pass'), {});

    const pwErrors = validateChangePasswordForm('samepass12', 'short', 'mismatch');
    assert.ok(pwErrors.newPassword);
    assert.ok(pwErrors.confirmPassword);
    assert.deepEqual(
      validateChangePasswordForm('OldSecret#10', 'NewSecret#20', 'NewSecret#20'),
      {}
    );
  });
});
