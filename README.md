# IQRA TCS — Tele-Counseling & Admissions CRM Monorepo

Monorepo for **IQRA TCS** (`iqracentral.com`), containing two independent, production-grade applications:

1. **[`consultancy-crm/`](./consultancy-crm/)**: Django + Django REST Framework + PostgreSQL backend, `/api/v1/` mobile API, and `/crm/` Admin Web CRM console.
2. **[`caller-app/`](./caller-app/)**: Android employee calling application built with Expo (React Native + TypeScript).

---

## 1. Product Overview & Application Responsibilities

### `consultancy-crm/` — Admin Web CRM & REST API Backend
- **Administrator Web Interface (`/crm/`)**:
  - Session-authenticated administration console restricted to active `ADMIN` users and active superusers.
  - PostgreSQL-backed atomic login rate limiting (`PostgresAtomicCache` on `iqra_shared_cache`, enforcing `5` failed attempts per `60s` per `(username, IP)` and per `IP`).
  - Live `Asia/Kolkata (IST)` organization & employee-scoped analytics dashboard, lead pipeline distribution, staged CSV/XLSX lead imports with formula-injection protection, atomic single/bulk lead queue assignment, non-superuser calling employee management, immutable call history & outcome catalog configuration, follow-up monitoring & inactive-owner cancellation, and CSV/XLSX report exports.
- **Employee Mobile REST API (`/api/v1/`)**:
  - JWT authentication (`SimpleJWT` with token rotation, server-side refresh token blacklisting on logout/password reset/deactivation, and `IsEmployeeRole` / `IsAdminRole` isolation).
  - Assigned lead retrieval (`GET /api/v1/leads/`, `GET /api/v1/leads/<id>/`) and status updates (`POST /api/v1/leads/<id>/status/`).
  - Dynamic call outcome catalog (`GET /api/v1/call-outcomes/`) and idempotent call logging (`POST /api/v1/calls/`, `GET /api/v1/calls/`).
  - Idempotent follow-up scheduling and lifecycle endpoints (`GET/POST /api/v1/follow-ups/`, `reschedule`, `complete`, `cancel`).

### `caller-app/` — Android Employee Calling App
- **Employee-Only Mobile Workspace (`com.iqracentral.tcs.caller`)**:
  - Authenticates active `EMPLOYEE` accounts against `/api/v1/auth/login/`, stores refresh tokens in Android hardware-backed `expo-secure-store`, keeps access tokens in memory only, and coordinates concurrent `401` refreshes via a single-flight mutex.
  - Displays assigned student leads with search, status filtering, and pull-to-refresh.
  - Initiates calls through the native Android phone dialer (`tel:` intent handoff without requiring `CALL_PHONE` permissions).
  - Logs call outcomes and durations idempotently (`client_event_id` + `Idempotency-Key` UUID header) and schedules/manages callbacks (`reschedule`, `complete`, `cancel`).

---

## 2. Repository Structure

```text
IQRA_TCS/
├── consultancy-crm/          # Django + DRF + PostgreSQL Backend & Admin Web CRM
│   ├── manage.py
│   ├── config/               # Django settings, root URLs, WSGI
│   ├── apps/
│   │   ├── accounts/         # Custom User model, JWT auth, employee management API
│   │   ├── leads/            # Leads, assignment history, staged CSV/XLSX imports
│   │   ├── calls/            # CallOutcome catalog & immutable Call audit records
│   │   ├── followups/        # FollowUp scheduling, lifecycle & FollowUpEvent audit log
│   │   ├── reports/          # Asia/Kolkata dashboard aggregations & CSV/XLSX exporters
│   │   └── webcrm/           # Admin Web CRM views, templates, static CSS/JS & rate limiter
│   ├── requirements.txt
│   ├── .env.example
│   └── README.md
├── caller-app/               # Expo (React Native + TypeScript) Android Caller App
│   ├── src/
│   │   ├── app/              # Expo Router screens ((tabs), login, lead-details, log-call, etc.)
│   │   ├── components/       # Reusable UI components
│   │   ├── constants/        # Environment, API base URL, theme & lead status constants
│   │   ├── context/          # AuthContext & AppThemeContext
│   │   ├── hooks/            # Assigned leads & debounce hooks
│   │   ├── services/         # API client, auth, leads, calls, followUps, dialer, secureStorage
│   │   ├── types/            # Strict TypeScript API contracts
│   │   ├── utils/            # Phone, UUID idempotency, timestamp formatting & validation
│   │   └── __tests__/        # Phase 1 & Phase 2 automated test suites
│   ├── assets/
│   ├── package.json
│   ├── app.json
│   ├── app.config.js
│   ├── .env.example
│   └── README.md
└── README.md
```

---

## 3. CRM Backend (`consultancy-crm/`) — Local Setup & Testing

### Prerequisites
- Python `3.12+`
- PostgreSQL `14+` with a dedicated database (e.g. `iqra_tcs_crm`)

### Setup
```bash
cd consultancy-crm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set SECRET_KEY, DEBUG=True (for local dev), and DATABASE_URL

python manage.py migrate
python manage.py createsuperuser
```

