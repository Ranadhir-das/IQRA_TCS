import type { ThemeMode } from '../constants/theme.ts';

const REFRESH_TOKEN_KEY = 'iqra_tcs_caller_refresh_token';
const THEME_MODE_KEY = 'iqra_tcs_caller_theme_mode';

export interface SecureStoreAdapter {
  getItemAsync(key: string): Promise<string | null>;
  setItemAsync(key: string, value: string): Promise<void>;
  deleteItemAsync(key: string): Promise<void>;
}

let customAdapter: SecureStoreAdapter | null = null;

/**
 * Allows unit tests to inject an in-memory adapter without native Android bindings.
 */
export function setSecureStoreAdapter(adapter: SecureStoreAdapter | null): void {
  customAdapter = adapter;
}

async function getAdapter(): Promise<SecureStoreAdapter> {
  if (customAdapter) {
    return customAdapter;
  }
  const SecureStore = await import('expo-secure-store');
  const options: import('expo-secure-store').SecureStoreOptions = {
    keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  };
  return {
    getItemAsync: (key: string) => SecureStore.getItemAsync(key, options),
    setItemAsync: (key: string, value: string) => SecureStore.setItemAsync(key, value, options),
    deleteItemAsync: (key: string) => SecureStore.deleteItemAsync(key, options),
  };
}

export async function getStoredRefreshToken(): Promise<string | null> {
  const store = await getAdapter();
  return store.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function setStoredRefreshToken(refreshToken: string): Promise<void> {
  const trimmed = refreshToken.trim();
  if (!trimmed) {
    throw new Error('Cannot store an empty refresh token.');
  }
  const store = await getAdapter();
  await store.setItemAsync(REFRESH_TOKEN_KEY, trimmed);
}

export async function clearStoredRefreshToken(): Promise<void> {
  try {
    const store = await getAdapter();
    await store.deleteItemAsync(REFRESH_TOKEN_KEY);
  } catch {
    // Ignore cleanup errors on already-removed keys.
  }
}

export async function getStoredThemeMode(): Promise<ThemeMode | null> {
  try {
    const store = await getAdapter();
    const saved = await store.getItemAsync(THEME_MODE_KEY);
    if (saved === 'light' || saved === 'dark') {
      return saved;
    }
    return null;
  } catch {
    return null;
  }
}

export async function setStoredThemeMode(mode: ThemeMode): Promise<void> {
  const store = await getAdapter();
  await store.setItemAsync(THEME_MODE_KEY, mode);
}
