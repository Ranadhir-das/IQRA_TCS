"""
Phase 4.2 Automated Tests — IQRA TCS Admin Web CRM (/crm/).

Covers:
1. Session authentication & role authorization (Admin, Superuser, Employee, Inactive, CSRF, POST logout)
2. Dashboard metrics, filters, and HTMX partial updates
3. Lead management (CRUD, manual assignment + audit history, archive toggle, atomic bulk assign, staged CSV/XLSX preview/commit/cancel)
4. Employee management (list, create, profile edit, superuser isolation, activation/deactivation + token revocation, password reset + token revocation)
5. Call history (read-only + immutable snapshots) & Call outcome catalog (immutable code)
6. Follow-up views (list, detail, event history without internal keys, admin inactive-owner cancellation policy)
7. Reports & CSV/XLSX exports (including formula-injection neutralization)
"""

from concurrent.futures import ThreadPoolExecutor
import datetime
import io
import threading
from unittest import skipUnless
from unittest.mock import patch
import uuid
import zipfile
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache, caches
from django.core.cache.backends.base import BaseCache
from django.core.cache.backends.dummy import DummyCache
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, close_old_connections, connection, connections, models
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.followups.models import FollowUp, FollowUpEvent
from apps.leads.models import Lead, LeadAssignmentHistory, StagedLeadImport
from apps.reports.services import build_dashboard_summary
from apps.webcrm.forms import AdminWebLoginForm
from apps.webcrm.rate_limit import (
    LoginRateLimiter,
    PostgresAtomicCache,
    RateLimitConfigurationError,
    RateLimitStorageUnavailableError,
    parse_trusted_proxy_networks,
    resolve_client_ip,
    validate_login_rate_limit_settings,
    validate_table_identifier,
)
from apps.webcrm.views import (
    RATE_LIMIT_ERROR_MESSAGE,
    RATE_LIMIT_STORAGE_ERROR_MESSAGE,
)
from config.settings import build_caches_setting

KOLKATA = ZoneInfo("Asia/Kolkata")


class BaseWebCRMTestCase(TestCase):
    """Shared fixtures using setUpTestData for fast PostgreSQL test execution."""

    @classmethod
    def setUpTestData(cls):
        cls.password = "StrongPass#2026"
        cls.admin_user = User.objects.create_user(
            username="crm_admin",
            password=cls.password,
            role=User.Role.ADMIN,
            first_name="Crm",
            last_name="Admin",
            is_active=True,
        )
        cls.inactive_admin = User.objects.create_user(
            username="inactive_crm_admin",
            password=cls.password,
            role=User.Role.ADMIN,
            is_active=False,
        )
        cls.superuser_emp = User.objects.create_superuser(
            username="root_su_emp",
            email="root@iqracentral.com",
            password=cls.password,
            role=User.Role.EMPLOYEE,
        )
        cls.emp1 = User.objects.create_user(
            username="caller_one",
            password=cls.password,
            role=User.Role.EMPLOYEE,
            first_name="Amina",
            last_name="Khan",
            phone="9876543210",
            designation="Senior Counselor",
            is_active=True,
        )
        cls.emp2 = User.objects.create_user(
            username="caller_two",
            password=cls.password,
            role=User.Role.EMPLOYEE,
            first_name="Zaid",
            last_name="Ali",
            phone="9876543211",
            designation="Junior Counselor",
            is_active=True,
        )
        cls.inactive_emp = User.objects.create_user(
            username="caller_inactive",
            password=cls.password,
            role=User.Role.EMPLOYEE,
            first_name="Former",
            last_name="Staff",
            is_active=False,
        )
        cls.outcome, _ = CallOutcome.objects.update_or_create(
            code="INTERESTED",
            defaults={
                "label": "Interested in Admission",
                "display_order": 1,
                "is_active": True,
            },
        )


