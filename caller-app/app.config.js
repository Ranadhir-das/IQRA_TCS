const appJson = require('./app.json');

const DEFAULT_PRODUCTION_API_BASE_URL = 'https://iqracentral.com/api/v1';
const DEFAULT_DEVELOPMENT_API_BASE_URL = 'http://10.0.2.2:8000/api/v1';

module.exports = ({ config }) => {
  const baseExpoConfig = config || appJson.expo;
  const appEnv = process.env.APP_ENV || (process.env.NODE_ENV === 'production' ? 'production' : 'development');
  const configuredApiUrl = (
    process.env.EXPO_PUBLIC_API_BASE_URL ||
    (appEnv === 'production' ? DEFAULT_PRODUCTION_API_BASE_URL : DEFAULT_DEVELOPMENT_API_BASE_URL)
  ).replace(/\/+$/, '');

  return {
    ...baseExpoConfig,
    extra: {
      ...(baseExpoConfig.extra || {}),
      appEnv,
      apiBaseUrl: configuredApiUrl,
      productionApiBaseUrl: DEFAULT_PRODUCTION_API_BASE_URL,
    },
  };
};