### Run Local Development Server
```bash
cd consultancy-crm
DEBUG=True .venv/bin/python manage.py runserver 0.0.0.0:8000
```
- **Admin Web CRM:** `http://127.0.0.1:8000/crm/`
- **REST API Base:** `http://127.0.0.1:8000/api/v1/`

### Validation & Test Commands
```bash
cd consultancy-crm
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```

---

## 4. Android Caller App (`caller-app/`) — Local Setup, Testing & Build

### Prerequisites
- Node.js `20+` and `npm`
- Android Studio / Android SDK (`ANDROID_HOME`) and JDK `17` for local Android builds

### Setup
```bash
cd caller-app
npm install
cp .env.example .env
```

### API Base URL Configuration (`caller-app/.env`)
- **Android Emulator** (connecting to host Django server on port `8000`):
  ```env
  APP_ENV=development
  EXPO_PUBLIC_API_BASE_URL=http://10.0.2.2:8000/api/v1
  ```
- **Physical Android Device over USB (`adb reverse`)**:
  ```bash
  adb reverse tcp:8000 tcp:8000
  ```
  ```env
  APP_ENV=development
  EXPO_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api/v1
  ```
- **Physical Android Device over Wi-Fi / LAN**:
  ```env
  APP_ENV=development
  EXPO_PUBLIC_API_BASE_URL=http://<YOUR_MAC_LAN_IP>:8000/api/v1
  ```
- **Production (`https://` enforced automatically when `APP_ENV=production` / `EXPO_PUBLIC_APP_ENV=production`)**:
  ```env
  APP_ENV=production
  EXPO_PUBLIC_APP_ENV=production
  EXPO_PUBLIC_API_BASE_URL=https://iqracentral.com/api/v1
  ```

### Validation & Test Commands
```bash
cd caller-app
npm test
npm run typecheck
npm run lint
npx expo config --type public
APP_ENV=production EXPO_PUBLIC_APP_ENV=production EXPO_PUBLIC_API_BASE_URL=https://iqracentral.com/api/v1 npx expo config --type public
```

### Android Build Instructions
```bash
cd caller-app
# Generate native Android project and build/install debug APK on connected device:
npx expo run:android
```

---

## 5. Environment, Production Deployment Readiness & Rollback Reference

> **Deployment Status:** The repository is configured and verified for production readiness. Live production cutover must only be executed after explicit operator authorization and TLS/reverse-proxy verification on `iqracentral.com`.

### 5.1 Production Environment Variables (`consultancy-crm/.env`)

