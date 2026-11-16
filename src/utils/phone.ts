/**
 * Phone number validation and normalization for click-to-call (`tel:` URI).
 */

export class InvalidPhoneNumberError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InvalidPhoneNumberError';
  }
}

/**
 * Validates and normalizes a lead phone number for the native Android dialer.
 * - Rejects empty/missing values.
 * - Rejects strings containing letters or disallowed URI characters.
 * - Strips visual separators (spaces, hyphens, parentheses, dots).
 * - Preserves an optional leading `+`.
 * - Requires between 7 and 15 digits (ITU-T E.164 & standard Indian 10-digit numbers).
 */
export function validateAndNormalizePhone(rawPhone: unknown): string {
  if (typeof rawPhone !== 'string' || !rawPhone.trim()) {
    throw new InvalidPhoneNumberError(
      'This lead does not have a phone number configured.'
    );
  }

  const trimmed = rawPhone.trim();

  // Reject letters or unexpected characters
  if (/[^0-9+\-\s().]/.test(trimmed)) {
    throw new InvalidPhoneNumberError(
      `Invalid phone number "${trimmed}". Phone numbers may only contain digits and standard formatting characters.`
    );
  }

  // Only allow '+' at the very beginning
  const plusCount = (trimmed.match(/\+/g) || []).length;
  if (plusCount > 1 || (plusCount === 1 && !trimmed.startsWith('+'))) {
    throw new InvalidPhoneNumberError(
      `Invalid phone number "${trimmed}". The "+" prefix is only valid at the start of the number.`
    );
  }

  const hasLeadingPlus = trimmed.startsWith('+');
  const digitsOnly = trimmed.replace(/\D/g, '');

  if (digitsOnly.length < 7 || digitsOnly.length > 15) {
    throw new InvalidPhoneNumberError(
      `Invalid phone number "${trimmed}". Expected 7 to 15 digits.`
    );
  }

  return hasLeadingPlus ? `+${digitsOnly}` : digitsOnly;
}

/**
 * Builds a validated `tel:` URI for opening the Android dialer.
 */
export function buildTelUri(rawPhone: unknown): {
  telUri: string;
  normalizedPhone: string;
} {
  const normalizedPhone = validateAndNormalizePhone(rawPhone);
  return {
    telUri: `tel:${normalizedPhone}`,
    normalizedPhone,
  };
}
