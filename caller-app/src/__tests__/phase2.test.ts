import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import {
  ApiError,
  clearSessionTokens,
  getAccessToken,
  setAccessToken,
  setSessionInvalidatedListener,
} from '../services/api.ts';
import {
  buildCallHistoryQueryString,
  computeCallTimestampsFromDuration,
  fetchActiveCallOutcomes,
  fetchMyCallHistory,
  submitCallOutcome,
} from '../services/calls.ts';
import { openNativeDialer } from '../services/dialer.ts';
import {
  buildFollowUpsQueryString,
  cacheLeadPhone,
  cancelFollowUp,
  clearLeadPhoneCache,
  completeFollowUp,
  createFollowUp,
  fetchMyFollowUps,
  rescheduleFollowUp,
  validateFutureFollowUpSchedule,
} from '../services/followUps.ts';
import {
  getStoredRefreshToken,
  setSecureStoreAdapter,
  setStoredRefreshToken,
} from '../services/secureStorage.ts';
import {
  IdempotentOperationCoordinator,
  generateIdempotencyKey,
  isValidUuid,
} from '../utils/idempotency.ts';
import {
  InvalidPhoneNumberError,
  buildTelUri,
  validateAndNormalizePhone,
} from '../utils/phone.ts';

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

describe('Phase 2 — Click-to-Call & Phone Number Validation', () => {
  it('validates and normalizes valid phone numbers and builds tel: URIs', () => {
    assert.equal(validateAndNormalizePhone('+91 98765-43210'), '+919876543210');
    assert.equal(validateAndNormalizePhone('(040) 2345 6789'), '04023456789');

    const { telUri, normalizedPhone } = buildTelUri(' +91 (981) 122-3344 ');
    assert.equal(normalizedPhone, '+919811223344');
    assert.equal(telUri, 'tel:+919811223344');
  });

  it('rejects missing, alphabetic, too-short, or malformed phone numbers', () => {
    assert.throws(() => validateAndNormalizePhone(''), InvalidPhoneNumberError);
    assert.throws(() => validateAndNormalizePhone('   '), InvalidPhoneNumberError);
    assert.throws(
      () => validateAndNormalizePhone('98765abc10'),
      InvalidPhoneNumberError
    );
    assert.throws(
      () => validateAndNormalizePhone('12345'),
      InvalidPhoneNumberError
    );
    assert.throws(
      () => validateAndNormalizePhone('12345678901234567'),
      InvalidPhoneNumberError
    );
    assert.throws(
      () => validateAndNormalizePhone('91+9876543210'),
      InvalidPhoneNumberError
    );
  });

  it('opens native dialer with tel: URI and does not auto-submit a call', async () => {
    const openedUrls: string[] = [];
    const result = await openNativeDialer('+91 99887 76655', {
      async openURL(url: string) {
        openedUrls.push(url);
      },
    });

    assert.deepEqual(openedUrls, ['tel:+919988776655']);
    assert.equal(result.telUri, 'tel:+919988776655');
    assert.equal(result.normalizedPhone, '+919988776655');
    assert.ok(result.openedAtIso.endsWith('Z'));
  });
});