class WebCRMAuthenticationTests(BaseWebCRMTestCase):
    """Step 3 & Step 12.1 — Authentication & authorization tests."""

    @staticmethod
    def _clear_rate_limit_caches():
        cache.clear()
        caches[settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS].clear()

    def setUp(self):
        super().setUp()
        self._clear_rate_limit_caches()

    def tearDown(self):
        self._clear_rate_limit_caches()
        super().tearDown()

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("webcrm:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/crm/login/?next=%2Fcrm%2F", response["Location"])

    def test_active_admin_can_login_and_access_dashboard(self):
        response = self.client.post(
            reverse("webcrm:login"),
            {"username": self.admin_user.username, "password": self.password},
        )
        self.assertRedirects(response, reverse("webcrm:dashboard"))
        dash = self.client.get(reverse("webcrm:dashboard"))
        self.assertEqual(dash.status_code, 200)
        self.assertContains(dash, "IQRA TCS CRM")

    def test_active_superuser_with_employee_role_can_login_and_access_crm(self):
        response = self.client.post(
            reverse("webcrm:login"),
            {"username": self.superuser_emp.username, "password": self.password},
        )
        self.assertRedirects(response, reverse("webcrm:dashboard"))
        dash = self.client.get(reverse("webcrm:dashboard"))
        self.assertEqual(dash.status_code, 200)
        self.assertContains(dash, "SUPERUSER")

    def test_ordinary_employee_rejected_at_login_and_forbidden_on_crm_views(self):
        # 1. Login form rejects ordinary EMPLOYEE credentials with generic message
        login_resp = self.client.post(
            reverse("webcrm:login"),
            {"username": self.emp1.username, "password": self.password},
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertContains(
            login_resp,
            AdminWebLoginForm.GENERIC_AUTH_ERROR,
        )

        # 2. Even if an employee session exists, @crm_admin_required returns 403
        self.client.force_login(self.emp1)
        for url_name in (
            "webcrm:dashboard",
            "webcrm:lead-list",
            "webcrm:employee-list",
            "webcrm:call-list",
            "webcrm:followup-list",
            "webcrm:reports-hub",
        ):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 403, f"Expected 403 for {url_name}")

    def test_inactive_admin_rejected_at_login_and_forbidden_if_session_exists(self):
        login_resp = self.client.post(
            reverse("webcrm:login"),
            {"username": self.inactive_admin.username, "password": self.password},
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertContains(
            login_resp,
            AdminWebLoginForm.GENERIC_AUTH_ERROR,
        )

        # Verify is_authorized_crm_admin and @crm_admin_required directly deny an inactive admin user object
        from django.test import RequestFactory
        from apps.webcrm.permissions import is_authorized_crm_admin
        from apps.webcrm.views import dashboard_view

        self.assertFalse(is_authorized_crm_admin(self.inactive_admin))
        rf = RequestFactory()
        req = rf.get(reverse("webcrm:dashboard"))
        req.user = self.inactive_admin
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            dashboard_view(req)

    def test_logout_requires_post_and_terminates_session(self):
        self.client.force_login(self.admin_user)
        get_resp = self.client.get(reverse("webcrm:logout"))
        self.assertEqual(get_resp.status_code, 405)

        post_resp = self.client.post(reverse("webcrm:logout"))
        self.assertRedirects(post_resp, reverse("webcrm:login"))

        after_resp = self.client.get(reverse("webcrm:dashboard"))
        self.assertEqual(after_resp.status_code, 302)

    def test_csrf_enforced_on_state_changing_endpoints(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin_user)
        resp = csrf_client.post(
            reverse("webcrm:lead-create"),
            {"name": "No CSRF", "phone": "9999988888"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_login_rate_limiting_and_timing_and_response_equivalence(self):
        """Phase 4.2.1 Fix 1 — Rate limit, generic error equivalence, timing mitigation, XFF safety."""
        login_url = reverse("webcrm:login")

        # 1. Unknown username executes dummy password hashing work and returns identical generic error
        with patch.object(User, "set_password", wraps=User().set_password) as mock_set_pw:
            unknown_resp = self.client.post(
                login_url,
                {"username": "nonexistent_account_xyz", "password": "WrongPassword#123"},
                REMOTE_ADDR="198.51.100.10",
            )
            self.assertTrue(mock_set_pw.called)
        self.assertEqual(unknown_resp.status_code, 200)
        self.assertContains(unknown_resp, AdminWebLoginForm.GENERIC_AUTH_ERROR)
        self.assertEqual(
            list(unknown_resp.context["form"].non_field_errors()),
            [AdminWebLoginForm.GENERIC_AUTH_ERROR],
        )
        self.assertNotContains(unknown_resp, "198.51.100.10")

        # 2. Existing admin with wrong password, inactive admin, and ordinary employee all return same generic error
        self._clear_rate_limit_caches()
        for uname, pwd in (
            (self.admin_user.username, "WrongPassword#123"),
            (self.inactive_admin.username, self.password),
            (self.emp1.username, self.password),
        ):
            resp = self.client.post(
                login_url,
                {"username": uname, "password": pwd},
                REMOTE_ADDR="198.51.100.11",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, AdminWebLoginForm.GENERIC_AUTH_ERROR)
            self.assertNotContains(resp, "deactivated")
            self.assertNotContains(resp, "Administrator access is required")

        # 3. Repeated failed attempts from same IP/username hit 429 after CRM_LOGIN_MAX_ATTEMPTS (5)
        self._clear_rate_limit_caches()
        for attempt in range(5):
            # Spoofed/malformed X-Forwarded-For headers must be ignored by default (CRM_TRUST_X_FORWARDED_FOR=False)
            r = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": f"WrongPass#{attempt}"},
                REMOTE_ADDR="198.51.100.25",
                HTTP_X_FORWARDED_FOR=f"203.0.113.{attempt}, not-an-ip",
            )
            self.assertEqual(r.status_code, 200, f"Attempt {attempt + 1} should return 200")
            self.assertContains(r, AdminWebLoginForm.GENERIC_AUTH_ERROR)

        # 6th attempt is rate-limited with 429 and safe generic message
        throttled_resp = self.client.post(
            login_url,
            {"username": self.admin_user.username, "password": self.password},
            REMOTE_ADDR="198.51.100.25",
            HTTP_X_FORWARDED_FOR="203.0.113.99",
        )
        self.assertEqual(throttled_resp.status_code, 429)
        self.assertContains(throttled_resp, RATE_LIMIT_ERROR_MESSAGE, status_code=429)
        self.assertNotContains(throttled_resp, "198.51.100.25", status_code=429)
        self.assertNotContains(throttled_resp, self.admin_user.username, status_code=429)

        # 4. When CRM_TRUST_X_FORWARDED_FOR=True, valid leftmost XFF IP is used and malformed XFF falls back safely
        self._clear_rate_limit_caches()
        with override_settings(CRM_TRUST_X_FORWARDED_FOR=True):
            malformed_xff_resp = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="198.51.100.30",
                HTTP_X_FORWARDED_FOR="garbage_ip_value",
            )
            self.assertEqual(malformed_xff_resp.status_code, 200)

        # 5. Legitimate admin login succeeds once rate-limit window expires/clears
        self._clear_rate_limit_caches()
        ok_resp = self.client.post(
            login_url,
            {"username": self.admin_user.username, "password": self.password},
            REMOTE_ADDR="198.51.100.25",
        )
        self.assertRedirects(ok_resp, reverse("webcrm:dashboard"))

    def test_shared_cache_configuration_and_atomic_increment_behavior(self):
        """Phase 4.2.1.1 — CRM rate-limit cache is shared PostgresAtomicCache with single-statement atomic increment."""
        alias = settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS
        crm_cache_cfg = settings.CACHES[alias]
        self.assertEqual(
            crm_cache_cfg["BACKEND"],
            "apps.webcrm.rate_limit.PostgresAtomicCache",
        )
        crm_cache = caches[alias]
        self.assertIsInstance(crm_cache, PostgresAtomicCache)
        self.assertTrue(getattr(crm_cache, "is_shared_backend", False))

        # 1. atomic_increment initializes at 1 and increments atomically while preserving expiry
        key = "webcrm:test:atomic_counter"
        self.assertEqual(crm_cache.atomic_increment(key, delta=1, timeout=60), 1)
        self.assertEqual(crm_cache.atomic_increment(key, delta=1, timeout=60), 2)
        self.assertEqual(crm_cache.incr(key, delta=3), 5)
        self.assertEqual(crm_cache.get(key), 5)

        # 2. add() is atomic and does not overwrite an active key
        self.assertFalse(crm_cache.add(key, 99, timeout=60))
        self.assertEqual(crm_cache.get(key), 5)

        # 3. Expired key is reset to 1 by atomic_increment and rejected by incr()
        expired_key = "webcrm:test:expired_counter"
        crm_cache.set(expired_key, 4, timeout=-10)
        with self.assertRaises(ValueError):
            crm_cache.incr(expired_key, delta=1)
        self.assertEqual(crm_cache.atomic_increment(expired_key, delta=1, timeout=60), 1)

    def test_counter_enforcement_across_independently_constructed_limiter_instances(self):
        """Phase 4.2.1.1 & 4.2.1.3 — Independently constructed cache/limiter instances share state via PostgreSQL."""
        worker_cache_1 = PostgresAtomicCache(
            "iqra_shared_cache", {"TIMEOUT": 300, "OPTIONS": {}}
        )
        worker_cache_2 = PostgresAtomicCache(
            "iqra_shared_cache", {"TIMEOUT": 300, "OPTIONS": {}}
        )
        limiter_1 = LoginRateLimiter(
            cache_backend=worker_cache_1, max_attempts=5, max_ip_attempts=10
        )
        limiter_2 = LoginRateLimiter(
            cache_backend=worker_cache_2, max_attempts=5, max_ip_attempts=10
        )

        client_ip = "198.51.100.77"
        username = "crm_admin"

        # 3 failed attempts on worker 1 + 2 failed attempts on worker 2 = 5 total
        for _ in range(3):
            limiter_1.record_failed_attempt(client_ip, username)
        self.assertFalse(limiter_1.is_rate_limited(client_ip, username))
        self.assertFalse(limiter_2.is_rate_limited(client_ip, username))

        for _ in range(2):
            limiter_2.record_failed_attempt(client_ip, username)
        self.assertTrue(limiter_1.is_rate_limited(client_ip, username))
        self.assertTrue(limiter_2.is_rate_limited(client_ip, username))

        # Clearing on worker 2 clears only (IP, username) state while preserving IP-wide counter (5)
        limiter_2.clear_attempts(client_ip, username)
        self.assertEqual(limiter_1.get_counts(client_ip, username), (0, 5))
        self.assertEqual(limiter_2.get_counts(client_ip, username), (0, 5))
        self.assertFalse(limiter_1.is_rate_limited(client_ip, username))
        self.assertFalse(limiter_2.is_rate_limited(client_ip, username))

        # IP-wide limit across different usernames shared across worker instances (5 more reaches 10)
        for idx in range(3):
            limiter_1.record_failed_attempt(client_ip, f"user_{idx}")
        for idx in range(3, 5):
            limiter_2.record_failed_attempt(client_ip, f"user_{idx}")
        self.assertTrue(limiter_1.is_rate_limited(client_ip, "brand_new_username"))
        self.assertTrue(limiter_2.is_rate_limited(client_ip, "brand_new_username"))

    def test_h1_ip_wide_counter_preserved_after_clear_attempts_and_successful_login(self):
        """H-1 — Successful login or clear_attempts() never erases the IP-wide failure counter."""
        login_url = reverse("webcrm:login")
        client_ip = "198.51.100.61"
        limiter = LoginRateLimiter()

        # 1. Failures accumulate against multiple distinct usernames from the same IP (2 + 2 = 4)
        for idx in range(2):
            r1 = self.client.post(
                login_url,
                {"username": f"probe_alpha_{idx}", "password": "WrongPassword#1"},
                REMOTE_ADDR=client_ip,
            )
            self.assertEqual(r1.status_code, 200)
            r2 = self.client.post(
                login_url,
                {"username": f"probe_beta_{idx}", "password": "WrongPassword#2"},
                REMOTE_ADDR=client_ip,
            )
            self.assertEqual(r2.status_code, 200)

        self.assertEqual(limiter.get_counts(client_ip, self.admin_user.username), (0, 4))

        # 2. A valid login from the same IP succeeds and reconciles only its own +1 reservation
        ok_resp = self.client.post(
            login_url,
            {"username": self.admin_user.username, "password": self.password},
            REMOTE_ADDR=client_ip,
        )
        self.assertRedirects(ok_resp, reverse("webcrm:dashboard"))
        self.client.post(reverse("webcrm:logout"))

        # Even if clear_attempts() is called explicitly, ip_key remains 4
        limiter.clear_attempts(client_ip, self.admin_user.username)
        self.assertEqual(limiter.get_counts(client_ip, self.admin_user.username), (0, 4))

        # 3. One more failed attempt against another username hits CRM_LOGIN_MAX_IP_ATTEMPTS (5),
        #    and the subsequent attempt from this IP is throttled with 429
        fifth_fail = self.client.post(
            login_url,
            {"username": "probe_gamma", "password": "WrongPassword#3"},
            REMOTE_ADDR=client_ip,
        )
        self.assertEqual(fifth_fail.status_code, 200)
        self.assertEqual(limiter.get_counts(client_ip, "probe_gamma"), (1, 5))

        throttled_resp = self.client.post(
            login_url,
            {"username": "probe_delta", "password": "WrongPassword#4"},
            REMOTE_ADDR=client_ip,
        )
        self.assertEqual(throttled_resp.status_code, 429)
        self.assertContains(throttled_resp, RATE_LIMIT_ERROR_MESSAGE, status_code=429)

    def test_h2_strict_backend_selection_and_cache_url_isolation(self):
        """H-2 — CACHE_URL never alters crm_ratelimit, and unverified/Redis/non-atomic backends fail closed."""
        # 1. Setting CACHE_URL changes only CACHES["default"], never CACHES["crm_ratelimit"]
        caches_with_redis_url = build_caches_setting(
            cache_url="redis://127.0.0.1:6379/1",
            crm_shared_cache_table="iqra_shared_cache",
        )
        self.assertIn("redis", caches_with_redis_url["default"]["BACKEND"].lower())
        self.assertEqual(
            caches_with_redis_url["crm_ratelimit"]["BACKEND"],
            "apps.webcrm.rate_limit.PostgresAtomicCache",
        )
        self.assertEqual(
            caches_with_redis_url["crm_ratelimit"]["LOCATION"],
            "iqra_shared_cache",
        )

        # 2. Custom backend with incr() but without is_shared_backend=True + atomic_increment is rejected
        class CustomIncrOnlyCache(BaseCache):
            def __init__(self):
                super().__init__({})

            def incr(self, key, delta=1, version=None):
                return 1

        class UnmarkedAtomicCache(BaseCache):
            def __init__(self):
                super().__init__({})

            def atomic_increment(self, key, delta=1, timeout=None, version=None):
                return 1

        class SharedWithoutAtomicMethodCache(BaseCache):
            is_shared_backend = True

            def __init__(self):
                super().__init__({})

            def incr(self, key, delta=1, version=None):
                return 1

        redis_backend = RedisCache("redis://127.0.0.1:6379/1", {})

        for disallowed_backend in (
            LocMemCache("local", {}),
            DummyCache("dummy", {}),
            redis_backend,
            CustomIncrOnlyCache(),
            UnmarkedAtomicCache(),
            SharedWithoutAtomicMethodCache(),
        ):
            limiter = LoginRateLimiter(cache_backend=disallowed_backend)
            with self.assertRaises(RateLimitStorageUnavailableError):
                limiter.admit_attempt("198.51.100.210", self.admin_user.username)

    def test_m2_bounded_expired_row_cleanup_preserves_active_rows(self):
        """M-2 — Bounded pruning deletes at most 128 expired rows per call and never deletes unexpired counters."""
        crm_cache: PostgresAtomicCache = caches[
            settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS
        ]
        crm_cache.clear()

        # Insert 200 expired rows and 15 active rows
        for idx in range(200):
            crm_cache.set(f"webcrm:test:expired:{idx}", idx, timeout=-30)
        for idx in range(15):
            crm_cache.set(f"webcrm:test:active:{idx}", idx + 100, timeout=300)

        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) FROM "iqra_shared_cache" WHERE "expires" <= STATEMENT_TIMESTAMP()'
            )
            self.assertEqual(cursor.fetchone()[0], 200)
            cursor.execute(
                'SELECT COUNT(*) FROM "iqra_shared_cache" WHERE "expires" > STATEMENT_TIMESTAMP()'
            )
            self.assertEqual(cursor.fetchone()[0], 15)

        # First atomic_increment opportunistically prunes exactly 128 expired rows
        new_val = crm_cache.atomic_increment(
            "webcrm:test:trigger_prune", delta=1, timeout=300
        )
        self.assertEqual(new_val, 1)

        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) FROM "iqra_shared_cache" WHERE "expires" <= STATEMENT_TIMESTAMP()'
            )
            self.assertEqual(cursor.fetchone()[0], 72)
            cursor.execute(
                'SELECT COUNT(*) FROM "iqra_shared_cache" WHERE "expires" > STATEMENT_TIMESTAMP()'
            )
            self.assertEqual(cursor.fetchone()[0], 16)

        # Second prune removes the remaining 72 expired rows while all 16 active rows survive
        deleted_second = crm_cache.prune_expired(batch_size=128)
        self.assertEqual(deleted_second, 72)

        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) FROM "iqra_shared_cache" WHERE "expires" <= STATEMENT_TIMESTAMP()'
            )
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute(
                'SELECT COUNT(*) FROM "iqra_shared_cache" WHERE "expires" > STATEMENT_TIMESTAMP()'
            )
            self.assertEqual(cursor.fetchone()[0], 16)

        for idx in range(15):
            self.assertEqual(crm_cache.get(f"webcrm:test:active:{idx}"), idx + 100)

    def test_m3_rate_limit_configuration_validation_rejects_zero_and_negative_values(self):
        """M-3 — Zero, negative, or non-integer rate-limit settings raise ImproperlyConfigured and fail closed."""
        for bad_kwargs in (
            {"max_attempts": 0},
            {"max_attempts": -5},
            {"max_attempts": False},
            {"max_ip_attempts": 0},
            {"max_ip_attempts": -1},
            {"window_seconds": 0},
            {"window_seconds": -60},
        ):
            with self.assertRaises(ImproperlyConfigured):
                validate_login_rate_limit_settings(**bad_kwargs)
            with self.assertRaises(RateLimitConfigurationError):
                LoginRateLimiter(**bad_kwargs)

        login_url = reverse("webcrm:login")
        for setting_name, invalid_val in (
            ("CRM_LOGIN_MAX_ATTEMPTS", 0),
            ("CRM_LOGIN_MAX_ATTEMPTS", -1),
            ("CRM_LOGIN_MAX_IP_ATTEMPTS", 0),
            ("CRM_LOGIN_MAX_IP_ATTEMPTS", -3),
            ("CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 0),
            ("CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS", -10),
        ):
            with override_settings(**{setting_name: invalid_val}):
                resp = self.client.post(
                    login_url,
                    {"username": self.admin_user.username, "password": self.password},
                    REMOTE_ADDR="198.51.100.222",
                )
                self.assertEqual(resp.status_code, 503)
                self.assertContains(
                    resp, RATE_LIMIT_STORAGE_ERROR_MESSAGE, status_code=503
                )
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_l1_table_identifier_validation(self):
        """L-1 — PostgresAtomicCache validates table identifiers against a strict allowlist before SQL execution."""
        for valid_name in ("iqra_shared_cache", "_cache_table_1", "CRM_Cache_01"):
            self.assertEqual(validate_table_identifier(valid_name), valid_name)
            cache_inst = PostgresAtomicCache(valid_name, {"TIMEOUT": 60})
            self.assertEqual(cache_inst._table, valid_name)

        for invalid_name in (
            "",
            "1starts_with_digit",
            "iqra-shared-cache",
            "public.iqra_shared_cache",
            'iqra_shared_cache"; DROP TABLE users; --',
            "iqra shared cache",
            "a" * 47,
            123,  # type: ignore[arg-type]
        ):
            with self.assertRaises(ImproperlyConfigured):
                validate_table_identifier(invalid_name)  # type: ignore[arg-type]
            with self.assertRaises(RateLimitConfigurationError):
                PostgresAtomicCache(invalid_name, {"TIMEOUT": 60})  # type: ignore[arg-type]

    def test_l3_database_clock_expiry_boundary_and_window_bound_reconciliation(self):
        """L-3 — Expiry boundaries and slot releases use PostgreSQL STATEMENT_TIMESTAMP() consistently."""
        crm_cache: PostgresAtomicCache = caches[
            settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS
        ]
        crm_cache.clear()

        # timeout=0 is immediately at the boundary (expires <= STATEMENT_TIMESTAMP()) and treated as expired
        boundary_key = "webcrm:test:boundary_zero"
        crm_cache.set(boundary_key, 10, timeout=0)
        self.assertIsNone(crm_cache.get(boundary_key))
        self.assertFalse(crm_cache.has_key(boundary_key))
        self.assertEqual(crm_cache.atomic_increment(boundary_key, delta=1, timeout=60), 1)

        # Window-bound slot release does not decrement a newer window if the original window expired
        stale_window_expires = datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
        remaining = crm_cache.atomic_release_slot(
            boundary_key, expected_expires=stale_window_expires, delta=1
        )
        self.assertEqual(remaining, 1)
        self.assertEqual(crm_cache.get(boundary_key), 1)

        # Matching window expiry decrements cleanly down to a floor of 0
        current_expires = crm_cache.get_last_increment_expires(boundary_key)
        self.assertIsNotNone(current_expires)
        self.assertEqual(
            crm_cache.atomic_release_slot(
                boundary_key, expected_expires=current_expires, delta=1
            ),
            0,
        )
        self.assertEqual(
            crm_cache.atomic_release_slot(
                boundary_key, expected_expires=current_expires, delta=1
            ),
            0,
        )

    def test_storage_backend_failure_fails_closed_without_leaking_sensitive_data(self):
        """Phase 4.2.1.1 & 4.2.1.3 — Storage errors or unsupported backends fail closed (503) and log safely."""
        login_url = reverse("webcrm:login")
        secret_attempt_pw = "SuperSecretAttempt#98765"
        sensitive_ip = "198.51.100.200"

        # 1. Operational error in shared storage during pre-authentication admission fails closed with 503
        with (
            patch.object(
                PostgresAtomicCache,
                "atomic_increment",
                side_effect=DatabaseError("simulated postgres cache outage"),
            ),
            self.assertLogs("apps.webcrm", level="ERROR") as captured_logs,
        ):
            resp = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": self.password},
                REMOTE_ADDR=sensitive_ip,
            )

        self.assertEqual(resp.status_code, 503)
        self.assertContains(resp, RATE_LIMIT_STORAGE_ERROR_MESSAGE, status_code=503)
        self.assertNotIn("_auth_user_id", self.client.session)
        joined_logs = "\n".join(captured_logs.output)
        self.assertIn("DatabaseError", joined_logs)
        self.assertNotIn(self.password, joined_logs)
        self.assertNotIn(self.admin_user.username, joined_logs)
        self.assertNotIn(sensitive_ip, joined_logs)

        # 2. Operational error during post-authentication reconciliation also fails closed with 503
        with (
            patch.object(
                PostgresAtomicCache,
                "atomic_release_slot",
                side_effect=DatabaseError("simulated reconciliation failure"),
            ),
            self.assertLogs("apps.webcrm", level="ERROR") as release_logs,
        ):
            resp_release = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": self.password},
                REMOTE_ADDR=sensitive_ip,
            )

        self.assertEqual(resp_release.status_code, 503)
        self.assertContains(
            resp_release, RATE_LIMIT_STORAGE_ERROR_MESSAGE, status_code=503
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        joined_release_logs = "\n".join(release_logs.output)
        self.assertNotIn(self.password, joined_release_logs)
        self.assertNotIn(self.admin_user.username, joined_release_logs)
        self.assertNotIn(sensitive_ip, joined_release_logs)

        # 3. Operational error during get_many in is_rate_limited raises RateLimitStorageUnavailableError
        with patch.object(
            PostgresAtomicCache,
            "get_many",
            side_effect=DatabaseError("simulated read failure"),
        ):
            with self.assertRaises(RateLimitStorageUnavailableError):
                LoginRateLimiter().is_rate_limited(
                    sensitive_ip, self.admin_user.username
                )

        # 4. Process-local LocMemCache and DummyCache are rejected (never silently used)
        for bad_backend in (LocMemCache("local", {}), DummyCache("dummy", {})):
            limiter = LoginRateLimiter(cache_backend=bad_backend)
            with self.assertRaises(RateLimitStorageUnavailableError):
                limiter.is_rate_limited(sensitive_ip, self.admin_user.username)

        with override_settings(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "disallowed-local",
                },
                "crm_ratelimit": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "disallowed-crm-local",
                },
            }
        ):
            locmem_resp = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": secret_attempt_pw},
                REMOTE_ADDR=sensitive_ip,
            )
            self.assertEqual(locmem_resp.status_code, 503)
            self.assertContains(
                locmem_resp, RATE_LIMIT_STORAGE_ERROR_MESSAGE, status_code=503
            )

    def test_finding1_rejected_admissions_do_not_retain_increments_or_cause_collateral_lockout(self):
        """
        Phase 4.2.1.4 Finding 1 — Rejected admissions roll back both counter updates:
        1. At threshold 5, requests 1-5 are admitted and request 6 is rejected before PBKDF2.
        2. After request 6 (and further rejected requests), both counters remain at 5, not 6+.
        3. A rejected request against a saturated IP-wide counter does not create or inflate
           another username's counter.
        4. A saturated username/IP counter does not inflate the IP-wide counter through
           repeated rejected requests when max_ip_attempts > max_attempts.
        """
        login_url = reverse("webcrm:login")
        client_ip = "198.51.100.81"
        limiter = LoginRateLimiter()

        # 1 & 2. Requests 1-5 are admitted; request 6+ are rejected and counters stay at (5, 5)
        for attempt in range(1, 6):
            res = limiter.admit_attempt(client_ip, self.admin_user.username)
            self.assertTrue(res.admitted)
            self.assertEqual((res.user_ip_count, res.ip_count), (attempt, attempt))
        self.assertEqual(limiter.get_counts(client_ip, self.admin_user.username), (5, 5))

        for _ in range(3):
            rejected = limiter.admit_attempt(client_ip, self.admin_user.username)
            self.assertFalse(rejected.admitted)
            self.assertIsNone(rejected.user_ip_expires)
            self.assertIsNone(rejected.ip_expires)
            self.assertEqual((rejected.user_ip_count, rejected.ip_count), (5, 5))
            self.assertEqual(
                limiter.get_counts(client_ip, self.admin_user.username),
                (5, 5),
            )

        # 3. Saturated IP-wide counter (5) rejects a different username ("root_su_emp")
        #    without creating or incrementing root_su_emp's per-user counter (stays 0)
        for _ in range(5):
            other_rejected = limiter.admit_attempt(
                client_ip, self.superuser_emp.username
            )
            self.assertFalse(other_rejected.admitted)
            self.assertEqual(
                limiter.get_counts(client_ip, self.superuser_emp.username),
                (0, 5),
            )

        # 4. Saturated username/IP counter does not inflate IP-wide counter when max_ip_attempts=15
        self._clear_rate_limit_caches()
        wide_ip_limiter = LoginRateLimiter(max_attempts=5, max_ip_attempts=15)
        shared_ip = "198.51.100.82"
        for _ in range(5):
            r = wide_ip_limiter.admit_attempt(shared_ip, self.admin_user.username)
            self.assertTrue(r.admitted)
        self.assertEqual(
            wide_ip_limiter.get_counts(shared_ip, self.admin_user.username),
            (5, 5),
        )
        # 10 rejected attempts for locked-out admin_user must NOT inflate ip_count above 5
        for _ in range(10):
            r_denied = wide_ip_limiter.admit_attempt(
                shared_ip, self.admin_user.username
            )
            self.assertFalse(r_denied.admitted)
        self.assertEqual(
            wide_ip_limiter.get_counts(shared_ip, self.admin_user.username),
            (5, 5),
        )
        # Another admin on the same IP can still be admitted and sign in
        with override_settings(CRM_LOGIN_MAX_ATTEMPTS=5, CRM_LOGIN_MAX_IP_ATTEMPTS=15):
            ok_other = self.client.post(
                login_url,
                {
                    "username": self.superuser_emp.username,
                    "password": self.password,
                },
                REMOTE_ADDR=shared_ip,
            )
            self.assertRedirects(ok_other, reverse("webcrm:dashboard"))
            self.client.post(reverse("webcrm:logout"))
        self.assertEqual(
            wide_ip_limiter.get_counts(shared_ip, self.superuser_emp.username),
            (0, 5),
        )

        # 5. Five admitted failed requests followed by a valid request reject before PBKDF2
        self._clear_rate_limit_caches()
        exhausted_ip = "198.51.100.83"
        for idx in range(5):
            fail_resp = self.client.post(
                login_url,
                {
                    "username": self.admin_user.username,
                    "password": f"WrongPass#{idx}",
                },
                REMOTE_ADDR=exhausted_ip,
            )
            self.assertEqual(fail_resp.status_code, 200)
        with patch.object(
            User, "check_password", wraps=self.admin_user.check_password
        ) as mock_check_pw:
            sixth_resp = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": self.password},
                REMOTE_ADDR=exhausted_ip,
            )
            self.assertEqual(sixth_resp.status_code, 429)
            self.assertFalse(mock_check_pw.called)
        self.assertEqual(
            limiter.get_counts(exhausted_ip, self.admin_user.username),
            (5, 5),
        )

    def test_finding3_trusted_proxy_and_forwarded_ip_handling(self):
        """
        Phase 4.2.1.4 Finding 3 — Hardened X-Forwarded-For resolution with CRM_TRUSTED_PROXY_IPS:
        1. Default mode (CRM_TRUST_X_FORWARDED_FOR=False) ignores X-Forwarded-For.
        2. CRM_TRUST_X_FORWARDED_FOR=True with empty CRM_TRUSTED_PROXY_IPS ignores X-Forwarded-For.
        3. Untrusted REMOTE_ADDR attempting to spoof X-Forwarded-For is ignored.
        4. Trusted proxy with a valid forwarded address resolves the client IP.
        5. Multiple forwarded hops select the rightmost non-trusted IP, ignoring leftmost spoofed values.
        6. Malformed forwarded hops fail safely back to REMOTE_ADDR.
        """
        login_url = reverse("webcrm:login")
        limiter = LoginRateLimiter()

        # 1. Default mode ignores X-Forwarded-For even if CRM_TRUSTED_PROXY_IPS is populated
        self._clear_rate_limit_caches()
        with override_settings(
            CRM_TRUST_X_FORWARDED_FOR=False,
            CRM_TRUSTED_PROXY_IPS=("10.0.0.1",),
        ):
            self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR="203.0.113.10",
            )
            self.assertEqual(
                limiter.get_counts("10.0.0.1", self.admin_user.username),
                (1, 1),
            )
            self.assertEqual(
                limiter.get_counts("203.0.113.10", self.admin_user.username),
                (0, 0),
            )

        # 2. CRM_TRUST_X_FORWARDED_FOR=True with empty CRM_TRUSTED_PROXY_IPS ignores XFF
        self._clear_rate_limit_caches()
        with override_settings(
            CRM_TRUST_X_FORWARDED_FOR=True,
            CRM_TRUSTED_PROXY_IPS=(),
        ):
            self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR="203.0.113.11",
            )
            self.assertEqual(
                limiter.get_counts("10.0.0.1", self.admin_user.username),
                (1, 1),
            )
            self.assertEqual(
                limiter.get_counts("203.0.113.11", self.admin_user.username),
                (0, 0),
            )

        # 3. Untrusted REMOTE_ADDR attempting to spoof X-Forwarded-For is ignored
        self._clear_rate_limit_caches()
        with override_settings(
            CRM_TRUST_X_FORWARDED_FOR=True,
            CRM_TRUSTED_PROXY_IPS=("10.0.0.1", "10.0.1.0/24"),
        ):
            self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="198.51.100.99",
                HTTP_X_FORWARDED_FOR="203.0.113.12",
            )
            self.assertEqual(
                limiter.get_counts("198.51.100.99", self.admin_user.username),
                (1, 1),
            )
            self.assertEqual(
                limiter.get_counts("203.0.113.12", self.admin_user.username),
                (0, 0),
            )

            # 4. Trusted proxy (10.0.0.1) with valid single forwarded address
            self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR="203.0.113.42",
            )
            self.assertEqual(
                limiter.get_counts("203.0.113.42", self.admin_user.username),
                (1, 1),
            )

            # 5. Multiple hops: attacker spoofs leftmost "192.0.2.77", outer trusted proxy
            #    "10.0.1.5" and inner trusted proxy "10.0.0.1" append hops -> resolves "203.0.113.55"
            self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR="192.0.2.77, 203.0.113.55, 10.0.1.5",
            )
            self.assertEqual(
                limiter.get_counts("203.0.113.55", self.admin_user.username),
                (1, 1),
            )
            self.assertEqual(
                limiter.get_counts("192.0.2.77", self.admin_user.username),
                (0, 0),
            )

            # 6. Malformed rightmost hop falls back safely to REMOTE_ADDR ("10.0.0.1")
            self._clear_rate_limit_caches()
            self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": "bad"},
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR="203.0.113.60, not-an-ip",
            )
            self.assertEqual(
                limiter.get_counts("10.0.0.1", self.admin_user.username),
                (1, 1),
            )
            self.assertEqual(
                limiter.get_counts("203.0.113.60", self.admin_user.username),
                (0, 0),
            )

        # 7. Invalid CRM_TRUSTED_PROXY_IPS entry raises ImproperlyConfigured
        with self.assertRaises(ImproperlyConfigured):
            parse_trusted_proxy_networks(["10.0.0.1", "not-a-valid-cidr"])
        with self.assertRaises(ImproperlyConfigured):
            validate_login_rate_limit_settings(trusted_proxies=["bad_proxy_ip"])

    def test_finding4_bounded_thread_local_reservation_metadata(self):
        """
        Phase 4.2.1.4 Finding 4 — Thread-local expiry metadata is bounded and does not grow
        unboundedly across distinct login keys.
        """
        crm_cache: PostgresAtomicCache = caches[
            settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS
        ]
        crm_cache.clear()
        crm_cache._get_last_expires_map().clear()

        # 1. Direct atomic_increment across 200 distinct keys never exceeds MAX_TRACKED_EXPIRES_KEYS
        for idx in range(200):
            crm_cache.atomic_increment(
                f"webcrm:test:bounded_meta:{idx}", delta=1, timeout=60
            )
            self.assertLessEqual(
                len(crm_cache._get_last_expires_map()),
                PostgresAtomicCache.MAX_TRACKED_EXPIRES_KEYS,
            )

        # 2. LoginRateLimiter.admit_attempt pops per-key expiry entries immediately so 0 remain
        crm_cache._get_last_expires_map().clear()
        limiter = LoginRateLimiter()
        for idx in range(40):
            res = limiter.admit_attempt(f"198.51.100.{idx % 200 + 1}", f"user_{idx}")
            self.assertTrue(res.admitted)
            self.assertIsNotNone(res.user_ip_expires)
            self.assertIsNotNone(res.ip_expires)
            self.assertEqual(len(crm_cache._get_last_expires_map()), 0)
            self.assertTrue(limiter.reconcile_successful_attempt(res))
            self.assertNotIn("_auth_user_id", self.client.session)


