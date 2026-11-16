# IQRA TCS — Android Employee Caller App

Mobile calling workspace for **IQRA TCS** (`iqracentral.com`) employees, built with **Expo (React Native + TypeScript)** and integrated with the IQRA TCS Django + DRF backend (`/api/v1/`).

---

## Scope (Phase 1 & Phase 2)

### Phase 1 — Foundation, Authentication & Assigned Leads
- **App Foundation**: Clean Expo Router + TypeScript project (`com.iqracentral.tcs.caller`) with light/dark theme support and temporary IQRA TCS text branding.
- **Centralized API Configuration**: Configurable via `EXPO_PUBLIC_API_BASE_URL` with separated development (`http://10.0.2.2:8000/api/v1`) and production (`https://iqracentral.com/api/v1`) defaults, HTTPS enforcement in production, 15-second request timeouts, and normalized DRF error handling.
- **Employee-Only JWT Authentication**:
  - `POST /api/v1/auth/login/`
  - `POST /api/v1/auth/refresh/` (single-flight token refresh mutex supporting `ROTATE_REFRESH_TOKENS=True` and `BLACKLIST_AFTER_ROTATION=True`)
  - `POST /api/v1/auth/logout/`
  - `GET /api/v1/auth/me/`
  - `POST /api/v1/auth/change-password/`
  - Refresh tokens are persisted exclusively in Android secure storage (`expo-secure-store`); access tokens are kept in memory only.
  - Enforces `role === "EMPLOYEE"` and `is_active === true` (admin accounts use the web CRM and are blocked from signing into the caller app).
- **Assigned Leads Foundation**:
  - `GET /api/v1/leads/` — Paginated assigned leads list with debounced search (`?search=`), status filters (`?status=`), pull-to-refresh, and infinite pagination.
  - `GET /api/v1/leads/<id>/` — Assigned lead detail screen displaying all employee-permitted fields (`EmployeeLeadSerializer`).

### Phase 2 — Calling Workflow, Outcomes & Follow-Ups
- **Click-to-Call (`tel:` Dialer Handoff)**:
  - Launches the native Android phone dialer with `Linking.openURL("tel:...")` after validating/normalizing the phone number (`src/utils/phone.ts` & `src/services/dialer.ts`).
  - Does not request `CALL_PHONE` or place unattended calls, and never assumes a call was completed merely because the dialer opened.
- **Dynamic Call Outcomes & Idempotent Call Submission**:
  - Loads active call outcomes dynamically from `GET /api/v1/call-outcomes/`.
  - Employee-confirmed duration entry (quick presets + manual minutes/seconds) converted into timezone-aware `started_at` and `ended_at` ISO 8601 strings with explicit timezone offsets.
  - Submits to `POST /api/v1/calls/` with a frozen UUID (`client_event_id` + `Idempotency-Key` header) and frozen `(started_at, ended_at)` window across retries of the same logical submission (`IdempotentOperationCoordinator`).
  - Handles `201 Created`, `200 OK` idempotent replay, `409 Conflict`, `400` validation errors, `401` single-flight token refresh, and post-call follow-up scheduling linked via `call_id`.
- **Follow-Up Scheduling & Lifecycle**:
  - Schedule follow-ups via `POST /api/v1/follow-ups/` with `DateTimePickerModal`, future timestamp validation, explicit timezone offset (`+05:30`), optional `call_id`, optional `notes`, and retry-safe `Idempotency-Key` header.
  - Dedicated **Follow-Ups** tab (`GET /api/v1/follow-ups/`) supporting Overdue (`due_state=overdue`), Today (`due_state=today`), Upcoming (`due_state=upcoming`), Pending (`status=PENDING`), Completed (`status=COMPLETED`), and Cancelled (`status=CANCELLED`) views.
  - Lifecycle actions: Reschedule (`POST /api/v1/follow-ups/<id>/reschedule/` with `Idempotency-Key`), Mark Completed (`POST /api/v1/follow-ups/<id>/complete/` with `{}`), and Cancel (`POST /api/v1/follow-ups/<id>/cancel/` with optional reason).
- **My Call History**:
  - Dedicated **History** tab (`GET /api/v1/calls/`) displaying paginated employee call records, outcome filter chips, duration, timestamps, employee notes, and one-tap navigation to Lead Details.

---

## Local Development Setup

### 1. Install Dependencies

```bash
cd /Users/apple/Projects/iqra-tcs/caller-app
npm install
```

### 2. Configure Environment

Copy `.env.example` to `.env` and set `EXPO_PUBLIC_API_BASE_URL`:

```bash
cp .env.example .env
```

- **Android Emulator** connecting to a local Django server on your Mac (`python manage.py runserver 0.0.0.0:8000`):
  ```env
  EXPO_PUBLIC_API_BASE_URL=http://10.0.2.2:8000/api/v1
  ```
- **Physical Android Device** on the same Wi-Fi/LAN:
  ```env
  EXPO_PUBLIC_API_BASE_URL=http://<YOUR_MAC_LAN_IP>:8000/api/v1
  ```
- **Production**:
  ```env
  APP_ENV=production
  EXPO_PUBLIC_API_BASE_URL=https://iqracentral.com/api/v1
  ```

### 3. Run on Android

```bash
# Start Metro and launch on a connected Android device or running Android Emulator
npx expo start --android
```

### 4. Verification Commands

```bash
# TypeScript typecheck
npm run typecheck

# ESLint check
npm run lint

# Automated unit tests (Phase 1 & Phase 2 suites: 24 tests)
npm test

# Public Expo config verification
npx expo config --type public
```
