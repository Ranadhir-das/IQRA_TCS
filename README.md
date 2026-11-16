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
- **Production (`https://` enforced automatically when `APP_ENV=production`)**:
  ```env
  APP_ENV=production
  EXPO_PUBLIC_API_BASE_URL=https://iqracentral.com/api/v1
  ```

### Validation & Test Commands
```bash
cd caller-app
npm test
npm run typecheck
npm run lint
npx expo config --type public
```

### Android Build Instructions
```bash
cd caller-app
# Generate native Android project and build/install debug APK on connected device:
npx expo run:android
```

---

## 5. Environment & Security Boundaries

- **No Committed Secrets**: `.env` files, Django `SECRET_KEY` values, PostgreSQL credentials, JWT tokens, Android keystores (`*.jks`, `*.keystore`), and service-account JSON files are strictly excluded via `.gitignore`. Use [`consultancy-crm/.env.example`](./consultancy-crm/.env.example) and [`caller-app/.env.example`](./caller-app/.env.example) as templates.
- **Complete Isolation from Vaani Infrastructure**:
  - IQRA TCS (`iqracentral.com`, Android package `com.iqracentral.tcs.caller`, database `iqra_tcs_crm`) is completely independent of any Vaani repositories, databases, credentials, Firebase projects, signing keys, or deployment environments.