class WebCRMLeadManagementTests(BaseWebCRMTestCase):
    """Step 5 & Step 12.2 — Lead CRUD, assignment, bulk assign, and staged imports."""

    def setUp(self):
        self.client.force_login(self.admin_user)

    def test_lead_create_edit_assign_and_archive_lifecycle(self):
        # 1. Create lead
        create_resp = self.client.post(
            reverse("webcrm:lead-create"),
            {
                "name": "Farhan Siddiqui",
                "phone": "9123456780",
                "alternate_phone": "9123456781",
                "email": "farhan@example.com",
                "course_interest": "MBA Finance",
                "enquiry_details": "Wants evening batch",
                "status": Lead.Status.NEW,
                "internal_notes": "High priority candidate",
            },
        )
        lead = Lead.objects.get(phone="9123456780")
        self.assertRedirects(create_resp, reverse("webcrm:lead-detail", args=[lead.pk]))
        self.assertEqual(lead.created_by, self.admin_user)
        self.assertEqual(lead.internal_notes, "High priority candidate")

        # 2. Edit lead
        edit_resp = self.client.post(
            reverse("webcrm:lead-edit", args=[lead.pk]),
            {
                "name": "Farhan A. Siddiqui",
                "phone": "9123456780",
                "email": "farhan@example.com",
                "course_interest": "MBA International Business",
                "status": Lead.Status.CONTACTED,
                "internal_notes": "Updated internal notes",
            },
        )
        self.assertRedirects(edit_resp, reverse("webcrm:lead-detail", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.name, "Farhan A. Siddiqui")
        self.assertEqual(lead.status, Lead.Status.CONTACTED)

        # 3. Assign lead to emp1 and verify LeadAssignmentHistory
        assign_resp = self.client.post(
            reverse("webcrm:lead-assign", args=[lead.pk]),
            {"assigned_to": self.emp1.pk, "reason": "Initial counselor allocation"},
        )
        self.assertRedirects(assign_resp, reverse("webcrm:lead-detail", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.emp1)
        history = LeadAssignmentHistory.objects.filter(lead=lead).first()
        self.assertIsNotNone(history)
        self.assertIsNone(history.previous_employee)
        self.assertEqual(history.new_employee, self.emp1)
        self.assertEqual(history.changed_by, self.admin_user)
        self.assertEqual(history.reason, "Initial counselor allocation")

        # 4. Reject assigning to inactive employee or superuser
        bad_assign = self.client.post(
            reverse("webcrm:lead-assign", args=[lead.pk]),
            {"assigned_to": self.inactive_emp.pk, "reason": "Should fail"},
        )
        self.assertEqual(bad_assign.status_code, 400)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.emp1)

        # 5. Archive lead and verify archived lead cannot be reassigned
        arch_resp = self.client.post(
            reverse("webcrm:lead-archive-toggle", args=[lead.pk]),
            {"action": "archive"},
        )
        self.assertRedirects(arch_resp, reverse("webcrm:lead-detail", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertTrue(lead.is_archived)

        archived_assign = self.client.post(
            reverse("webcrm:lead-assign", args=[lead.pk]),
            {"assigned_to": self.emp2.pk, "reason": "Cannot assign archived"},
        )
        self.assertEqual(archived_assign.status_code, 400)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.emp1)

    def test_atomic_bulk_assignment_and_rollback_on_archived_lead(self):
        l1 = Lead.objects.create(name="Bulk Lead 1", phone="9000011111", created_by=self.admin_user)
        l2 = Lead.objects.create(name="Bulk Lead 2", phone="9000011112", created_by=self.admin_user)
        l_arch = Lead.objects.create(
            name="Bulk Archived",
            phone="9000011113",
            is_archived=True,
            created_by=self.admin_user,
        )

        # 1. Valid atomic bulk assignment of l1 and l2
        resp = self.client.post(
            reverse("webcrm:lead-bulk-assign"),
            {
                "lead_ids": [l1.pk, l2.pk],
                "employee_id": self.emp1.pk,
                "reason": "Batch morning queue",
            },
        )
        self.assertRedirects(resp, reverse("webcrm:lead-list"))
        l1.refresh_from_db()
        l2.refresh_from_db()
        self.assertEqual(l1.assigned_to, self.emp1)
        self.assertEqual(l2.assigned_to, self.emp1)

        # 2. Including an archived lead rolls back the entire batch
        resp_fail = self.client.post(
            reverse("webcrm:lead-bulk-assign"),
            {
                "lead_ids": [l1.pk, l_arch.pk],
                "employee_id": self.emp2.pk,
                "reason": "Should roll back",
            },
        )
        self.assertRedirects(resp_fail, reverse("webcrm:lead-list"))
        l1.refresh_from_db()
        self.assertEqual(l1.assigned_to, self.emp1)

    def test_staged_csv_import_preview_confirmation_commit_and_cancel(self):
        csv_content = (
            "name,phone,course_interest,internal_notes\n"
            "Imported Student One,9222233331,BBA,Priority callback\n"
            "Imported Student Two,9222233332,BCA,\n"
        ).encode("utf-8")
        upload = SimpleUploadedFile("leads.csv", csv_content, content_type="text/csv")

        # 1. Preview stages the import without creating Lead rows
        preview_resp = self.client.post(
            reverse("webcrm:lead-import-preview"),
            {"file": upload, "duplicate_policy": StagedLeadImport.DuplicatePolicy.SKIP},
        )
        self.assertEqual(preview_resp.status_code, 200)
        self.assertFalse(Lead.objects.filter(phone="9222233331").exists())
        staged = preview_resp.context["staged"]
        commit_token = preview_resp.context["commit_token"]
        self.assertEqual(staged.state, StagedLeadImport.State.STAGED)

        # 2. Commit without confirm_commit="yes" is rejected and does not create leads
        no_confirm_resp = self.client.post(
            reverse("webcrm:lead-import-commit"),
            {
                "staged_import_id": staged.pk,
                "commit_token": commit_token,
            },
        )
        self.assertRedirects(no_confirm_resp, reverse("webcrm:lead-import"))
        self.assertFalse(Lead.objects.filter(phone="9222233331").exists())

        # 3. Commit with confirm_commit="yes" creates leads
        commit_resp = self.client.post(
            reverse("webcrm:lead-import-commit"),
            {
                "staged_import_id": staged.pk,
                "commit_token": commit_token,
                "confirm_commit": "yes",
            },
        )
        self.assertEqual(commit_resp.status_code, 200)
        imported_lead = Lead.objects.get(phone="9222233331")
        self.assertEqual(imported_lead.name, "Imported Student One")
        self.assertEqual(imported_lead.internal_notes, "Priority callback")
        staged.refresh_from_db()
        self.assertEqual(staged.state, StagedLeadImport.State.COMMITTED)

        # 4. Stage another upload and cancel it
        upload2 = SimpleUploadedFile("leads2.csv", csv_content, content_type="text/csv")
        preview2 = self.client.post(
            reverse("webcrm:lead-import-preview"),
            {"file": upload2, "duplicate_policy": StagedLeadImport.DuplicatePolicy.SKIP},
        )
        staged2 = preview2.context["staged"]
        cancel_resp = self.client.post(reverse("webcrm:lead-import-cancel", args=[staged2.pk]))
        self.assertRedirects(cancel_resp, reverse("webcrm:lead-import"))
        staged2.refresh_from_db()
        self.assertEqual(staged2.state, StagedLeadImport.State.CANCELLED)

    def test_stale_lead_edit_does_not_revert_concurrent_reassignment(self):
        """Phase 4.2.1 Fix 2 — Stale lead edit form cannot overwrite concurrent reassignment."""
        lead = Lead.objects.create(
            name="Concurrent Edit Student",
            phone="9555566661",
            course_interest="B.Tech",
            status=Lead.Status.NEW,
            assigned_to=self.emp1,
            created_by=self.admin_user,
        )

        # 1. Admin A opens the edit page while lead is assigned to emp1
        edit_get_resp = self.client.get(reverse("webcrm:lead-edit", args=[lead.pk]))
        self.assertEqual(edit_get_resp.status_code, 200)
        edit_form = edit_get_resp.context["form"]
        self.assertNotIn("assigned_to", edit_form.fields)
        self.assertNotIn("assignment_reason", edit_form.fields)

        # 2. Meanwhile, Admin B reassigns the lead from emp1 to emp2 via dedicated assign endpoint
        assign_resp = self.client.post(
            reverse("webcrm:lead-assign", args=[lead.pk]),
            {"assigned_to": self.emp2.pk, "reason": "Reassigned to Counselor Two"},
        )
        self.assertRedirects(assign_resp, reverse("webcrm:lead-detail", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.emp2)
        self.assertEqual(LeadAssignmentHistory.objects.filter(lead=lead).count(), 1)

        # 3. Admin A submits the edit form (even including stale assigned_to=emp1 in POST payload)
        stale_edit_resp = self.client.post(
            reverse("webcrm:lead-edit", args=[lead.pk]),
            {
                "name": "Concurrent Edit Student",
                "phone": "9555566661",
                "course_interest": "M.Tech Data Science",
                "status": Lead.Status.CONTACTED,
                "internal_notes": "Updated notes from Admin A",
                "assigned_to": str(self.emp1.pk),
                "assignment_reason": "Stale form submission",
            },
        )
        self.assertRedirects(stale_edit_resp, reverse("webcrm:lead-detail", args=[lead.pk]))

        # 4. Lead remains assigned to emp2, non-assignment fields updated, and no extra assignment history created
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.emp2)
        self.assertEqual(lead.course_interest, "M.Tech Data Science")
        self.assertEqual(lead.status, Lead.Status.CONTACTED)
        self.assertEqual(lead.internal_notes, "Updated notes from Admin A")
        self.assertEqual(LeadAssignmentHistory.objects.filter(lead=lead).count(), 1)

    def test_upload_limit_middleware_covers_both_api_and_crm_import_routes(self):
        """Phase 4.2.1 Fix 3 — LeadImportUploadLimitMiddleware enforces limits on both API and CRM routes."""
        oversized_content_length = str(settings.LEAD_IMPORT_MAX_REQUEST_SIZE + 1024)

        # 1. Oversized Content-Length on /crm/leads/import/preview/ is rejected with 413 before parsing
        crm_cl_resp = self.client.generic(
            "POST",
            reverse("webcrm:lead-import-preview"),
            b"small-body",
            content_type="multipart/form-data; boundary=---boundary",
            CONTENT_LENGTH=oversized_content_length,
        )
        self.assertEqual(crm_cl_resp.status_code, 413)

        # 2. Oversized Content-Length on /api/v1/leads/import/preview/ is also rejected with 413
        api_cl_resp = self.client.generic(
            "POST",
            "/api/v1/leads/import/preview/",
            b"small-body",
            content_type="multipart/form-data; boundary=---boundary",
            CONTENT_LENGTH=oversized_content_length,
        )
        self.assertEqual(api_cl_resp.status_code, 413)

        # 3. Streamed upload exceeding LEAD_IMPORT_MAX_FILE_SIZE on /crm/leads/import/preview/ returns 413
        with override_settings(
            LEAD_IMPORT_MAX_FILE_SIZE=256,
            LEAD_IMPORT_MAX_REQUEST_SIZE=1024 * 1024,
        ):
            oversized_csv = SimpleUploadedFile(
                "oversized.csv",
                b"name,phone\n" + (b"Student,9876543210\n" * 40),
                content_type="text/csv",
            )
            stream_resp = self.client.post(
                reverse("webcrm:lead-import-preview"),
                {"file": oversized_csv, "duplicate_policy": StagedLeadImport.DuplicatePolicy.SKIP},
            )
            self.assertEqual(stream_resp.status_code, 413)

        # 4. Valid CSV upload under the limit on /crm/leads/import/preview/ succeeds
        valid_csv = SimpleUploadedFile(
            "valid_small.csv",
            b"name,phone,course_interest\nValid Import,9666677771,MBA\n",
            content_type="text/csv",
        )
        valid_resp = self.client.post(
            reverse("webcrm:lead-import-preview"),
            {"file": valid_csv, "duplicate_policy": StagedLeadImport.DuplicatePolicy.SKIP},
        )
        self.assertEqual(valid_resp.status_code, 200)

        # 5. Unrelated CRM POST endpoints are unaffected
        unrelated_resp = self.client.post(
            reverse("webcrm:lead-create"),
            {
                "name": "Normal Lead After Upload Check",
                "phone": "9666677772",
                "status": Lead.Status.NEW,
            },
        )
        self.assertEqual(unrelated_resp.status_code, 302)


class WebCRMEmployeeManagementTests(BaseWebCRMTestCase):
    """Step 6 & Step 12.3 — Employee CRUD, superuser isolation, deactivation, and password reset."""

    def setUp(self):
        self.client.force_login(self.admin_user)

    def test_employee_list_excludes_superusers_and_never_exposes_hashes(self):
        resp = self.client.get(reverse("webcrm:employee-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.emp1.username)
        self.assertNotContains(resp, self.superuser_emp.username)
        self.assertNotContains(resp, "pbkdf2_")

    def test_superuser_returns_404_on_employee_detail_edit_toggle_and_reset(self):
        for url_name in (
            "webcrm:employee-detail",
            "webcrm:employee-edit",
            "webcrm:employee-toggle-active",
            "webcrm:employee-reset-password",
        ):
            method = self.client.get if url_name == "webcrm:employee-detail" else self.client.post
            resp = method(reverse(url_name, args=[self.superuser_emp.pk]))
            self.assertEqual(resp.status_code, 404, f"Expected 404 for {url_name} on superuser")

    def test_employee_create_edit_deactivate_and_password_reset_revoke_tokens(self):
        # 1. Create new employee
        create_resp = self.client.post(
            reverse("webcrm:employee-create"),
            {
                "username": "new_counselor",
                "first_name": "Neha",
                "last_name": "Verma",
                "email": "neha@iqracentral.com",
                "phone": "9888877776",
                "designation": "Admissions Executive",
                "password": "ValidPassword#2026",
            },
        )
        new_emp = User.objects.get(username="new_counselor")
        self.assertRedirects(create_resp, reverse("webcrm:employee-detail", args=[new_emp.pk]))
        self.assertEqual(new_emp.role, User.Role.EMPLOYEE)
        self.assertFalse(new_emp.is_superuser)

        # 2. Edit profile
        edit_resp = self.client.post(
            reverse("webcrm:employee-edit", args=[new_emp.pk]),
            {
                "first_name": "Neha",
                "last_name": "Sharma",
                "email": "neha.sharma@iqracentral.com",
                "phone": "9888877776",
                "designation": "Senior Admissions Executive",
            },
        )
        self.assertRedirects(edit_resp, reverse("webcrm:employee-detail", args=[new_emp.pk]))
        new_emp.refresh_from_db()
        self.assertEqual(new_emp.last_name, "Sharma")

        # 3. Issue a JWT RefreshToken for new_emp, then deactivate via web CRM
        token1 = RefreshToken.for_user(new_emp)
        jti1 = token1["jti"]
        deact_resp = self.client.post(
            reverse("webcrm:employee-toggle-active", args=[new_emp.pk]),
            {"action": "deactivate"},
        )
        self.assertRedirects(deact_resp, reverse("webcrm:employee-detail", args=[new_emp.pk]))
        new_emp.refresh_from_db()
        self.assertFalse(new_emp.is_active)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=jti1).exists())

        # 4. Reactivate and reset password -> revokes new refresh token
        self.client.post(
            reverse("webcrm:employee-toggle-active", args=[new_emp.pk]),
            {"action": "activate"},
        )
        token2 = RefreshToken.for_user(new_emp)
        jti2 = token2["jti"]
        reset_resp = self.client.post(
            reverse("webcrm:employee-reset-password", args=[new_emp.pk]),
            {
                "new_password": "ResetPassword#2027",
                "confirm_password": "ResetPassword#2027",
            },
        )
        self.assertRedirects(reset_resp, reverse("webcrm:employee-detail", args=[new_emp.pk]))
        new_emp.refresh_from_db()
        self.assertTrue(new_emp.check_password("ResetPassword#2027"))
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=jti2).exists())

    def test_employee_profile_edit_ignores_submitted_role_and_protected_fields(self):
        """Phase 4.2.1 Fix 4 — Crafted POST to employee edit cannot promote role or alter protected fields."""
        emp = User.objects.create_user(
            username="profile_only_emp",
            password=self.password,
            role=User.Role.EMPLOYEE,
            first_name="Original",
            last_name="Counselor",
            email="original@iqracentral.com",
            phone="9777766665",
            designation="Counselor",
            is_active=True,
        )

        # Submit crafted POST attempting to escalate role=ADMIN, is_superuser=true, is_active=false, password
        resp = self.client.post(
            reverse("webcrm:employee-edit", args=[emp.pk]),
            {
                "first_name": "UpdatedFirst",
                "last_name": "UpdatedLast",
                "email": "updated@iqracentral.com",
                "phone": "9777766660",
                "designation": "Lead Counselor",
                "role": User.Role.ADMIN,
                "is_superuser": "true",
                "is_staff": "true",
                "is_active": "false",
                "password": "HackedPassword#999",
            },
        )
        self.assertRedirects(resp, reverse("webcrm:employee-detail", args=[emp.pk]))

        emp.refresh_from_db()
        # Allowed profile fields updated
        self.assertEqual(emp.first_name, "UpdatedFirst")
        self.assertEqual(emp.last_name, "UpdatedLast")
        self.assertEqual(emp.email, "updated@iqracentral.com")
        self.assertEqual(emp.phone, "9777766660")
        self.assertEqual(emp.designation, "Lead Counselor")

        # Protected account fields remain untouched
        self.assertEqual(emp.role, User.Role.EMPLOYEE)
        self.assertFalse(emp.is_superuser)
        self.assertTrue(emp.is_active)
        self.assertTrue(emp.check_password(self.password))

        # Superuser account still returns 404 on edit
        su_resp = self.client.post(
            reverse("webcrm:employee-edit", args=[self.superuser_emp.pk]),
            {"first_name": "Attempt", "role": User.Role.EMPLOYEE},
        )
        self.assertEqual(su_resp.status_code, 404)

    def test_employee_reset_password_invalid_submission_preserves_detail_context(self):
        """UI Review Finding 5 — Invalid password reset (400) preserves KPIs, leads, calls, and follow-ups."""
        lead = Lead.objects.create(
            name="Preserved Context Student",
            phone="9765432109",
            assigned_to=self.emp1,
            created_by=self.admin_user,
        )
        now = datetime.datetime(2026, 6, 15, 11, 0, tzinfo=KOLKATA)
        call = Call.objects.create(
            lead=lead,
            caller=self.emp1,
            outcome=self.outcome,
            client_event_id=uuid.uuid4(),
            request_fingerprint="c" * 64,
            caller_username_snapshot=self.emp1.username,
            caller_name_snapshot="Amina Khan",
            outcome_code_snapshot=self.outcome.code,
            outcome_label_snapshot=self.outcome.label,
            started_at=now - datetime.timedelta(minutes=2),
            ended_at=now,
            duration_seconds=120,
            employee_notes="Preserved call note",
        )
        fu = FollowUp.objects._create_from_service(
            lead=lead,
            owner=self.emp1,
            call=call,
            scheduled_at=now + datetime.timedelta(days=1),
            status=FollowUp.Status.PENDING,
            notes="Preserved follow-up note",
        )

        bad_resp = self.client.post(
            reverse("webcrm:employee-reset-password", args=[self.emp1.pk]),
            {
                "new_password": "short",
                "confirm_password": "mismatch",
            },
        )
        self.assertEqual(bad_resp.status_code, 400)
        self.assertIsNotNone(bad_resp.context["performance"])
        self.assertEqual(
            bad_resp.context["performance"].assigned_active_leads_count,
            1,
        )
        self.assertEqual([l.pk for l in bad_resp.context["assigned_leads"]], [lead.pk])
        self.assertEqual([c.pk for c in bad_resp.context["recent_calls"]], [call.pk])
        self.assertEqual([f.pk for f in bad_resp.context["pending_followups"]], [fu.pk])
        self.assertContains(bad_resp, "Preserved Context Student", status_code=400)
        self.assertTrue(bool(bad_resp.context.get("password_error")))


