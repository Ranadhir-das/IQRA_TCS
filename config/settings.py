"""
IQRA TCS Calling CRM — Django Settings
Domain: iqracentral.com

Configuration is loaded entirely from environment variables (see .env.example).
No production secrets, credentials, or database connection strings may appear
in this file or any committed file.
"""

from datetime import timedelta
from pathlib import Path

import environ

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# ─── Environment ──────────────────────────────────────────────────────────────
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, []),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
    DJANGO_LOG_LEVEL=(str, "INFO"),
)

# Load .env if present (safe to omit in production where env vars are injected)
environ.Env.read_env(BASE_DIR / ".env")

# ─── Security ─────────────────────────────────────────────────────────────────
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ─── Application Registry ─────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",   # Enables logout / token revocation
    "corsheaders",
    # IQRA apps
    "apps.accounts",
    "apps.leads",
    "apps.calls",
    "apps.followups",
    "apps.reports",
    "apps.webcrm",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.leads.middleware.LeadImportUploadLimitMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ─── Database ─────────────────────────────────────────────────────────────────
# Always use a fresh, independent IQRA database — never Vaani's.
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://apple@localhost:5432/iqra_tcs_crm",
    )
}

# ─── Cache & Shared CRM Rate-Limit Storage ────────────────────────────────────
# DRF API throttling uses CACHES["default"].
# Admin Web CRM login rate limiting (/crm/login/) uses CACHES["crm_ratelimit"],
# which always defaults to the shared PostgreSQL-backed atomic cache
# (PostgresAtomicCache) and never implicitly inherits CACHE_URL.
CACHE_URL = env.str("CACHE_URL", default="").strip()
CRM_SHARED_CACHE_TABLE = env.str(
    "CRM_SHARED_CACHE_TABLE", default="iqra_shared_cache"
).strip()


def build_caches_setting(
    *,
    cache_url: str = "",
    crm_shared_cache_table: str = "iqra_shared_cache",
) -> dict:
    """Build CACHES setting while keeping crm_ratelimit isolated from CACHE_URL."""
    cleaned_cache_url = (cache_url or "").strip()
    if cleaned_cache_url:
        default_cache = env.cache_url_config(cleaned_cache_url)
    else:
        default_cache = {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "iqra-default-locmem",
        }
    return {
        "default": default_cache,
        "crm_ratelimit": {
            "BACKEND": "apps.webcrm.rate_limit.PostgresAtomicCache",
            "LOCATION": crm_shared_cache_table,
            "TIMEOUT": 300,
        },
    }


CACHES = build_caches_setting(
    cache_url=CACHE_URL,
    crm_shared_cache_table=CRM_SHARED_CACHE_TABLE,
)
CRM_LOGIN_RATELIMIT_CACHE_ALIAS = "crm_ratelimit"

# ─── Custom User Model ────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"

# ─── Password Validation ──────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─── Internationalisation & Timezone ──────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ─── Static Files ─────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ─── Admin Web CRM Session Login Settings ─────────────────────────────────────
LOGIN_URL = "/crm/login/"
LOGIN_REDIRECT_URL = "/crm/"
LOGOUT_REDIRECT_URL = "/crm/login/"
CRM_LOGIN_MAX_ATTEMPTS = env.int("CRM_LOGIN_MAX_ATTEMPTS", default=5)
CRM_LOGIN_MAX_IP_ATTEMPTS = env.int(
    "CRM_LOGIN_MAX_IP_ATTEMPTS", default=CRM_LOGIN_MAX_ATTEMPTS
)
CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS = env.int(
    "CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS", default=60
)
CRM_TRUST_X_FORWARDED_FOR = env.bool("CRM_TRUST_X_FORWARDED_FOR", default=False)
CRM_TRUSTED_PROXY_IPS = tuple(
    ip.strip()
    for ip in env.list("CRM_TRUSTED_PROXY_IPS", default=[])
    if ip and ip.strip()
)

from apps.webcrm.rate_limit import (  # noqa: E402
    validate_login_rate_limit_settings,
    validate_table_identifier,
)

validate_table_identifier(CRM_SHARED_CACHE_TABLE)
validate_login_rate_limit_settings(
    max_attempts=CRM_LOGIN_MAX_ATTEMPTS,
    max_ip_attempts=CRM_LOGIN_MAX_IP_ATTEMPTS,
    window_seconds=CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    trusted_proxies=CRM_TRUSTED_PROXY_IPS,
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Lead spreadsheet import guardrails and staging lifecycle.
LEAD_IMPORT_MAX_FILE_SIZE = 5 * 1024 * 1024
LEAD_IMPORT_MAX_REQUEST_SIZE = 6 * 1024 * 1024
LEAD_IMPORT_MAX_ROWS = 5000
LEAD_IMPORT_MAX_CSV_COLUMNS = 100
LEAD_IMPORT_MAX_XLSX_ROW_ELEMENTS = 5001
LEAD_IMPORT_MAX_XLSX_CELL_ELEMENTS = 100000
LEAD_IMPORT_MAX_XLSX_SHARED_STRINGS = 100000
LEAD_IMPORT_MAX_TEXT_LENGTH = 5000
LEAD_IMPORT_STAGING_TTL_SECONDS = 30 * 60
LEAD_IMPORT_PROCESSING_RECOVERY_SECONDS = 60 * 60

# Reporting & export guardrails (Phase 4.1).
REPORT_DEFAULT_DATE_RANGE_DAYS = 30
REPORT_MAX_DATE_RANGE_DAYS = 366
REPORT_EXPORT_MAX_ROWS = 10000

# ─── Django REST Framework ────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
        "login": "5/min",   # Applied by LoginRateThrottle on the login endpoint
    },
    # Return JSON only — no Browsable API in production
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
}

# ─── JWT Token Configuration ──────────────────────────────────────────────────
# Token lifecycle:
#   - Access token:  30-minute lifetime, used in every API request (Bearer header)
#   - Refresh token: 7-day lifetime, used ONLY to obtain new access tokens
#   - Rotation:      Each refresh call issues a new refresh token and blacklists the old one
#   - Revocation:    Logout blacklists the refresh token (server-side) preventing future use
#   - Inactive check: Refresh is blocked for deactivated users (checked in CustomTokenRefreshView)
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,           # New refresh token issued on each refresh call
    "BLACKLIST_AFTER_ROTATION": True,        # Previous refresh token is blacklisted immediately
    "UPDATE_LAST_LOGIN": True,               # Update User.last_login on successful token obtain
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# ─── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = env("CORS_ALLOW_ALL_ORIGINS")

# ─── HTTP Security Headers ────────────────────────────────────────────────────
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# ─── Logging ──────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL"),
            "propagate": False,
        },
        "apps.accounts": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "apps.webcrm": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL"),
            "propagate": False,
        },
    },
}