| Variable | Production Value / Example | Purpose |
| :--- | :--- | :--- |
| `SECRET_KEY` | `<50+ char random secret>` | Cryptographic signing key for sessions, CSRF, and JWTs. |
| `DEBUG` | `False` | Disables debug stack traces and development static serving. |
| `ALLOWED_HOSTS` | `iqracentral.com,www.iqracentral.com` | Permitted `Host` headers. |
| `DATABASE_URL` | `postgres://<user>:<pass>@<host>:5432/iqra_tcs_crm` | PostgreSQL `14+` connection string. |
| `DATABASE_CONN_MAX_AGE` | `60` | Persistent connection lifetime (seconds). |
| `DATABASE_CONN_HEALTH_CHECKS` | `True` | Pre-query connection health validation. |
| `SECURE_SSL_REDIRECT` | `True` | Enforces HTTPS redirection (enable only when TLS is active). |
| `SESSION_COOKIE_SECURE` | `True` | Restricts CRM session cookie to HTTPS. |
| `CSRF_COOKIE_SECURE` | `True` | Restricts CSRF cookie to HTTPS. |
| `SESSION_COOKIE_SAMESITE` | `Lax` | SameSite policy for session cookies. |
| `CSRF_COOKIE_SAMESITE` | `Lax` | SameSite policy for CSRF cookies. |
| `SECURE_REFERRER_POLICY` | `strict-origin-when-cross-origin` | Referrer-Policy response header. |
| `CSRF_TRUSTED_ORIGINS` | `https://iqracentral.com,https://www.iqracentral.com` | Trusted HTTPS origins for CSRF verification. |
| `SECURE_HSTS_SECONDS` | `31536000` *(set after HTTPS is verified)* | Enables 1-year HSTS (startup-validated to require HTTPS). |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` *(optional)* | Includes subdomains in HSTS policy. |
| `SECURE_HSTS_PRELOAD` | `False` | Controls HSTS `preload` directive. |
| `CORS_ALLOWED_ORIGINS` | `https://iqracentral.com` | Allowed browser origins. |
| `CORS_ALLOW_ALL_ORIGINS` | `False` | Disables wildcard CORS (enforced `False` in HTTPS production). |
| `CACHE_URL` | *(optional / empty)* | Configures `CACHES["default"]` (DRF throttling) only; never overrides `CACHES["crm_ratelimit"]`. |
| `CRM_SHARED_CACHE_TABLE` | `iqra_shared_cache` | PostgreSQL table used by `PostgresAtomicCache` for `/crm/login/` rate limiting (no Redis required). |
| `CRM_LOGIN_MAX_ATTEMPTS` | `5` | Max failed login attempts per `(username, IP)` per `60s`. |
| `CRM_LOGIN_MAX_IP_ATTEMPTS` | `5` | Max failed login attempts per `IP` across all users per `60s`. |
| `CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `60` | Login rate-limit window in seconds. |
| `CRM_TRUST_X_FORWARDED_FOR` | `True` | Evaluates `X-Forwarded-For` right-to-left only from `CRM_TRUSTED_PROXY_IPS`. |
| `CRM_TRUST_X_FORWARDED_PROTO` | `True` | Honors `X-Forwarded-Proto: https` only when `REMOTE_ADDR` matches `CRM_TRUSTED_PROXY_IPS`. |
| `CRM_TRUSTED_PROXY_IPS` | `127.0.0.1` | Trusted Nginx reverse-proxy IP/CIDR allowlist (wildcard `0.0.0.0/0` and `::/0` are rejected). |
| `DJANGO_LOG_LEVEL` | `INFO` | Log verbosity for `django`, `apps.accounts`, and `apps.webcrm`. |

### 5.2 Nginx, Static Files & Upload Limit Alignment (5 MB File vs. 6 MB Multipart Request)
- **File Size vs. Complete Multipart Request Size**:
  - **Uploaded Spreadsheet File Limit (`5 MB` / `5,242,880` bytes)**: `LEAD_IMPORT_MAX_FILE_SIZE = 5 MB` and `FILE_UPLOAD_MAX_MEMORY_SIZE = 5 MB` cap the raw CSV/XLSX file stream itself (`LeadImportSizeLimitUploadHandler` aborts streaming immediately if file bytes exceed `5 MB`).
  - **Complete HTTP Multipart Request Limit (`6 MB` / `6,291,456` bytes)**: `LEAD_IMPORT_MAX_REQUEST_SIZE = 6 MB`, `DATA_UPLOAD_MAX_MEMORY_SIZE = 6 MB`, and Nginx `client_max_body_size 6m;` cap the entire `multipart/form-data` HTTP request body (`Content-Length`). The extra `1 MB` above the `5 MB` file limit accommodates multipart MIME boundaries, `Content-Disposition` headers, the CSRF token field, and form metadata fields (`assigned_to_id`, `source`, etc.) so a valid `5 MB` spreadsheet upload is never rejected due to multipart framing overhead.
- **Static & Media Serving**: Run `.venv/bin/python manage.py collectstatic --noinput` to populate `consultancy-crm/staticfiles/` and serve `/static/` directly from Nginx (`alias /path/to/IQRA_TCS/consultancy-crm/staticfiles/;`). Django does not serve static files when `DEBUG=False`. Uploaded file permissions are restricted to `0o640` (`0o750` for directories).

### 5.3 Production Deployment Checklist
1. **Backup Database**:
   ```bash
   pg_dump -Fc --no-acl --no-owner -h localhost -U iqra_user -d iqra_tcs_crm -f iqra_tcs_crm_predeploy.dump
   ```
2. **Configure `.env` & Validate Deployment Settings**:
   ```bash
   cd consultancy-crm
   .venv/bin/python manage.py check --deploy
   .venv/bin/python manage.py makemigrations --check --dry-run
   ```
3. **Apply Migrations & Collect Static Assets**:
   ```bash
   .venv/bin/python manage.py migrate --noinput
   .venv/bin/python manage.py collectstatic --noinput
   ```
4. **Provision Initial Admin User (First Deploy Only)**:
   ```bash
   .venv/bin/python manage.py create_admin --username admin --email admin@iqracentral.com
   ```
5. **Configure Scheduled Maintenance (Cron / systemd timer every 15m)**:
   ```bash
   .venv/bin/python manage.py cleanup_staged_lead_imports
   .venv/bin/python manage.py recover_staged_lead_imports
   ```
6. **Start / Reload Gunicorn (`127.0.0.1:8000`) & Nginx** and verify `GET https://iqracentral.com/crm/login/` (`200 OK`) and `/static/webcrm/css/crm.css` (`200 OK`).

### 5.4 Rollback Checklist
1. Stop Gunicorn workers to halt writes.
2. Restore pre-deployment PostgreSQL snapshot if schema/data rollback is required:
   ```bash
   pg_restore --clean --if-exists --no-acl --no-owner -h localhost -U iqra_user -d iqra_tcs_crm iqra_tcs_crm_predeploy.dump
   ```
3. Checkout the previous verified Git commit (`git checkout <previous_sha>`), run `.venv/bin/python manage.py collectstatic --noinput`, and restart Gunicorn.

### 5.5 Security & Infrastructure Isolation
- **No Committed Secrets**: `.env` files, `SECRET_KEY` values, database credentials, Android keystores (`*.jks`, `*.keystore`), and service-account JSON files are strictly excluded via `.gitignore`.
- **Complete Isolation from Vaani Infrastructure**: IQRA TCS (`iqracentral.com`, package `com.iqracentral.tcs.caller`, database `iqra_tcs_crm`) is completely independent of any Vaani repositories, databases, credentials, Firebase projects, signing keys, or deployment environments.
