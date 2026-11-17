const appJson = require('./app.json');

const ANDROID_PACKAGE_ID = 'com.iqracentral.tcs.caller';
const DEFAULT_PRODUCTION_API_BASE_URL = 'https://iqracentral.com/api/v1';
const DEFAULT_DEVELOPMENT_API_BASE_URL = 'http://10.0.2.2:8000/api/v1';

function isLocalOrPrivateHost(rawHostname) {
  const host = String(rawHostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
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
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (m) {
    const a = Number(m[1]);
    const b = Number(m[2]);
    if (a === 10 || a === 127 || a === 0) return true;
    if (a === 192 && b === 168) return true;
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 169 && b === 254) return true;
  }
  return false;
}

module.exports = ({ config } = {}) => {
  const baseExpoConfig = {
    ...appJson.expo,
    ...(config || {}),
  };
  const explicitEnv = (process.env.EXPO_PUBLIC_APP_ENV || process.env.APP_ENV || '').trim().toLowerCase();
  const appEnv =
    explicitEnv === 'production'
      ? 'production'
      : explicitEnv === 'development'
        ? 'development'
        : process.env.NODE_ENV === 'production'
          ? 'production'
          : 'development';

  const configuredApiUrl = (
    process.env.EXPO_PUBLIC_API_BASE_URL ||
    (appEnv === 'production' ? DEFAULT_PRODUCTION_API_BASE_URL : DEFAULT_DEVELOPMENT_API_BASE_URL)
  )
    .trim()
    .replace(/\/+$/, '');

  if (appEnv === 'production') {
    if (!configuredApiUrl.startsWith('https://')) {
      throw new Error(
        `Invalid production EXPO_PUBLIC_API_BASE_URL "${configuredApiUrl}". Production API base URL must use HTTPS.`
      );
    }
    const parsed = new URL(configuredApiUrl);
    if (isLocalOrPrivateHost(parsed.hostname)) {
      throw new Error(
        `Invalid production EXPO_PUBLIC_API_BASE_URL "${configuredApiUrl}". Production builds cannot target localhost, emulator, or private LAN hosts.`
      );
    }
  }

  return {
    ...baseExpoConfig,
    name: 'IQRA TCS',
    android: {
      ...(appJson.expo.android || {}),
      ...(baseExpoConfig.android || {}),
      package: ANDROID_PACKAGE_ID,
    },
    extra: {
      ...(baseExpoConfig.extra || {}),
      appEnv,
      apiBaseUrl: configuredApiUrl,
      productionApiBaseUrl: DEFAULT_PRODUCTION_API_BASE_URL,
    },
  };
};
