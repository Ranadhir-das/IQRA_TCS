"""
IQRA TCS Calling CRM — Authentication Tests

Covers:
1.  POST /api/v1/auth/login/   — Valid admin login.
2.  POST /api/v1/auth/login/   — Valid employee login.
3.  POST /api/v1/auth/login/   — Wrong password → 400.
4.  POST /api/v1/auth/login/   — Unknown username → 400.
5.  POST /api/v1/auth/login/   — Inactive employee → 400 (distinct message).
6.  POST /api/v1/auth/login/   — Inactive admin → 400.
7.  POST /api/v1/auth/login/   — Missing fields → 400.
8.  POST /api/v1/auth/refresh/ — Valid refresh → new access token.
9.  POST /api/v1/auth/refresh/ — Refresh after user deactivation → 401.
10. POST /api/v1/auth/refresh/ — Invalid / tampered refresh token → 401.
11. POST /api/v1/auth/refresh/ — Blacklisted token (after logout) → 401.
12. POST /api/v1/auth/logout/  — Valid logout → 200; subsequent refresh → 401.
13. POST /api/v1/auth/logout/  — Without auth → 401.
14. POST /api/v1/auth/logout/  — Without refresh body → 400.
15. GET  /api/v1/auth/me/      — Authenticated → returns user profile.
16. GET  /api/v1/auth/me/      — Unauthenticated → 401.
17. GET  /api/v1/auth/me/      — Expired/invalid token → 401.
18. POST /api/v1/auth/login/   — Login rate throttle (5/min enforced).
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User


def _make_admin(**kwargs) -> User:
    defaults = dict(username="admin", email="admin@iqracentral.com",
                    role=User.Role.ADMIN, is_active=True, is_staff=True)
    defaults.update(kwargs)
    u = User(**{k: v for k, v in defaults.items() if k != "password"})
    u.set_password(kwargs.get("password", "Admin@12345"))
    u.save()
    return u


def _make_employee(**kwargs) -> User:
    defaults = dict(username="emp1", email="emp1@iqracentral.com",
                    role=User.Role.EMPLOYEE, is_active=True)
    defaults.update(kwargs)
    u = User(**{k: v for k, v in defaults.items() if k != "password"})
    u.set_password(kwargs.get("password", "Emp@12345"))
    u.save()
    return u


class LoginTests(TestCase):
    LOGIN_URL = "/api/v1/auth/login/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_admin()
        self.employee = _make_employee()

    def tearDown(self):
        cache.clear()

    # ── Successful logins ──────────────────────────────────────────────────

    def test_admin_login_returns_tokens_and_profile(self):
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "admin", "password": "Admin@12345"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertEqual(data["user"]["role"], User.Role.ADMIN)
        self.assertEqual(data["user"]["username"], "admin")
        self.assertTrue(data["user"]["is_active"])

    def test_employee_login_returns_tokens_and_profile(self):
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "emp1", "password": "Emp@12345"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertIn("access", data)
        self.assertEqual(data["user"]["role"], User.Role.EMPLOYEE)

    # ── Invalid credentials ────────────────────────────────────────────────

    def test_wrong_password_returns_400(self):
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "admin", "password": "WrongPassword"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid credentials", str(resp.json()))

    def test_unknown_username_returns_400(self):
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "nobody", "password": "Whatever1"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid credentials", str(resp.json()))

    def test_missing_password_returns_400(self):
        resp = self.client.post(self.LOGIN_URL, {"username": "admin"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_username_returns_400(self):
        resp = self.client.post(self.LOGIN_URL, {"password": "Admin@12345"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Inactive account ───────────────────────────────────────────────────

    def test_inactive_employee_login_returns_400_with_deactivated_message(self):
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "emp1", "password": "Emp@12345"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("deactivated", str(resp.json()).lower())

    def test_inactive_admin_login_returns_400(self):
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "admin", "password": "Admin@12345"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("deactivated", str(resp.json()).lower())

    # ── Case-insensitive username ──────────────────────────────────────────

    def test_username_lookup_is_case_insensitive(self):
        resp = self.client.post(self.LOGIN_URL,
                                {"username": "ADMIN", "password": "Admin@12345"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── Login Rate Throttle ────────────────────────────────────────────────

    def test_login_rate_throttle_enforces_limit(self):
        """After 5 attempts within 1 minute, the 6th attempt is throttled with 429."""
        for _ in range(5):
            self.client.post(self.LOGIN_URL, {"username": "admin", "password": "WrongPassword"}, format="json")

        resp = self.client.post(self.LOGIN_URL, {"username": "admin", "password": "Admin@12345"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class TokenRefreshTests(TestCase):
    REFRESH_URL = "/api/v1/auth/refresh/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.employee = _make_employee()
        self.refresh = RefreshToken.for_user(self.employee)

    def tearDown(self):
        cache.clear()

    def test_valid_refresh_returns_new_access_token(self):
        original_refresh = str(self.refresh)
        resp = self.client.post(self.REFRESH_URL, {"refresh": original_refresh},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertIn("access", data)
        # Rotation: a new refresh token is returned
        self.assertIn("refresh", data)
        self.assertNotEqual(data["refresh"], original_refresh)

        rotated_response = self.client.post(
            self.REFRESH_URL, {"refresh": data["refresh"]}, format="json"
        )
        self.assertEqual(rotated_response.status_code, status.HTTP_200_OK)

    def test_refresh_after_user_deactivation_returns_401(self):
        # Deactivate the user after the refresh token was issued
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        resp = self.client.post(self.REFRESH_URL, {"refresh": str(self.refresh)},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("deactivated", str(resp.json()).lower())

    def test_invalid_refresh_token_returns_401(self):
        resp = self.client.post(self.REFRESH_URL, {"refresh": "not.a.real.token"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_expired_refresh_token_returns_401(self):
        expired_refresh = self.refresh
        expired_refresh.set_exp(lifetime=timedelta(seconds=-1))
        resp = self.client.post(
            self.REFRESH_URL, {"refresh": str(expired_refresh)}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_blacklisted_refresh_token_returns_401(self):
        raw_refresh = str(self.refresh)
        # Use the refresh token once (rotates it, blacklists old one)
        resp1 = self.client.post(self.REFRESH_URL, {"refresh": raw_refresh},
                                 format="json")
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        # Reuse the original (now blacklisted) refresh token → must fail
        resp2 = self.client.post(self.REFRESH_URL, {"refresh": raw_refresh},
                                 format="json")
        self.assertEqual(resp2.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutTests(TestCase):
    LOGOUT_URL = "/api/v1/auth/logout/"
    REFRESH_URL = "/api/v1/auth/refresh/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.employee = _make_employee()
        self.refresh = RefreshToken.for_user(self.employee)
        self.access = str(self.refresh.access_token)

    def tearDown(self):
        cache.clear()

    def test_logout_blacklists_refresh_token(self):
        raw_refresh = str(self.refresh)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        resp = self.client.post(self.LOGOUT_URL, {"refresh": raw_refresh},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("logged out", resp.json()["detail"].lower())

        # Subsequent refresh with the same token must fail
        self.client.credentials()
        resp2 = self.client.post(self.REFRESH_URL, {"refresh": raw_refresh},
                                 format="json")
        self.assertEqual(resp2.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_cannot_revoke_another_users_refresh_token(self):
        other_user = _make_admin(username="other_admin", email="other@iqracentral.com")
        other_refresh = str(RefreshToken.for_user(other_user))

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.post(
            self.LOGOUT_URL, {"refresh": other_refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.credentials()
        refresh_response = self.client.post(
            self.REFRESH_URL, {"refresh": other_refresh}, format="json"
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)

    def test_logout_without_authentication_returns_401(self):
        self.client.credentials()
        resp = self.client.post(self.LOGOUT_URL, {"refresh": str(self.refresh)}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_without_refresh_body_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        resp = self.client.post(self.LOGOUT_URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_with_invalid_token_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        resp = self.client.post(self.LOGOUT_URL, {"refresh": "not.valid.token"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class MeViewTests(TestCase):
    ME_URL = "/api/v1/auth/me/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_admin()
        self.employee = _make_employee()

    def tearDown(self):
        cache.clear()

    def test_me_returns_own_profile_for_admin(self):
        access = str(RefreshToken.for_user(self.admin).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(self.ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["username"], "admin")
        self.assertEqual(data["role"], User.Role.ADMIN)

    def test_me_returns_own_profile_for_employee(self):
        access = str(RefreshToken.for_user(self.employee).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(self.ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["role"], User.Role.EMPLOYEE)

    def test_me_unauthenticated_returns_401(self):
        self.client.credentials()
        resp = self.client.get(self.ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_with_invalid_token_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not.a.real.token")
        resp = self.client.get(self.ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_includes_expected_fields(self):
        access = str(RefreshToken.for_user(self.employee).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(self.ME_URL)
        data = resp.json()
        for field in ("id", "username", "email", "full_name", "role",
                      "phone", "designation", "is_active", "date_joined"):
            self.assertIn(field, data, f"Expected field '{field}' in /me/ response")


class ChangePasswordTests(TestCase):
    CHANGE_PASSWORD_URL = "/api/v1/auth/change-password/"
    LOGIN_URL = "/api/v1/auth/login/"
    REFRESH_URL = "/api/v1/auth/refresh/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_admin()
        self.employee = _make_employee()

    def tearDown(self):
        cache.clear()

    def test_employee_can_change_own_password_and_revokes_outstanding_refresh_tokens(self):
        refresh_1 = RefreshToken.for_user(self.employee)
        refresh_2 = RefreshToken.for_user(self.employee)
        admin_refresh = RefreshToken.for_user(self.admin)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_1.access_token}")
        resp = self.client.post(
            self.CHANGE_PASSWORD_URL,
            {
                "current_password": "Emp@12345",
                "new_password": "NewEmpPassword@2026",
                "confirm_password": "NewEmpPassword@2026",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.json())
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password("NewEmpPassword@2026"))
        self.assertFalse(self.employee.check_password("Emp@12345"))

        # All prior employee refresh tokens must now be blacklisted
        self.client.credentials()
        for token in (refresh_1, refresh_2):
            refresh_resp = self.client.post(
                self.REFRESH_URL,
                {"refresh": str(token)},
                format="json",
            )
            self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)

        # Unrelated admin refresh token remains valid
        admin_refresh_resp = self.client.post(
            self.REFRESH_URL,
            {"refresh": str(admin_refresh)},
            format="json",
        )
        self.assertEqual(admin_refresh_resp.status_code, status.HTTP_200_OK)

        # Login with old password fails; login with new password succeeds
        old_login = self.client.post(
            self.LOGIN_URL,
            {"username": "emp1", "password": "Emp@12345"},
            format="json",
        )
        self.assertEqual(old_login.status_code, status.HTTP_400_BAD_REQUEST)
        new_login = self.client.post(
            self.LOGIN_URL,
            {"username": "emp1", "password": "NewEmpPassword@2026"},
            format="json",
        )
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)

    def test_admin_can_change_own_password(self):
        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        resp = self.client.post(
            self.CHANGE_PASSWORD_URL,
            {
                "current_password": "Admin@12345",
                "new_password": "UpdatedAdminPass@2026",
                "confirm_password": "UpdatedAdminPass@2026",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("UpdatedAdminPass@2026"))

        self.client.credentials()
        refresh_resp = self.client.post(
            self.REFRESH_URL,
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_validation_errors(self):
        refresh = RefreshToken.for_user(self.employee)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        invalid_payloads = (
            # Wrong current password
            {
                "current_password": "WrongPassword@1",
                "new_password": "NewEmpPassword@2026",
                "confirm_password": "NewEmpPassword@2026",
            },
            # Mismatched confirm_password
            {
                "current_password": "Emp@12345",
                "new_password": "NewEmpPassword@2026",
                "confirm_password": "DifferentPassword@2026",
            },
            # Weak password
            {
                "current_password": "Emp@12345",
                "new_password": "123",
                "confirm_password": "123",
            },
            # New password identical to current password
            {
                "current_password": "Emp@12345",
                "new_password": "Emp@12345",
                "confirm_password": "Emp@12345",
            },
            # Unknown field
            {
                "current_password": "Emp@12345",
                "new_password": "NewEmpPassword@2026",
                "confirm_password": "NewEmpPassword@2026",
                "role": "ADMIN",
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                resp = self.client.post(
                    self.CHANGE_PASSWORD_URL,
                    payload,
                    format="json",
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Password and refresh token must remain unchanged after failed attempts
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password("Emp@12345"))
        self.client.credentials()
        refresh_resp = self.client.post(
            self.REFRESH_URL,
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertEqual(refresh_resp.status_code, status.HTTP_200_OK)

    def test_change_password_requires_active_authenticated_user(self):
        self.client.credentials()
        unauth = self.client.post(
            self.CHANGE_PASSWORD_URL,
            {
                "current_password": "Emp@12345",
                "new_password": "NewEmpPassword@2026",
                "confirm_password": "NewEmpPassword@2026",
            },
            format="json",
        )
        self.assertEqual(unauth.status_code, status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(self.employee)
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        inactive_resp = self.client.post(
            self.CHANGE_PASSWORD_URL,
            {
                "current_password": "Emp@12345",
                "new_password": "NewEmpPassword@2026",
                "confirm_password": "NewEmpPassword@2026",
            },
            format="json",
        )
        self.assertIn(
            inactive_resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
