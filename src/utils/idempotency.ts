/**
 * UUID v4 idempotency key generation, validation, and retry-safe operation tracking.
 *
 * Backend requirements verified in `/Users/apple/Projects/iqra-tcs/consultancy-crm`:
 * - `POST /api/v1/calls/` requires `client_event_id` (UUID) in the JSON body; server computes a
 *   canonical SHA-256 fingerprint over `(caller_id, client_event_id, lead_id, outcome_code, started_at, ended_at, employee_notes)`.
 * - `POST /api/v1/follow-ups/` and `POST /api/v1/follow-ups/<id>/reschedule/` require
 *   `Idempotency-Key: <UUID>` in the HTTP header (lowercase canonical UUID string).
 */

const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/**
 * Generates a lowercase canonical RFC 4122 UUID v4 string.
 */
export function generateIdempotencyKey(): string {
  if (
    typeof globalThis.crypto !== 'undefined' &&
    typeof globalThis.crypto.randomUUID === 'function'
  ) {
    return globalThis.crypto.randomUUID().toLowerCase();
  }

  const bytes = new Uint8Array(16);
  if (
    typeof globalThis.crypto !== 'undefined' &&
    typeof globalThis.crypto.getRandomValues === 'function'
  ) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }

  // Per RFC 4122 section 4.4: version 4 and variant 10xx
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

/**
 * Validates that a string is a canonical lowercase UUID.
 */
export function isValidUuid(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const cleaned = value.trim().toLowerCase();
  return UUID_REGEX.test(cleaned);
}

/**
 * Tracks a single logical submission so that:
 * 1. Concurrent duplicate taps while a submission is in-flight are blocked.
 * 2. Retries of the exact same logical input reuse the same UUID key and frozen metadata
 *    (such as `started_at` and `ended_at` for call submissions).
 * 3. Editing the logical input after a failed attempt automatically mints a new UUID key
 *    so the server never raises a 409 fingerprint mismatch for user-edited inputs.
 */
export class IdempotentOperationCoordinator<TMeta = undefined> {
  private currentSignature: string | null = null;
  private currentKey: string | null = null;
  private currentMeta: TMeta | undefined = undefined;
  private inFlight = false;

  public isSubmitting(): boolean {
    return this.inFlight;
  }

  public getCurrentKey(): string | null {
    return this.currentKey;
  }

  /**
   * Returns the existing `(key, meta)` if `signature` matches the pending operation,
   * or generates a fresh UUID and computes `createMeta()` if this is a new or modified payload.
   */
  public prepare(
    signature: string,
    createMeta?: () => TMeta
  ): { key: string; meta: TMeta } {
    if (this.currentSignature !== signature || !this.currentKey) {
      this.currentSignature = signature;
      this.currentKey = generateIdempotencyKey();
      this.currentMeta = createMeta ? createMeta() : (undefined as TMeta);
    }
    return {
      key: this.currentKey,
      meta: this.currentMeta as TMeta,
    };
  }

  /**
   * Forces a fresh UUID on the next attempt (e.g. after resolving a 409 Conflict).
   */
  public reset(): void {
    this.currentSignature = null;
    this.currentKey = null;
    this.currentMeta = undefined;
  }

  /**
   * Executes `task` under a single-flight lock while preserving `(key, meta)` on failure
   * and resetting the key only after the operation succeeds.
   */
  public async execute<TResult>(
    signature: string,
    task: (key: string, meta: TMeta) => Promise<TResult>,
    createMeta?: () => TMeta
  ): Promise<TResult> {
    if (this.inFlight) {
      throw new Error('Submission is already in progress. Please wait.');
    }

    const { key, meta } = this.prepare(signature, createMeta);
    this.inFlight = true;
    try {
      const result = await task(key, meta);
      this.reset();
      return result;
    } finally {
      this.inFlight = false;
    }
  }
}