describe('Phase 2 — Dynamic Call Outcomes, Call Submission, Idempotency & Retries', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    setAccessToken('employee-access-token');
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setAccessToken(null);
  });

  it('loads active call outcomes dynamically and filters out inactive outcomes', async () => {
    globalThis.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      assert(url.endsWith('/call-outcomes/'));
      return jsonResponse([
        {
          id: 3,
          code: 'NO_ANSWER',
          label: 'No Answer',
          is_active: true,
          display_order: 20,
        },
        {
          id: 2,
          code: 'LEGACY_OUTCOME',
          label: 'Legacy Inactive',
          is_active: false,
          display_order: 1,
        },
        {
          id: 1,
          code: 'INTERESTED',
          label: 'Interested',
          is_active: true,
          display_order: 10,
        },
      ]);
    };

    const outcomes = await fetchActiveCallOutcomes();
    assert.equal(outcomes.length, 2);
    assert.deepEqual(
      outcomes.map((o) => o.code),
      ['INTERESTED', 'NO_ANSWER']
    );
  });

  it('submits call outcome with strict backend fields, preserves UUID across network retry, and handles 201 and 200 retry', async () => {
    const coordinator = new IdempotentOperationCoordinator<{
      started_at: string;
      ended_at: string;
    }>();

    const receivedBodies: Record<string, unknown>[] = [];
    const receivedIdempotencyHeaders: (string | null)[] = [];
    let attempt = 0;

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      assert(url.endsWith('/calls/'));
      attempt += 1;

      const headers = new Headers(init?.headers);
      receivedIdempotencyHeaders.push(headers.get('Idempotency-Key'));
      const parsedBody = JSON.parse(String(init?.body ?? '{}'));
      receivedBodies.push(parsedBody);

      if (attempt === 1) {
        throw new TypeError('Network request failed');
      }

      const status = attempt === 2 ? 201 : 200;
      return jsonResponse(
        {
          id: 501,
          lead_id: 42,
          client_event_id: parsedBody.client_event_id,
          outcome_code_snapshot: 'INTERESTED',
          outcome_label_snapshot: 'Interested',
          started_at: parsedBody.started_at,
          ended_at: parsedBody.ended_at,
          duration_seconds: 95,
          employee_notes: parsedBody.employee_notes,
          created_at: '2026-03-15T10:00:00Z',
        },
        status
      );
    };

    const signature = JSON.stringify({
      leadId: 42,
      outcomeCode: 'INTERESTED',
      durationSeconds: 95,
      notes: 'Wants brochure',
    });

    // Attempt 1: fails due to network error
    await assert.rejects(
      () =>
        coordinator.execute(
          signature,
          (clientEventId, window) =>
            submitCallOutcome({
              lead_id: 42,
              client_event_id: clientEventId,
              outcome_code: 'INTERESTED',
              started_at: window.started_at,
              ended_at: window.ended_at,
              employee_notes: 'Wants brochure',
            }),
          () => computeCallTimestampsFromDuration(95, new Date('2026-03-15T10:00:00Z'))
        ),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.isNetworkError, true);
        return true;
      }
    );

    // Attempt 2: retry of the exact same logical operation reuses the SAME UUID & timestamps!
    const secondResult = await coordinator.execute(
      signature,
      (clientEventId, window) =>
        submitCallOutcome({
          lead_id: 42,
          client_event_id: clientEventId,
          outcome_code: 'INTERESTED',
          started_at: window.started_at,
          ended_at: window.ended_at,
          employee_notes: 'Wants brochure',
        }),
      () => computeCallTimestampsFromDuration(95, new Date('2026-03-15T10:05:00Z'))
    );

    assert.equal(secondResult.created, true);
    assert.equal(secondResult.status, 201);
    assert.equal(receivedBodies.length, 2);
    assert.equal(
      receivedBodies[0].client_event_id,
      receivedBodies[1].client_event_id
    );
    assert.equal(receivedBodies[0].started_at, receivedBodies[1].started_at);
    assert.equal(receivedBodies[0].ended_at, receivedBodies[1].ended_at);
    assert.equal(
      receivedIdempotencyHeaders[0],
      receivedBodies[0].client_event_id
    );
    // Ensure duration_seconds is NOT sent in request body (rejected by CallSubmissionSerializer)
    assert.equal('duration_seconds' in receivedBodies[0], false);

    // Also verify direct exact-retry 200 OK handling on submitCallOutcome
    const sameKey = String(receivedBodies[0].client_event_id);
    const exactRetryResult = await submitCallOutcome({
      lead_id: 42,
      client_event_id: sameKey,
      outcome_code: 'INTERESTED',
      started_at: String(receivedBodies[0].started_at),
      ended_at: String(receivedBodies[0].ended_at),
      employee_notes: 'Wants brochure',
    });
    assert.equal(exactRetryResult.created, false);
    assert.equal(exactRetryResult.status, 200);
  });

  it('prevents accidental concurrent duplicate submissions', async () => {
    const coordinator = new IdempotentOperationCoordinator();

    const firstPromise = coordinator.execute('sig-1', async () => {
      await new Promise((resolve) => setTimeout(resolve, 40));
      return 'done';
    });

    await assert.rejects(
      () =>
        coordinator.execute('sig-1', async () => {
          return 'duplicate';
        }),
      /Submission is already in progress/
    );

    const res = await firstPromise;
    assert.equal(res, 'done');
  });

  it('handles 409 Conflict on call submission cleanly', async () => {
    const eventKey = generateIdempotencyKey();
    assert.equal(isValidUuid(eventKey), true);

    globalThis.fetch = async () =>
      jsonResponse(
        {
          detail:
            'This client event ID was already used with a different call payload.',
        },
        409
      );

    await assert.rejects(
      () =>
        submitCallOutcome({
          lead_id: 42,
          client_event_id: eventKey,
          outcome_code: 'BUSY',
          started_at: '2026-03-15T09:59:00.000Z',
          ended_at: '2026-03-15T09:59:30.000Z',
          employee_notes: '',
        }),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 409);
        assert.equal(err.isConflict, true);
        assert.match(err.message, /already used with a different call payload/);
        return true;
      }
    );
  });
});