class WebCRMCallsAndFollowUpsTests(BaseWebCRMTestCase):
    """Step 7, Step 8 & Step 12.4 — Calls, outcomes, and follow-up cancellation rules."""

    def setUp(self):
        self.client.force_login(self.admin_user)
        self.lead = Lead.objects.create(
            name="Call & FollowUp Lead",
            phone="9333344445",
            assigned_to=self.emp1,
            created_by=self.admin_user,
        )
        now = datetime.datetime(2026, 6, 15, 11, 0, tzinfo=KOLKATA)
        self.call = Call.objects.create(
            lead=self.lead,
            caller=self.emp1,
            outcome=self.outcome,
            client_event_id=uuid.uuid4(),
            request_fingerprint="a" * 64,
            caller_username_snapshot=self.emp1.username,
            caller_name_snapshot="Amina Khan",
            outcome_code_snapshot=self.outcome.code,
            outcome_label_snapshot="Original Snapshot Label",
            started_at=now - datetime.timedelta(minutes=5),
            ended_at=now,
            duration_seconds=300,
            employee_notes="Spoke with student about MBA fee structure.",
        )

    def test_call_history_and_detail_are_read_only_and_preserve_snapshots(self):
        # Rename live CallOutcome label — historical call view must still show snapshot ("Interested in Admission")
        CallOutcome.objects.filter(pk=self.outcome.pk).update(label="Renamed Live Label")

        list_resp = self.client.get(reverse("webcrm:call-list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, "Interested in Admission")

        detail_resp = self.client.get(reverse("webcrm:call-detail", args=[self.call.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Interested in Admission")
        self.assertNotContains(detail_resp, "Renamed Live Label")
        self.assertContains(detail_resp, "Spoke with student about MBA fee structure.")

        # Call list and detail reject POST
        self.assertEqual(self.client.post(reverse("webcrm:call-list")).status_code, 405)
        self.assertEqual(
            self.client.post(reverse("webcrm:call-detail", args=[self.call.pk])).status_code,
            405,
        )

    def test_call_outcome_create_update_and_immutable_code_enforcement(self):
        create_resp = self.client.post(
            reverse("webcrm:call-outcome-list"),
            {
                "code": "DOCUMENTS_PENDING",
                "label": "Documents Pending",
                "display_order": "5",
                "is_active": "true",
            },
        )
        self.assertRedirects(create_resp, reverse("webcrm:call-outcome-list"))
        outcome = CallOutcome.objects.get(code="DOCUMENTS_PENDING")

        # Attempting to modify code returns 400
        bad_update = self.client.post(
            reverse("webcrm:call-outcome-update", args=[outcome.pk]),
            {"code": "CHANGED_CODE", "label": "Documents Pending"},
        )
        self.assertEqual(bad_update.status_code, 400)

        # Updating label and active state succeeds
        ok_update = self.client.post(
            reverse("webcrm:call-outcome-update", args=[outcome.pk]),
            {"label": "Transcripts Pending", "display_order": "6", "is_active": "false"},
        )
        self.assertRedirects(ok_update, reverse("webcrm:call-outcome-list"))
        outcome.refresh_from_db()
        self.assertEqual(outcome.label, "Transcripts Pending")
        self.assertFalse(outcome.is_active)

    def test_followup_event_history_and_admin_cancellation_policy(self):
        sched = datetime.datetime(2026, 6, 20, 14, 0, tzinfo=KOLKATA)
        fu_active = FollowUp.objects._create_from_service(
            lead=self.lead,
            owner=self.emp1,
            call=self.call,
            scheduled_at=sched,
            status=FollowUp.Status.PENDING,
            notes="Follow up on scholarship documents",
        )
        idem_uuid = uuid.uuid4()
        FollowUpEvent.objects._create_from_service(
            follow_up=fu_active,
            actor=self.emp1,
            event_type=FollowUpEvent.EventType.CREATED,
            previous_status=None,
            new_status=FollowUp.Status.PENDING,
            previous_scheduled_at=None,
            new_scheduled_at=sched,
            idempotency_key=idem_uuid,
            request_fingerprint="f" * 64,
        )

        # 1. Detail view displays event history without exposing idempotency_key or fingerprint
        detail_resp = self.client.get(reverse("webcrm:followup-detail", args=[fu_active.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "CREATED")
        self.assertNotContains(detail_resp, str(idem_uuid))
        self.assertNotContains(detail_resp, "f" * 64)

        # 2. Admin cannot cancel a pending follow-up owned by an ACTIVE employee (403)
        active_cancel = self.client.post(
            reverse("webcrm:followup-cancel", args=[fu_active.pk]),
            {"reason": "Admin trying to cancel active employee follow-up"},
        )
        self.assertEqual(active_cancel.status_code, 403)
        fu_active.refresh_from_db()
        self.assertEqual(fu_active.status, FollowUp.Status.PENDING)

        # 3. Create pending follow-up owned by INACTIVE employee
        fu_inactive = FollowUp.objects._create_from_service(
            lead=self.lead,
            owner=self.inactive_emp,
            scheduled_at=sched,
            status=FollowUp.Status.PENDING,
            notes="Leftover follow-up",
        )

        # Blank reason is rejected with 400
        blank_cancel = self.client.post(
            reverse("webcrm:followup-cancel", args=[fu_inactive.pk]),
            {"reason": "   "},
        )
        self.assertEqual(blank_cancel.status_code, 400)
        fu_inactive.refresh_from_db()
        self.assertEqual(fu_inactive.status, FollowUp.Status.PENDING)

        # Valid nonblank reason cancels follow-up and records FollowUpEvent
        ok_cancel = self.client.post(
            reverse("webcrm:followup-cancel", args=[fu_inactive.pk]),
            {"reason": "Employee left organization; reassigning lead"},
        )
        self.assertRedirects(ok_cancel, reverse("webcrm:followup-detail", args=[fu_inactive.pk]))
        fu_inactive.refresh_from_db()
        self.assertEqual(fu_inactive.status, FollowUp.Status.CANCELLED)
        cancel_event = FollowUpEvent.objects.filter(
            follow_up=fu_inactive,
            event_type=FollowUpEvent.EventType.CANCELLED,
        ).first()
        self.assertIsNotNone(cancel_event)
        self.assertEqual(cancel_event.actor, self.admin_user)
        self.assertEqual(cancel_event.reason, "Employee left organization; reassigning lead")


class WebCRMReportsAndExportsTests(BaseWebCRMTestCase):
    """Step 4, Step 9 & Step 12.5 — Dashboard accuracy, reports hub, and safe CSV/XLSX exports."""

    def setUp(self):
        self.client.force_login(self.admin_user)
        self.formula_lead = Lead.objects.create(
            name="=CMD|' /C calc'!A0",
            phone="9444455556",
            course_interest="  +SUM(1,1)",
            assigned_to=self.emp1,
            internal_notes="CONFIDENTIAL_INTERNAL_NOTE_DO_NOT_EXPORT",
            created_by=self.admin_user,
        )
        in_window = datetime.datetime(2026, 6, 10, 12, 0, tzinfo=KOLKATA)
        models.QuerySet.update(
            Lead.objects.filter(pk=self.formula_lead.pk),
            created_at=in_window,
        )

    def test_dashboard_metrics_match_reporting_service_and_support_htmx(self):
        resp = self.client.get(
            reverse("webcrm:dashboard"),
            {"date_from": "2026-06-01", "date_to": "2026-06-30"},
        )
        self.assertEqual(resp.status_code, 200)
        expected = build_dashboard_summary(
            date_from=datetime.date(2026, 6, 1),
            date_to=datetime.date(2026, 6, 30),
        )
        self.assertEqual(resp.context["summary"], expected)

        # HTMX partial request returns partial template without full HTML shell
        hx_resp = self.client.get(
            reverse("webcrm:dashboard"),
            {"date_from": "2026-06-01", "date_to": "2026-06-30"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="dashboard-content",
        )
        self.assertEqual(hx_resp.status_code, 200)
        self.assertNotContains(hx_resp, "<!DOCTYPE html>")
        self.assertContains(hx_resp, "Current Workload Snapshot")

    def test_reports_hub_and_csv_xlsx_exports_neutralize_formulas_and_omit_internal_notes(self):
        hub_resp = self.client.get(
            reverse("webcrm:reports-hub"),
            {
                "report_type": "leads",
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
            },
        )
        self.assertEqual(hub_resp.status_code, 200)

        # CSV export
        csv_resp = self.client.get(
            reverse("webcrm:reports-export"),
            {
                "report_type": "leads",
                "export_format": "csv",
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
            },
        )
        self.assertEqual(csv_resp.status_code, 200)
        self.assertTrue(csv_resp["Content-Type"].startswith("text/csv"))
        csv_text = csv_resp.content.decode("utf-8-sig")
        self.assertIn("'=CMD|' /C calc'!A0", csv_text)
        self.assertIn("'  +SUM(1,1)", csv_text)
        self.assertNotIn("CONFIDENTIAL_INTERNAL_NOTE_DO_NOT_EXPORT", csv_text)

        # XLSX export
        xlsx_resp = self.client.get(
            reverse("webcrm:reports-export"),
            {
                "report_type": "leads",
                "export_format": "xlsx",
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
            },
        )
        self.assertEqual(xlsx_resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(xlsx_resp.content)) as zf:
            sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("&apos;=CMD|&apos; /C calc&apos;!A0", sheet_xml)
        self.assertIn("&apos;  +SUM(1,1)", sheet_xml)
        self.assertNotIn("CONFIDENTIAL_INTERNAL_NOTE_DO_NOT_EXPORT", sheet_xml)

    def test_dashboard_employee_filter_scopes_lead_kpis_and_pipeline_distribution(self):
        """UI Review Finding 2 — Dashboard employee filter scopes lead KPIs, totals, and pipeline distribution."""
        in_window = datetime.datetime(2026, 6, 12, 10, 0, tzinfo=KOLKATA)

        # self.emp1 already has self.formula_lead (NEW, active, created in_window).
        # Add 1 CONVERTED active lead and 1 archived lead for emp1:
        emp1_converted = Lead.objects.create(
            name="Emp1 Converted Lead",
            phone="9444400001",
            status=Lead.Status.CONVERTED,
            assigned_to=self.emp1,
            created_by=self.admin_user,
        )
        emp1_archived = Lead.objects.create(
            name="Emp1 Archived Lead",
            phone="9444400002",
            status=Lead.Status.NOT_INTERESTED,
            assigned_to=self.emp1,
            is_archived=True,
            created_by=self.admin_user,
        )
        # Add 1 INTERESTED active lead for emp2 (and 0 leads in other statuses):
        emp2_interested = Lead.objects.create(
            name="Emp2 Interested Lead",
            phone="9444400003",
            status=Lead.Status.INTERESTED,
            assigned_to=self.emp2,
            created_by=self.admin_user,
        )
        # Add 1 unassigned active lead:
        unassigned_lead = Lead.objects.create(
            name="Unassigned Org Lead",
            phone="9444400004",
            status=Lead.Status.CONTACTED,
            assigned_to=None,
            created_by=self.admin_user,
        )
        models.QuerySet.update(
            Lead.objects.filter(
                pk__in=[
                    emp1_converted.pk,
                    emp1_archived.pk,
                    emp2_interested.pk,
                    unassigned_lead.pk,
                ]
            ),
            created_at=in_window,
        )

        # 1. Dashboard WITHOUT employee filter -> organization-wide lead KPIs
        org_resp = self.client.get(
            reverse("webcrm:dashboard"),
            {"date_from": "2026-06-01", "date_to": "2026-06-30"},
        )
        self.assertEqual(org_resp.status_code, 200)
        self.assertIsNone(org_resp.context["employee_total_leads"])
        self.assertEqual(org_resp.context["total_leads_all"], 5)
        self.assertContains(org_resp, "Active leads")
        self.assertContains(org_resp, "Total leads")
        self.assertContains(org_resp, "New leads in period")
        self.assertNotContains(org_resp, "Assigned active leads")

        # 2. Dashboard WITH employee filter for emp1 (2 active: 1 NEW, 1 CONVERTED; 1 archived; 3 total assigned)
        emp1_resp = self.client.get(
            reverse("webcrm:dashboard"),
            {
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
                "employee_id": str(self.emp1.pk),
            },
        )
        self.assertEqual(emp1_resp.status_code, 200)
        self.assertEqual(emp1_resp.context["employee_total_leads"], 3)
        emp1_filtered = emp1_resp.context["summary"]["leads"]["filtered_employee_leads"]
        self.assertEqual(emp1_filtered["assigned_active"], 2)
        self.assertEqual(emp1_filtered["assigned_archived"], 1)
        self.assertEqual(emp1_filtered["created_in_period_assigned_to_employee"], 3)
        self.assertContains(emp1_resp, "Assigned active leads")
        self.assertContains(emp1_resp, "Total assigned leads")
        self.assertContains(emp1_resp, "New leads in period (assigned)")
        self.assertContains(emp1_resp, "Calling team (org-wide)")
        self.assertContains(emp1_resp, "status-NEW")
        self.assertContains(emp1_resp, "status-CONVERTED")
        self.assertNotContains(emp1_resp, "status-INTERESTED")

        # 3 & 4. Dashboard WITH employee filter for emp2 (1 active INTERESTED lead, 0 archived, 0 in other statuses)
        emp2_resp = self.client.get(
            reverse("webcrm:dashboard"),
            {
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
                "employee_id": str(self.emp2.pk),
            },
        )
        self.assertEqual(emp2_resp.status_code, 200)
        self.assertEqual(emp2_resp.context["employee_total_leads"], 1)
        emp2_filtered = emp2_resp.context["summary"]["leads"]["filtered_employee_leads"]
        self.assertEqual(emp2_filtered["assigned_active"], 1)
        self.assertEqual(emp2_filtered["assigned_archived"], 0)
        self.assertEqual(emp2_filtered["created_in_period_assigned_to_employee"], 1)
        self.assertEqual(
            emp2_filtered["current_active_by_status"],
            {
                "NEW": 0,
                "CONTACTED": 0,
                "INTERESTED": 1,
                "NOT_INTERESTED": 0,
                "CONVERTED": 0,
            },
        )
        # Only status-INTERESTED has a segment in the pipeline bar; empty statuses are omitted from the bar
        self.assertContains(emp2_resp, "status-INTERESTED")
        self.assertNotContains(emp2_resp, "status-NEW")
        self.assertNotContains(emp2_resp, "status-CONVERTED")
        self.assertContains(
            emp2_resp,
            f"?status=INTERESTED&amp;assigned_to={self.emp2.pk}",
        )

    def test_dashboard_resolves_kolkata_period_only_once(self):
        """UI Review Finding 3 — dashboard_view resolves period once and reuses it for summary and employee queryset."""
        from unittest import mock
        from apps.reports.services import resolve_kolkata_period as real_resolve

        with mock.patch(
            "apps.webcrm.views.resolve_kolkata_period",
            wraps=real_resolve,
        ) as mocked_view_resolve, mock.patch(
            "apps.reports.services.resolve_kolkata_period",
            wraps=real_resolve,
        ) as mocked_service_resolve:
            resp = self.client.get(reverse("webcrm:dashboard"))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(mocked_view_resolve.call_count, 1)
            self.assertEqual(mocked_service_resolve.call_count, 0)

    def test_production_urls_do_not_expose_unconditional_staticfiles_serve_and_sidebar_active_states(self):
        """UI Review Finding 1 & Finding 6 — No unconditional staticfiles.serve in config.urls; sidebar active states."""
        import config.urls as root_urls

        self.assertFalse(settings.DEBUG)
        static_patterns = [
            p for p in root_urls.urlpatterns if "static" in str(getattr(p, "pattern", ""))
        ]
        self.assertEqual(static_patterns, [])

        # Sidebar highlights Import Leads even when lead-import-cancel returns HTTP 400
        cancel_resp = self.client.post(
            reverse("webcrm:lead-import-cancel", args=[999999])
        )
        self.assertEqual(cancel_resp.status_code, 400)
        html = cancel_resp.content.decode("utf-8")
        self.assertIn(
            f'href="{reverse("webcrm:lead-import")}"\n             class="crm-nav-link active"',
            html,
        )


@skipUnless(
    connection.vendor == "postgresql",
    "Concurrent CRM login rate-limit tests require PostgreSQL",
)
class WebCRMLoginConcurrencyTests(TransactionTestCase):
    """Phase 4.2.1.1 — Multi-threaded PostgreSQL concurrency tests for CRM login rate limiting."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        cache.clear()
        caches[settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS].clear()
        self.password = "ConcurrentAdmin#2026"
        self.admin_user = User.objects.create_user(
            username="concurrent_crm_admin",
            password=self.password,
            role=User.Role.ADMIN,
            is_active=True,
        )

    def tearDown(self):
        cache.clear()
        caches[settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS].clear()
        super().tearDown()

    def test_concurrent_failed_login_attempts_enforce_exact_threshold(self):
        """
        M-1 — 8 simultaneous failed login attempts across independent worker connections:
        - Produce an exact counter of 8 in PostgreSQL.
        - Admit exactly 5 requests to PBKDF2 password verification (returning HTTP 200).
        - Reject the remaining 3 requests with HTTP 429 BEFORE password verification runs.
        - Hold no database transaction open while PBKDF2 executes.
        """
        worker_count = 8
        barrier = threading.Barrier(worker_count)
        client_ip = "198.51.100.150"
        login_url = reverse("webcrm:login")
        pbkdf2_lock = threading.Lock()
        pbkdf2_in_atomic_states: list[bool] = []
        original_check_password = User.check_password

        def _tracked_check_password(user_self, raw_password):
            with pbkdf2_lock:
                pbkdf2_in_atomic_states.append(
                    bool(connections["default"].in_atomic_block)
                )
            return original_check_password(user_self, raw_password)

        def _worker(attempt_idx: int) -> int:
            close_old_connections()
            try:
                worker_client = Client()
                barrier.wait(timeout=10)
                response = worker_client.post(
                    login_url,
                    {
                        "username": self.admin_user.username,
                        "password": f"WrongConcurrentPass#{attempt_idx}",
                    },
                    REMOTE_ADDR=client_ip,
                )
                return response.status_code
            finally:
                connections.close_all()

        with patch.object(User, "check_password", new=_tracked_check_password):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [
                    executor.submit(_worker, idx) for idx in range(worker_count)
                ]
                status_codes = [future.result(timeout=30) for future in futures]

        self.assertEqual(status_codes.count(200), 5, f"Statuses: {status_codes}")
        self.assertEqual(status_codes.count(429), 3, f"Statuses: {status_codes}")
        # Only the 5 admitted requests executed password hashing; the 3 excess requests
        # were rejected with HTTP 429 before PBKDF2 ran.
        self.assertEqual(len(pbkdf2_in_atomic_states), 5)
        self.assertEqual(pbkdf2_in_atomic_states, [False] * 5)

        # Verify exact atomic counts in PostgreSQL: only the 5 admitted requests retain increments;
        # the 3 rejected requests rolled back their increments at admission.
        inspector_limiter = LoginRateLimiter(
            cache_backend=PostgresAtomicCache(
                "iqra_shared_cache", {"TIMEOUT": 300, "OPTIONS": {}}
            )
        )
        user_ip_count, ip_count = inspector_limiter.get_counts(
            client_ip, self.admin_user.username
        )
        self.assertEqual(user_ip_count, 5)
        self.assertEqual(ip_count, 5)
        self.assertTrue(
            inspector_limiter.is_rate_limited(client_ip, self.admin_user.username)
        )

        # Follow-up attempt with valid credentials while throttled still returns 429
        blocked_valid_resp = self.client.post(
            login_url,
            {"username": self.admin_user.username, "password": self.password},
            REMOTE_ADDR=client_ip,
        )
        self.assertEqual(blocked_valid_resp.status_code, 429)

    def test_concurrent_attempts_at_exact_threshold_across_independent_limiter_instances(self):
        """
        M-1 — 5 simultaneous attempts at the exact threshold (5) across 5 independent
        LoginRateLimiter + PostgresAtomicCache instances are all admitted, and the 6th is rejected.
        """
        worker_count = 5
        barrier = threading.Barrier(worker_count)
        client_ip = "198.51.100.151"

        def _worker(attempt_idx: int) -> bool:
            close_old_connections()
            try:
                independent_cache = PostgresAtomicCache(
                    "iqra_shared_cache", {"TIMEOUT": 300, "OPTIONS": {}}
                )
                independent_limiter = LoginRateLimiter(
                    cache_backend=independent_cache
                )
                barrier.wait(timeout=10)
                reservation = independent_limiter.admit_attempt(
                    client_ip, self.admin_user.username
                )
                return reservation.admitted
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_worker, idx) for idx in range(worker_count)]
            admitted_flags = [future.result(timeout=30) for future in futures]

        self.assertEqual(admitted_flags, [True] * 5)

        verifier = LoginRateLimiter(
            cache_backend=PostgresAtomicCache(
                "iqra_shared_cache", {"TIMEOUT": 300, "OPTIONS": {}}
            )
        )
        self.assertEqual(
            verifier.get_counts(client_ip, self.admin_user.username), (5, 5)
        )
        sixth = verifier.admit_attempt(client_ip, self.admin_user.username)
        self.assertFalse(sixth.admitted)

    def test_valid_password_racing_with_failed_passwords_preserves_failure_counts_and_blocks_on_saturation(self):
        """
        M-1 — Valid-password request racing with concurrent failed-password requests:
        1. When 4 failed requests + 1 valid request race (5 total <= threshold 5),
           the valid login succeeds (302), the 4 failures return 200, and the final
           PostgreSQL counter is (4, 4) — none of the 4 concurrent failures is erased.
        2. When 5 failed requests + 1 valid request race simultaneously (6 total, with
           5 failures saturating the threshold), the valid request is denied with HTTP 429
           and the account remains throttled.
        """
        login_url = reverse("webcrm:login")

        # Subcase 1: 4 concurrent failures + 1 concurrent valid request (5 total)
        client_ip_sub1 = "198.51.100.152"
        barrier_sub1 = threading.Barrier(5)

        def _worker_sub1(idx: int) -> int:
            close_old_connections()
            try:
                worker_client = Client()
                pwd = self.password if idx == 0 else f"WrongRacePass#{idx}"
                barrier_sub1.wait(timeout=10)
                resp = worker_client.post(
                    login_url,
                    {"username": self.admin_user.username, "password": pwd},
                    REMOTE_ADDR=client_ip_sub1,
                )
                return resp.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures_sub1 = [executor.submit(_worker_sub1, idx) for idx in range(5)]
            codes_sub1 = [f.result(timeout=30) for f in futures_sub1]

        self.assertEqual(codes_sub1.count(302), 1, f"Subcase 1 statuses: {codes_sub1}")
        self.assertEqual(codes_sub1.count(200), 4, f"Subcase 1 statuses: {codes_sub1}")

        inspector = LoginRateLimiter(
            cache_backend=PostgresAtomicCache(
                "iqra_shared_cache", {"TIMEOUT": 300, "OPTIONS": {}}
            )
        )
        # All 4 concurrent failed attempts remain recorded; valid login released only its own +1 slot
        self.assertEqual(
            inspector.get_counts(client_ip_sub1, self.admin_user.username),
            (4, 4),
        )

        # Subcase 2: Valid login admitted alongside 4 failed attempts (5 admitted total) while a
        # 6th concurrent failed attempt is rejected at admission before PBKDF2 completes.
        # Because the rejected 6th request does NOT retain an increment, the admitted valid
        # login still succeeds (HTTP 302) upon reconciliation and leaves the 4 failures (4, 4).
        client_ip_sub2 = "198.51.100.153"
        original_admit = LoginRateLimiter.admit_attempt
        valid_admitted_event = threading.Event()
        sixth_rejected_event = threading.Event()

        def _ordered_admit(limiter_self, ip, uname):
            res = original_admit(limiter_self, ip, uname)
            if ip == client_ip_sub2 and res.admitted and res.user_ip_count == 5:
                valid_admitted_event.set()
                sixth_rejected_event.wait(timeout=5)
            return res

        # Pre-record 4 admitted failed attempts so the valid login takes slot 5
        for idx in range(4):
            r_pre = self.client.post(
                login_url,
                {
                    "username": self.admin_user.username,
                    "password": f"WrongPrePass#{idx}",
                },
                REMOTE_ADDR=client_ip_sub2,
            )
            self.assertEqual(r_pre.status_code, 200)

        def _valid_worker() -> int:
            close_old_connections()
            try:
                worker_client = Client()
                resp = worker_client.post(
                    login_url,
                    {
                        "username": self.admin_user.username,
                        "password": self.password,
                    },
                    REMOTE_ADDR=client_ip_sub2,
                )
                return resp.status_code
            finally:
                connections.close_all()

        def _sixth_rejected_worker() -> int:
            close_old_connections()
            try:
                valid_admitted_event.wait(timeout=5)
                worker_client = Client()
                resp = worker_client.post(
                    login_url,
                    {
                        "username": self.admin_user.username,
                        "password": "WrongRejectedSixthPass#6",
                    },
                    REMOTE_ADDR=client_ip_sub2,
                )
                sixth_rejected_event.set()
                return resp.status_code
            finally:
                connections.close_all()

        with patch.object(LoginRateLimiter, "admit_attempt", new=_ordered_admit):
            with ThreadPoolExecutor(max_workers=2) as executor:
                fut_valid = executor.submit(_valid_worker)
                fut_sixth = executor.submit(_sixth_rejected_worker)
                valid_code = fut_valid.result(timeout=30)
                sixth_code = fut_sixth.result(timeout=30)

        self.assertEqual(valid_code, 302)
        self.assertEqual(sixth_code, 429)
        self.assertEqual(
            inspector.get_counts(client_ip_sub2, self.admin_user.username),
            (4, 4),
        )

        # Subcase 3: When 5 failed requests are admitted first (saturating threshold 5),
        # a concurrent 6th valid-password request is rejected at admission (HTTP 429) before PBKDF2
        client_ip_sub3 = "198.51.100.154"
        five_failures_admitted = threading.Event()
        admit_lock = threading.Lock()
        failure_admits = 0

        def _saturate_first_admit(limiter_self, ip, uname):
            nonlocal failure_admits
            res = original_admit(limiter_self, ip, uname)
            if ip == client_ip_sub3:
                with admit_lock:
                    failure_admits += 1
                    if failure_admits == 5:
                        five_failures_admitted.set()
            return res

        def _saturating_worker(idx: int) -> tuple[bool, int]:
            close_old_connections()
            try:
                worker_client = Client()
                is_valid = idx == 0
                if is_valid:
                    five_failures_admitted.wait(timeout=5)
                pwd = self.password if is_valid else f"WrongSatPass#{idx}"
                resp = worker_client.post(
                    login_url,
                    {"username": self.admin_user.username, "password": pwd},
                    REMOTE_ADDR=client_ip_sub3,
                )
                return is_valid, resp.status_code
            finally:
                connections.close_all()

        with patch.object(
            LoginRateLimiter, "admit_attempt", new=_saturate_first_admit
        ):
            with ThreadPoolExecutor(max_workers=6) as executor:
                futs_sub3 = [
                    executor.submit(_saturating_worker, idx) for idx in range(6)
                ]
                results_sub3 = [f.result(timeout=30) for f in futs_sub3]

        valid_codes_sub3 = [code for is_val, code in results_sub3 if is_val]
        failed_codes_sub3 = [code for is_val, code in results_sub3 if not is_val]
        self.assertEqual(valid_codes_sub3, [429])
        self.assertEqual(failed_codes_sub3, [200] * 5)
        self.assertEqual(
            inspector.get_counts(client_ip_sub3, self.admin_user.username),
            (5, 5),
        )

    def test_finding2_atomic_paired_admission_and_reconciliation_rollback_on_second_key_error(self):
        """
        Phase 4.2.1.4 Finding 2 — Real PostgreSQL transaction rollback tests verifying:
        1. If the 2nd counter increment (ip_key) fails in PostgreSQL during admit_attempt(),
           the 1st counter increment (user_ip_key) is genuinely rolled back in PostgreSQL.
        2. If the 2nd slot release (ip_key) fails in PostgreSQL during reconcile_successful_attempt(),
           the 1st slot release (user_ip_key) is genuinely rolled back in PostgreSQL and no
           authenticated session is established.
        """
        login_url = reverse("webcrm:login")
        crm_cache: PostgresAtomicCache = caches[
            settings.CRM_LOGIN_RATELIMIT_CACHE_ALIAS
        ]
        limiter = LoginRateLimiter()
        client_ip = "198.51.100.160"
        user_ip_key, ip_key = limiter.build_keys(
            client_ip, self.admin_user.username
        )
        normalized_ip_db_key = crm_cache._normalize_key(ip_key)

        # 1. Real PostgreSQL CHECK constraint causes the 2nd INSERT/UPDATE (ip_key) to fail
        #    after the 1st INSERT/UPDATE (user_ip_key) has already executed in the transaction
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE "iqra_shared_cache" '
                'DROP CONSTRAINT IF EXISTS "test_fail_second_admit_chk"'
            )
            cursor.execute(
                'ALTER TABLE "iqra_shared_cache" '
                'ADD CONSTRAINT "test_fail_second_admit_chk" '
                'CHECK ("cache_key" <> %s) NOT VALID',
                [normalized_ip_db_key],
            )
        try:
            resp_admit_fail = self.client.post(
                login_url,
                {"username": self.admin_user.username, "password": self.password},
                REMOTE_ADDR=client_ip,
            )
            self.assertEqual(resp_admit_fail.status_code, 503)
            self.assertNotIn("_auth_user_id", self.client.session)
            # First increment (user_ip_key) must have been rolled back by PostgreSQL
            self.assertEqual(
                limiter.get_counts(client_ip, self.admin_user.username),
                (0, 0),
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    'ALTER TABLE "iqra_shared_cache" '
                    'DROP CONSTRAINT IF EXISTS "test_fail_second_admit_chk"'
                )

        # 2. Real PostgreSQL CHECK constraint causes the 2nd release (ip_key -> int_value = 0)
        #    in reconcile_successful_attempt() to fail after the 1st release (user_ip_key -> 0)
        #    has already executed in the transaction.
        #    During admit_attempt(), int_value is 1 (so the CHECK constraint passes and (1, 1)
        #    is committed); during reconcile_successful_attempt(), user_ip_key updates to 0 and
        #    ip_key fails the CHECK constraint on int_value = 0, rolling back both releases to (1, 1).
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE "iqra_shared_cache" '
                'DROP CONSTRAINT IF EXISTS "test_fail_second_release_chk"'
            )
            cursor.execute(
                'ALTER TABLE "iqra_shared_cache" '
                'ADD CONSTRAINT "test_fail_second_release_chk" '
                'CHECK (NOT ("cache_key" = %s AND "int_value" = 0)) NOT VALID',
                [normalized_ip_db_key],
            )
        try:
            with self.assertLogs("apps.webcrm", level="ERROR") as reconcile_logs:
                resp_reconcile_fail = self.client.post(
                    login_url,
                    {
                        "username": self.admin_user.username,
                        "password": self.password,
                    },
                    REMOTE_ADDR=client_ip,
                )
            self.assertEqual(resp_reconcile_fail.status_code, 503)
            self.assertNotIn("_auth_user_id", self.client.session)
            self.assertIn(
                "reconciliation failed due to storage error (IntegrityError)",
                "\n".join(reconcile_logs.output),
            )
            # Admission committed (1, 1) before PBKDF2, and reconciliation rolled back BOTH
            # decrements when the 2nd release failed, so both counters remain at (1, 1)
            self.assertEqual(
                limiter.get_counts(client_ip, self.admin_user.username),
                (1, 1),
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    'ALTER TABLE "iqra_shared_cache" '
                    'DROP CONSTRAINT IF EXISTS "test_fail_second_release_chk"'
                )

    def test_l2_concurrent_table_initialization_across_independent_connections(self):
        """
        L-2 — Simultaneous _ensure_table() and atomic_increment() across 6 independent
        PostgreSQL connections serialize cleanly via pg_advisory_xact_lock without DDL errors.
        """
        temp_table = "iqra_shared_cache_init_test"
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{temp_table}"')

        worker_count = 6
        barrier = threading.Barrier(worker_count)

        def _worker() -> int:
            close_old_connections()
            try:
                worker_cache = PostgresAtomicCache(
                    temp_table, {"TIMEOUT": 300, "OPTIONS": {}}
                )
                barrier.wait(timeout=10)
                worker_cache._ensure_table()
                return worker_cache.atomic_increment(
                    "webcrm:test:concurrent_init_key", delta=1, timeout=60
                )
            finally:
                connections.close_all()

        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(_worker) for _ in range(worker_count)]
                counts = sorted(f.result(timeout=30) for f in futures)

            self.assertEqual(counts, [1, 2, 3, 4, 5, 6])
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
