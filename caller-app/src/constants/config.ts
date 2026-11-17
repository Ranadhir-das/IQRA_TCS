/**
 * Centralized application & API configuration for IQRA TCS Caller App.
 *
 * Production domain: https://iqracentral.com
 * Production API base URL: https://iqracentral.com/api/v1
 *
 * Local development override:
 * Set `EXPO_PUBLIC_API_BASE_URL` in `.env` (e.g. `http://10.0.2.2:8000/api/v1` for Android emulator
 * or `http://<LAN_IP>:8000/api/v1` for a physical Android device on the same network).
 */

export const APP_NAME = 'IQRA TCS';
export const APP_SUBTITLE = 'Employee Calling Workspace';
export const ANDROID_PACKAGE_ID = 'com.iqracentral.tcs.caller';
export const PRODUCTION_DOMAIN = 'iqracentral.com';
export const DEFAULT_PRODUCTION_API_BASE_URL = 'https://iqracentral.com/api/v1';
export const DEFAULT_DEVELOPMENT_API_BASE_URL = 'http://10.0.2.2:8000/api/v1';

export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
export const DEFAULT_LEADS_PAGE_SIZE = 20;

export type AppEnvironment = 'development' | 'production' | 'test';

declare const __DEV__: boolean | undefined;

function getDefaultRuntimeEnv(): Record<string, string | undefined> {
  const explicitAppEnv = process.env.EXPO_PUBLIC_APP_ENV || process.env.APP_ENV;
  const inferredAppEnv =
    explicitAppEnv ||
    (typeof __DEV__ === 'boolean' ? (__DEV__ ? 'development' : 'production') : undefined);

  return {
    APP_ENV: inferredAppEnv,
    EXPO_PUBLIC_APP_ENV: process.env.EXPO_PUBLIC_APP_ENV,
    EXPO_PUBLIC_API_BASE_URL: process.env.EXPO_PUBLIC_API_BASE_URL,
    NODE_ENV: process.env.NODE_ENV,
  };
}

export function resolveAppEnvironment(
  env: Record<string, string | undefined> = getDefaultRuntimeEnv()
): AppEnvironment {
  const explicit = (env.EXPO_PUBLIC_APP_ENV ?? env.APP_ENV)?.trim().toLowerCase();
  if (explicit === 'production') return 'production';
  if (explicit === 'development') return 'development';
  if (explicit === 'test' || env.NODE_ENV === 'test') return 'test';
  if (env.NODE_ENV === 'production') return 'production';
  return 'development';
}

export function normalizeBaseUrl(rawUrl: string): string {
  return rawUrl.trim().replace(/\/+$/, '');
}

/**
 * Returns true if the hostname is loopback, Android emulator (`10.0.2.2`),
 * RFC1918 private LAN (`10.x.x.x`, `172.16-31.x.x`, `192.168.x.x`), link-local,
 * or a `.local` / `.lan` / single-label developer hostname.
 */
export function isLocalOrPrivateHost(rawHostname: string): boolean {
  const host = rawHostname.trim().toLowerCase().replace(/^\[|\]$/g, '');
  if (!host) return true;
  if (
    host === 'localhost' ||
    host === '0.0.0.0' ||
    host === '::1' ||
    host.endsWith('.localhost') ||
    host.endsWith('.local') ||
    host.endsWith('.lan') ||
    host.endsWith('.internal') ||
    host.endsWith('.home.arpa') ||
    !host.includes('.')
  ) {
    return true;
  }

  const ipv4Match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (ipv4Match) {
    const a = Number(ipv4Match[1]);
    const b = Number(ipv4Match[2]);
    if (a === 10 || a === 127 || a === 0) return true;
    if (a === 192 && b === 168) return true;
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 169 && b === 254) return true;
  }

  return false;
}

/**
 * Resolves the API base URL from environment overrides or environment-specific defaults.
 * Enforces HTTPS and rejects developer LAN / loopback / emulator hosts in production mode.
 */
export function resolveApiBaseUrl(
  env: Record<string, string | undefined> = getDefaultRuntimeEnv()
): string {
  const appEnv = resolveAppEnvironment(env);
  const envUrl = env.EXPO_PUBLIC_API_BASE_URL?.trim();

  if (envUrl) {
    const normalized = normalizeBaseUrl(envUrl);
    if (appEnv === 'production') {
      if (!normalized.startsWith('https://')) {
        throw new Error(
          `Invalid production EXPO_PUBLIC_API_BASE_URL "${normalized}". Production API base URL must use HTTPS.`
        );
      }
      let parsed: URL;
      try {
        parsed = new URL(normalized);
      } catch {
        throw new Error(
          `Invalid production EXPO_PUBLIC_API_BASE_URL "${normalized}".`
        );
      }
      if (isLocalOrPrivateHost(parsed.hostname)) {
        throw new Error(
          `Invalid production EXPO_PUBLIC_API_BASE_URL "${normalized}". Production builds cannot target localhost, emulator, or private LAN hosts.`
        );
      }
    }
    return normalized;
  }

  return appEnv === 'production'
    ? DEFAULT_PRODUCTION_API_BASE_URL
    : DEFAULT_DEVELOPMENT_API_BASE_URL;
}

export const API_BASE_URL = resolveApiBaseUrl();
export const APP_ENV = resolveAppEnvironment();