describe('Phase 2 — Follow-Up Scheduling, Retry & Lifecycle Actions', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    setAccessToken('employee-access-token');
    clearLeadPhoneCache();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setAccessToken(null);
    clearLeadPhoneCache();
  });

  it('validates future timezone-aware follow-up timestamps', () => {
    const nowMs = Date.parse('2026-03-15T10:00:00Z');
    assert.throws(
      () => validateFutureFollowUpSchedule('2026-03-15T09:59:00Z', nowMs),
      /must be in the future/
    );
    assert.throws(
      () => validateFutureFollowUpSchedule('2026-03-15T12:00:00', nowMs),
      /explicit timezone offset/
    );
    const valid = validateFutureFollowUpSchedule(
      '2026-03-15T12:00:00+05:30',
      Date.parse('2026-03-15T05:00:00Z')
    );
    assert.ok(valid instanceof Date);
  });

  it('creates a follow-up with Idempotency-Key header, preserves key across retry, and links call_id', async () => {
    const coordinator = new IdempotentOperationCoordinator();
    const seenKeys: (string | null)[] = [];
    let callCount = 0;
    const futureIso = new Date(Date.now() + 3600_000).toISOString();

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      assert(url.endsWith('/follow-ups/'));
      callCount += 1;
      const headers = new Headers(init?.headers);
      seenKeys.push(headers.get('Idempotency-Key'));

      if (callCount === 1) {
        throw new TypeError('Temporary network drop');
      }

      const body = JSON.parse(String(init?.body ?? '{}'));
      assert.equal(body.lead_id, 42);
      assert.equal(body.call_id, 501);
      assert.equal(body.scheduled_at, futureIso);

      return jsonResponse(
        {
          id: 88,
          lead_id: 42,
          lead_name: 'Fatima Noor',
          call_id: 501,
          scheduled_at: futureIso,
          status: 'PENDING',
          notes: 'Call back with fee structure',
          completed_at: null,
          cancelled_at: null,
          created_at: '2026-03-15T10:05:00Z',
          updated_at: '2026-03-15T10:05:00Z',
        },
        201
      );
    };

    const sig = JSON.stringify({ leadId: 42, callId: 501, futureIso });

    await assert.rejects(() =>
      coordinator.execute(sig, (key) =>
        createFollowUp(
          {
            lead_id: 42,
            call_id: 501,
            scheduled_at: futureIso,
            notes: 'Call back with fee structure',
          },
          key
        )
      )
    );

    const retryResult = await coordinator.execute(sig, (key) =>
      createFollowUp(
        {
          lead_id: 42,
          call_id: 501,
          scheduled_at: futureIso,
          notes: 'Call back with fee structure',
        },
        key
      )
    );

    assert.equal(retryResult.created, true);
    assert.equal(retryResult.followUp.id, 88);
    assert.equal(seenKeys.length, 2);
    assert.equal(seenKeys[0], seenKeys[1]);
    assert.ok(isValidUuid(seenKeys[0]));
  });

  it('executes follow-up reschedule, complete (empty body), cancel, and enriches lead phone on list', async () => {
    const futureIso = new Date(Date.now() + 7200_000).toISOString();
    const rescheduleKey = generateIdempotencyKey();
    cacheLeadPhone(42, '+919811223344');

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);

      if (url.endsWith('/follow-ups/?page=1&page_size=20&due_state=today')) {
        return jsonResponse({
          count: 1,
          next: null,
          previous: null,
          results: [
            {
              id: 88,
              lead_id: 42,
              lead_name: 'Fatima Noor',
              call_id: 501,
              scheduled_at: futureIso,
              status: 'PENDING',
              notes: 'Initial note',
              completed_at: null,
              cancelled_at: null,
              created_at: '2026-03-15T10:00:00Z',
              updated_at: '2026-03-15T10:00:00Z',
            },
          ],
        });
      }

      if (url.endsWith('/follow-ups/88/reschedule/')) {
        assert.equal(headers.get('Idempotency-Key'), rescheduleKey);
        const body = JSON.parse(String(init?.body ?? '{}'));
        assert.equal(body.scheduled_at, futureIso);
        assert.equal(body.reason, 'Student in class');
        return jsonResponse({
          id: 88,
          lead_id: 42,
          lead_name: 'Fatima Noor',
          call_id: 501,
          scheduled_at: futureIso,
          status: 'PENDING',
          notes: 'Updated note',
          completed_at: null,
          cancelled_at: null,
          created_at: '2026-03-15T10:00:00Z',
          updated_at: '2026-03-15T11:00:00Z',
        });
      }

      if (url.endsWith('/follow-ups/88/complete/')) {
        // Backend FollowUpCompleteSerializer rejects any non-empty body fields!
        assert.equal(String(init?.body), '{}');
        return jsonResponse({
          id: 88,
          lead_id: 42,
          lead_name: 'Fatima Noor',
          call_id: 501,
          scheduled_at: futureIso,
          status: 'COMPLETED',
          notes: 'Updated note',
          completed_at: '2026-03-15T12:00:00Z',
          cancelled_at: null,
          created_at: '2026-03-15T10:00:00Z',
          updated_at: '2026-03-15T12:00:00Z',
        });
      }

      if (url.endsWith('/follow-ups/89/cancel/')) {
        const body = JSON.parse(String(init?.body ?? '{}'));
        assert.equal(body.reason, 'Duplicate entry');
        return jsonResponse({
          id: 89,
          lead_id: 42,
          lead_name: 'Fatima Noor',
          call_id: null,
          scheduled_at: futureIso,
          status: 'CANCELLED',
          notes: '',
          completed_at: null,
          cancelled_at: '2026-03-15T12:05:00Z',
          created_at: '2026-03-15T10:00:00Z',
          updated_at: '2026-03-15T12:05:00Z',
        });
      }

      throw new Error(`Unexpected URL: ${url}`);
    };

    const list = await fetchMyFollowUps({ due_state: 'today', page: 1, page_size: 20 });
    assert.equal(list.count, 1);
    assert.equal(list.results[0].lead_phone, '+919811223344');

    const rescheduled = await rescheduleFollowUp(
      88,
      {
        scheduled_at: futureIso,
        notes: 'Updated note',
        reason: 'Student in class',
      },
      rescheduleKey
    );
    assert.equal(rescheduled.status, 'PENDING');
    assert.equal(rescheduled.notes, 'Updated note');

    const completed = await completeFollowUp(88);
    assert.equal(completed.status, 'COMPLETED');
    assert.ok(completed.completed_at);

    const cancelled = await cancelFollowUp(89, 'Duplicate entry');
    assert.equal(cancelled.status, 'CANCELLED');
    assert.ok(cancelled.cancelled_at);

    assert.equal(
      buildFollowUpsQueryString({ status: 'PENDING', due_state: 'overdue', page: 2 }),
      '?page=2&page_size=20&status=PENDING&due_state=overdue'
    );
  });
});

