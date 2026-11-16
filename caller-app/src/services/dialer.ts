import { buildTelUri } from '../utils/phone.ts';

export interface DialerLinker {
  openURL(url: string): Promise<unknown>;
}

export interface DialerLaunchResult {
  telUri: string;
  normalizedPhone: string;
  openedAtIso: string;
}

/**
 * Opens the native Android phone dialer pre-filled with the lead's validated phone number (`tel:`).
 *
 * Security & workflow guarantees:
 * - Uses `tel:` intent handoff only; never requests `CALL_PHONE` or places unattended calls.
 * - Validates and normalizes the phone number before invoking the OS dialer.
 * - Does not log or mark a call as completed merely because the dialer opened.
 */
export async function openNativeDialer(
  rawPhone: unknown,
  customLinker?: DialerLinker
): Promise<DialerLaunchResult> {
  const { telUri, normalizedPhone } = buildTelUri(rawPhone);

  const linker: DialerLinker =
    customLinker ?? (await import('react-native')).Linking;

  try {
    await linker.openURL(telUri);
  } catch {
    throw new Error(
      `Could not open the phone dialer for ${normalizedPhone}. Please check that your device supports phone calls.`
    );
  }

  return {
    telUri,
    normalizedPhone,
    openedAtIso: new Date().toISOString(),
  };
}