describe('Phase 2 — Call History Pagination & Session Expiration During Submissions', () => {
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

  it('fetches paginated employee call history', async () => {
    setAccessToken('employee-access-token');

    globalThis.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      assert(url.endsWith('/calls/?page=2&page_size=10&outcome_code=INTERESTED'));
      return jsonResponse({
        count: 15,
        next: null,
        previous: 'http://10.0.2.2:8000/api/v1/calls/?page=1&page_size=10',
        results: [
          {
            id: 700,
            lead_id: 42,
            lead_name: 'Fatima Noor',
            caller_username_snapshot: 'caller01',
            caller_name_snapshot: 'Amina Caller',
            outcome_code_snapshot: 'INTERESTED',
            outcome_label_snapshot: 'Interested',
            started_at: '2026-03-15T09:00:00Z',
            ended_at: '2026-03-15T09:03:00Z',
            duration_seconds: 180,
            employee_notes: 'Asked for scholarship details',
            created_at: '2026-03-15T09:03:05Z',
          },
        ],
      });
    };

    const query = buildCallHistoryQueryString({
      page: 2,
      page_size: 10,
      outcome_code: 'INTERESTED',
    });
    assert.equal(query, '?page=2&page_size=10&outcome_code=INTERESTED');

    const history = await fetchMyCallHistory({
      page: 2,
      page_size: 10,
      outcome_code: 'INTERESTED',
    });
    assert.equal(history.count, 15);
    assert.equal(history.results.length, 1);
    assert.equal(history.results[0].duration_seconds, 180);
    assert.equal(history.results[0].outcome_label_snapshot, 'Interested');
  });

  it('refreshes token on 401 during call submission while preserving Idempotency-Key, and invalidates session if refresh fails', async () => {
    setAccessToken('expired-access-token');
    await setStoredRefreshToken('valid-refresh-token');

    const eventKey = generateIdempotencyKey();
    const seenIdempotencyKeys: (string | null)[] = [];

    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);

      if (url.endsWith('/calls/')) {
        seenIdempotencyKeys.push(headers.get('Idempotency-Key'));
        if (headers.get('Authorization') === 'Bearer expired-access-token') {
          return jsonResponse({ detail: 'Token expired' }, 401);
        }
        if (headers.get('Authorization') === 'Bearer fresh-access-token') {
          return jsonResponse(
            {
              id: 777,
              lead_id: 42,
              client_event_id: eventKey,
              outcome_code_snapshot: 'NO_ANSWER',
              outcome_label_snapshot: 'No Answer',
              started_at: '2026-03-15T10:00:00Z',
              ended_at: '2026-03-15T10:00:00Z',
              duration_seconds: 0,
              employee_notes: '',
              created_at: '2026-03-15T10:00:01Z',
            },
            201
          );
        }
      }

      if (url.endsWith('/auth/refresh/')) {
        return jsonResponse({
          access: 'fresh-access-token',
          refresh: 'rotated-refresh-token',
        });
      }

      throw new Error(`Unexpected URL: ${url}`);
    };

    const result = await submitCallOutcome({
      lead_id: 42,
      client_event_id: eventKey,
      outcome_code: 'NO_ANSWER',
      started_at: '2026-03-15T10:00:00Z',
      ended_at: '2026-03-15T10:00:00Z',
      employee_notes: '',
    });

    assert.equal(result.created, true);
    assert.equal(result.call.id, 777);
    assert.deepEqual(seenIdempotencyKeys, [eventKey, eventKey]);
    assert.equal(getAccessToken(), 'fresh-access-token');
    assert.equal(await getStoredRefreshToken(), 'rotated-refresh-token');

    // Now simulate expired refresh token during follow-up creation
    setAccessToken('expired-access-token-2');
    let sessionInvalidatedReason: string | null = null;
    setSessionInvalidatedListener((reason) => {
      sessionInvalidatedReason = reason;
    });

    globalThis.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/auth/refresh/')) {
        return jsonResponse({ detail: 'Refresh token expired' }, 401);
      }
      return jsonResponse({ detail: 'Access token expired' }, 401);
    };

    const futureIso = new Date(Date.now() + 3600_000).toISOString();
    await assert.rejects(
      () =>
        createFollowUp(
          { lead_id: 42, scheduled_at: futureIso, notes: '' },
          generateIdempotencyKey()
        ),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 401);
        return true;
      }
    );

    assert.equal(getAccessToken(), null);
    assert.equal(await getStoredRefreshToken(), null);
    assert.equal(sessionInvalidatedReason, 'Refresh token expired');
  });
});
