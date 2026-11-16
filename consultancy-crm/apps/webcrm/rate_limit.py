"""
Shared, atomic rate-limiting storage and helpers for IQRA TCS Admin Web CRM (/crm/login/).

Design:
- ``PostgresAtomicCache`` is a Django ``BaseCache`` implementation backed by the shared
  PostgreSQL database (``DATABASES["default"]``). It uses single-statement PostgreSQL
  ``INSERT ... ON CONFLICT ("cache_key") DO UPDATE ... RETURNING`` and ``UPDATE ... RETURNING``
  queries evaluated against PostgreSQL's ``STATEMENT_TIMESTAMP()`` clock so counter
  increments, reservation reconciliation, and TTL enforcement are atomic across multiple
  Gunicorn/Daphne worker processes without requiring an external Redis service.
- ``LoginRateLimiter`` enforces both per-(username, IP) and IP-wide failed login
  thresholds using ``PostgresAtomicCache`` on ``CACHES["crm_ratelimit"]`` by default.
- Atomic pre-authentication admission (``admit_attempt``) increments both counters in a
  short committed transaction *before* PBKDF2 password hashing runs. Once either threshold
  is reached, subsequent requests are rejected with HTTP 429 before password verification,
  and no database transaction or row lock is held open during PBKDF2.
- On successful authentication, ``reconcile_successful_attempt`` releases only the single
  window-bound ``+1`` reservation acquired by that request, never erasing prior or
  concurrent failures on either ``(client_ip, username)`` or ``client_ip``.
- ``clear_attempts`` deletes only the per-(username, IP) counter and preserves the
  IP-wide counter so it expires naturally.
- Process-local backends (``LocMemCache``, ``DummyCache``), Django's ``RedisCache``, and
  any backend that does not explicitly set ``is_shared_backend = True`` and provide a
  callable ``atomic_increment`` method are rejected.
- If shared rate-limit storage raises an operational error or is misconfigured,
  ``LoginRateLimiter`` logs a safe operational error (without credentials, usernames,
  or client IPs) and raises ``RateLimitStorageUnavailableError`` so ``login_view``
  fails closed with HTTP 503.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import ipaddress
import logging
import pickle
import re
import threading
import unicodedata

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import DEFAULT_TIMEOUT, BaseCache
from django.core.cache.backends.dummy import DummyCache
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, transaction

try:
    from django.core.cache.backends.redis import RedisCache
except ImportError:  # pragma: no cover
    RedisCache = ()  # type: ignore[assignment]

logger = logging.getLogger("apps.webcrm")

TABLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,45}$")
DEFAULT_PRUNE_BATCH_SIZE = 128
MAX_TRACKED_EXPIRES_KEYS = 8


class RateLimitStorageUnavailableError(RuntimeError):
    """Raised when shared login rate-limit storage is unavailable or unsupported."""


class RateLimitConfigurationError(
    ImproperlyConfigured, RateLimitStorageUnavailableError
):
    """Raised when login rate-limit settings or table identifiers are invalid."""


def normalize_login_username(raw_username: str | None) -> str:
    """Normalize a login username using NFKC, stripping whitespace, and casefolding."""
    return unicodedata.normalize("NFKC", raw_username or "").strip().casefold()


def validate_table_identifier(table_name: str) -> str:
    """
    Validate a PostgreSQL cache table identifier against a strict allowlist.

    Rejects empty strings, non-strings, identifiers starting with digits,
    punctuation/quotes/SQL tokens, or identifiers longer than 46 characters
    (preserving headroom for the ``_expires_idx`` suffix under PostgreSQL's
    63-byte identifier limit).
    """
    if not isinstance(table_name, str) or not TABLE_IDENTIFIER_RE.fullmatch(table_name):
        raise RateLimitConfigurationError(
            f"Invalid PostgresAtomicCache table identifier: {table_name!r}. "
            "Must match [A-Za-z_][A-Za-z0-9_]{0,45}."
        )
    return table_name


def _coerce_positive_int_setting(name: str, value) -> int:
    if isinstance(value, bool):
        raise RateLimitConfigurationError(f"{name} must be an integer >= 1.")
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise RateLimitConfigurationError(f"{name} must be an integer >= 1.") from exc
    if int_value < 1:
        raise RateLimitConfigurationError(
            f"{name} must be >= 1 (got {int_value})."
        )
    return int_value


def parse_trusted_proxy_networks(
    trusted_proxies=None,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """
    Parse and validate ``CRM_TRUSTED_PROXY_IPS`` entries as IPv4/IPv6 host or CIDR networks.

    Raises ``RateLimitConfigurationError`` if any entry is malformed.
    """
    raw_proxies = (
        getattr(settings, "CRM_TRUSTED_PROXY_IPS", ())
        if trusted_proxies is None
        else trusted_proxies
    )
    if isinstance(raw_proxies, str):
        items = [part.strip() for part in raw_proxies.split(",") if part.strip()]
    elif isinstance(raw_proxies, (list, tuple, set, frozenset)):
        items = []
        for item in raw_proxies:
            if not isinstance(item, str) or not item.strip():
                raise RateLimitConfigurationError(
                    f"Invalid entry in CRM_TRUSTED_PROXY_IPS: {item!r}."
                )
            items.append(item.strip())
    else:
        raise RateLimitConfigurationError(
            "CRM_TRUSTED_PROXY_IPS must be a sequence or comma-separated string of IP addresses or CIDR networks."
        )

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in items:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise RateLimitConfigurationError(
                f"Invalid IP or CIDR in CRM_TRUSTED_PROXY_IPS: {entry!r}."
            ) from exc
    return tuple(networks)


def _ip_in_networks(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    return any(
        ip_obj.version == net.version and ip_obj in net for net in networks
    )


def resolve_client_ip(
    request,
    *,
    trust_xff: bool | None = None,
    trusted_proxies=None,
) -> str:
    """
    Resolve client IP safely with explicit trusted-proxy validation.

    Rules:
    1. Parse ``REMOTE_ADDR`` first; if missing or invalid, return ``"unknown"``.
    2. By default (``CRM_TRUST_X_FORWARDED_FOR=False``), ignore ``X-Forwarded-For``
       and return the validated ``REMOTE_ADDR``.
    3. Even when ``CRM_TRUST_X_FORWARDED_FOR=True``, ``X-Forwarded-For`` is ONLY
       consulted when ``CRM_TRUSTED_PROXY_IPS`` is non-empty AND ``REMOTE_ADDR``
       matches a configured trusted proxy IP/network.
    4. When ``REMOTE_ADDR`` is a trusted proxy, parse ``X-Forwarded-For`` hops from
       right to left (standard reverse-proxy append order) and select the rightmost
       valid non-trusted IP address so client-spoofed leftmost entries are ignored.
    5. If any inspected hop is empty or malformed, fail safely by returning
       the validated ``REMOTE_ADDR``.
    """
    raw_remote = request.META.get("REMOTE_ADDR")
    if not isinstance(raw_remote, str) or not raw_remote.strip():
        return "unknown"
    try:
        remote_ip = ipaddress.ip_address(raw_remote.strip())
    except ValueError:
        return "unknown"

    xff_enabled = (
        bool(getattr(settings, "CRM_TRUST_X_FORWARDED_FOR", False))
        if trust_xff is None
        else bool(trust_xff)
    )
    if not xff_enabled:
        return str(remote_ip)

    try:
        proxy_networks = parse_trusted_proxy_networks(trusted_proxies)
    except RateLimitConfigurationError:
        return str(remote_ip)

    if not proxy_networks or not _ip_in_networks(remote_ip, proxy_networks):
        return str(remote_ip)

    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if not isinstance(xff, str) or not xff.strip():
        return str(remote_ip)

    raw_hops = [hop.strip() for hop in xff.split(",")]
    last_trusted_hop = remote_ip
    for hop in reversed(raw_hops):
        if not hop:
            return str(remote_ip)
        try:
            hop_ip = ipaddress.ip_address(hop)
        except ValueError:
            return str(remote_ip)
        if not _ip_in_networks(hop_ip, proxy_networks):
            return str(hop_ip)
        last_trusted_hop = hop_ip

    return str(last_trusted_hop)


def validate_login_rate_limit_settings(
    *,
    max_attempts=None,
    max_ip_attempts=None,
    window_seconds=None,
    trusted_proxies=None,
) -> tuple[int, int, int]:
    """
    Validate that CRM login rate-limit thresholds and window duration are integers >= 1,
    and that any configured ``CRM_TRUSTED_PROXY_IPS`` entries are valid IPs/CIDRs.

    Raises ``RateLimitConfigurationError`` (an ``ImproperlyConfigured`` subclass)
    if any value is zero, negative, non-integer, or an invalid IP/CIDR.
    """
    resolved_max_attempts = _coerce_positive_int_setting(
        "CRM_LOGIN_MAX_ATTEMPTS",
        getattr(settings, "CRM_LOGIN_MAX_ATTEMPTS", 5)
        if max_attempts is None
        else max_attempts,
    )
    resolved_max_ip_attempts = _coerce_positive_int_setting(
        "CRM_LOGIN_MAX_IP_ATTEMPTS",
        getattr(settings, "CRM_LOGIN_MAX_IP_ATTEMPTS", resolved_max_attempts)
        if max_ip_attempts is None
        else max_ip_attempts,
    )
    resolved_window_seconds = _coerce_positive_int_setting(
        "CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        getattr(settings, "CRM_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 60)
        if window_seconds is None
        else window_seconds,
    )
    parse_trusted_proxy_networks(trusted_proxies)
    return (
        resolved_max_attempts,
        resolved_max_ip_attempts,
        resolved_window_seconds,
    )


def _is_missing_table_error(exc: DatabaseError, table_name: str) -> bool:
    cause = getattr(exc, "__cause__", None)
    sqlstate = getattr(cause, "sqlstate", None) or getattr(exc, "pgcode", None)
    if sqlstate == "42P01":
        return True
    msg = str(exc).lower()
    return "does not exist" in msg and table_name.lower() in msg


class PostgresAtomicCache(BaseCache):
    """
    Shared PostgreSQL-backed Django cache with single-statement atomic counters.

    Key properties:
    1. ``atomic_increment()`` and ``incr()`` perform single-statement SQL updates/upserts
       with ``RETURNING`` rather than a non-atomic ``get()`` followed by ``set()``.
    2. All expiry calculations and comparisons use PostgreSQL's ``STATEMENT_TIMESTAMP()``
       clock so workers never mix ``time.time()`` and ``timezone.now()`` or suffer from
       application-server clock drift.
    3. Incrementing an active counter preserves its existing expiration timestamp.
    4. ``prune_expired()`` deletes a bounded batch (default 128 rows) of expired entries
       using the ``_expires_idx`` index with ``FOR UPDATE SKIP LOCKED``.
    5. ``_ensure_table()`` serializes concurrent DDL initialization across workers using
       a PostgreSQL transaction-scoped advisory lock (``pg_advisory_xact_lock``).
    6. Database operational errors are propagated rather than silently swallowed so
       security-critical callers (such as ``LoginRateLimiter``) fail closed.
    """

    is_shared_backend = True
    pickle_protocol = pickle.HIGHEST_PROTOCOL
    MAX_TRACKED_EXPIRES_KEYS = MAX_TRACKED_EXPIRES_KEYS

    def __init__(self, table: str, params: dict):
        super().__init__(params)
        raw_table = "iqra_shared_cache" if table is None else table
        self._table = validate_table_identifier(raw_table)
        options = params.get("OPTIONS") or {}
        self._db_alias = options.get("DB_ALIAS", DEFAULT_DB_ALIAS)
        self._prune_batch_size = max(
            0, int(options.get("PRUNE_BATCH_SIZE", DEFAULT_PRUNE_BATCH_SIZE))
        )
        self._max_tracked_expires_keys = max(
            2,
            int(
                options.get(
                    "MAX_TRACKED_EXPIRES_KEYS", self.MAX_TRACKED_EXPIRES_KEYS
                )
            ),
        )
        self._thread_local = threading.local()

    def _get_last_expires_map(self) -> OrderedDict[str, datetime]:
        mapping = getattr(self._thread_local, "last_expires", None)
        if not isinstance(mapping, OrderedDict):
            mapping = OrderedDict()
            self._thread_local.last_expires = mapping
        return mapping

    def _record_last_expires(self, cache_key: str, expires_val: datetime) -> None:
        mapping = self._get_last_expires_map()
        mapping.pop(cache_key, None)
        mapping[cache_key] = expires_val
        while len(mapping) > self._max_tracked_expires_keys:
            mapping.popitem(last=False)

    def get_last_increment_expires(self, key: str, version=None) -> datetime | None:
        cache_key = self._normalize_key(key, version=version)
        return self._get_last_expires_map().get(cache_key)

    def pop_last_increment_expires(self, key: str, version=None) -> datetime | None:
        cache_key = self._normalize_key(key, version=version)
        return self._get_last_expires_map().pop(cache_key, None)

    def _normalize_key(self, key: str, version=None) -> str:
        validated = self.make_and_validate_key(key, version=version)
        if len(validated) > 240:
            digest = hashlib.sha256(validated.encode("utf-8")).hexdigest()
            return f"{self.key_prefix}:v{version or self.version}:sha256:{digest}"
        return validated

    @staticmethod
    def _advisory_lock_id(table_name: str) -> int:
        digest = hashlib.sha256(
            f"iqra_tcs.PostgresAtomicCache:{table_name}".encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    @staticmethod
    def _relation_exists(cursor, connection, relation_name: str) -> bool:
        if connection.vendor == "postgresql":
            cursor.execute("SELECT to_regclass(%s)", [relation_name])
            row = cursor.fetchone()
            return bool(row and row[0] is not None)
        return False

    def _ensure_table(self, db: str | None = None) -> None:
        table_name = validate_table_identifier(self._table)
        index_name = f"{table_name}_expires_idx"
        alias = db or self._db_alias
        connection = connections[alias]
        quote_name = connection.ops.quote_name
        quoted_table = quote_name(table_name)
        quoted_index = quote_name(index_name)

        with transaction.atomic(using=alias), connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [self._advisory_lock_id(table_name)],
                )

            if not self._relation_exists(cursor, connection, table_name):
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {quoted_table} (
                        "cache_key" VARCHAR(255) PRIMARY KEY,
                        "value" TEXT NOT NULL,
                        "int_value" BIGINT NULL,
                        "expires" TIMESTAMPTZ NOT NULL
                    )
                    """
                )

            if not self._relation_exists(cursor, connection, index_name):
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS {quoted_index}
                    ON {quoted_table} ("expires")
                    """
                )

    def run_in_transaction(self, callback):
        """
        Execute ``callback(db_alias)`` inside a single PostgreSQL atomic transaction
        on this cache backend's configured database alias.

        If any nested operation raises an exception or marks the transaction for
        rollback via ``transaction.set_rollback(True, using=db_alias)``, all updates
        performed within ``callback`` are rolled back together.
        """
        table_name = validate_table_identifier(self._table)
        alias = self._db_alias
        prev_in_paired = getattr(self._thread_local, "in_paired_tx", False)
        self._thread_local.in_paired_tx = True
        try:
            try:
                with transaction.atomic(using=alias):
                    return callback(alias)
            except DatabaseError as exc:
                if prev_in_paired or not _is_missing_table_error(exc, table_name):
                    raise
                self._ensure_table(alias)
                with transaction.atomic(using=alias):
                    return callback(alias)
        finally:
            self._thread_local.in_paired_tx = prev_in_paired

    def _execute(self, operation):
        table_name = validate_table_identifier(self._table)
        alias = self._db_alias
        connection = connections[alias]
        quoted_table = connection.ops.quote_name(table_name)
        if getattr(self._thread_local, "in_paired_tx", False):
            with transaction.atomic(using=alias), connection.cursor() as cursor:
                return operation(connection, cursor, quoted_table)
        try:
            with transaction.atomic(using=alias), connection.cursor() as cursor:
                return operation(connection, cursor, quoted_table)
        except DatabaseError as exc:
            if not _is_missing_table_error(exc, table_name):
                raise
            self._ensure_table(alias)
            with transaction.atomic(using=alias), connection.cursor() as cursor:
                return operation(connection, cursor, quoted_table)

    def _normalize_timeout_seconds(self, timeout=DEFAULT_TIMEOUT) -> float | None:
        if timeout is DEFAULT_TIMEOUT:
            timeout = self.default_timeout
        if timeout is None:
            return None
        return float(timeout)

    @staticmethod
    def _expires_sql_expr(timeout_seconds: float | None) -> tuple[str, list]:
        if timeout_seconds is None:
            return "TIMESTAMPTZ '9999-12-31 23:59:59+00'", []
        return "STATEMENT_TIMESTAMP() + (%s * INTERVAL '1 second')", [timeout_seconds]

    def _encode_value(self, value) -> tuple[str, int | None]:
        pickled = pickle.dumps(value, self.pickle_protocol)
        b64 = base64.b64encode(pickled).decode("ascii")
        int_val = (
            int(value)
            if isinstance(value, int) and not isinstance(value, bool)
            else None
        )
        return b64, int_val

    def _decode_value(self, b64_value: str, int_value: int | None):
        if int_value is not None:
            return int(int_value)
        return pickle.loads(base64.b64decode(b64_value.encode("ascii")))

    @staticmethod
    def _prune_expired_cursor(
        cursor, quoted_table: str, batch_size: int = DEFAULT_PRUNE_BATCH_SIZE
    ) -> int:
        limit = max(0, int(batch_size))
        if limit == 0:
            return 0
        cursor.execute(
            f"""
            WITH expired_batch AS MATERIALIZED (
                SELECT "cache_key"
                FROM {quoted_table}
                WHERE "expires" <= STATEMENT_TIMESTAMP()
                ORDER BY "expires" ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            DELETE FROM {quoted_table}
            USING expired_batch
            WHERE {quoted_table}."cache_key" = expired_batch."cache_key"
            """,
            [limit],
        )
        return int(cursor.rowcount or 0)

    def prune_expired(self, batch_size: int | None = None) -> int:
        """
        Delete up to ``batch_size`` expired cache entries using the expiry index.

        Never touches unexpired rows and uses ``FOR UPDATE SKIP LOCKED`` so
        concurrent workers pruning simultaneously do not block each other.
        """
        effective_batch = (
            self._prune_batch_size if batch_size is None else int(batch_size)
        )

        def _op(connection, cursor, quoted_table):
            return self._prune_expired_cursor(cursor, quoted_table, effective_batch)

        return int(self._execute(_op))

    def get(self, key, default=None, version=None):
        return self.get_many([key], version=version).get(key, default)

    def get_many(self, keys, version=None):
        keys = list(keys)
        if not keys:
            return {}
        key_map = {
            self._normalize_key(key, version=version): key for key in keys
        }

        def _op(connection, cursor, quoted_table):
            placeholders = ", ".join(["%s"] * len(key_map))
            cursor.execute(
                f"""
                SELECT
                    "cache_key",
                    "value",
                    "int_value",
                    ("expires" <= STATEMENT_TIMESTAMP()) AS is_expired
                FROM {quoted_table}
                WHERE "cache_key" IN ({placeholders})
                """,
                list(key_map.keys()),
            )
            rows = cursor.fetchall()
            active = {}
            expired_keys = []
            for cache_key, b64_val, int_val, is_expired in rows:
                if is_expired:
                    expired_keys.append(cache_key)
                else:
                    orig_key = key_map.get(cache_key)
                    if orig_key is not None:
                        active[orig_key] = self._decode_value(b64_val, int_val)
            if expired_keys:
                del_placeholders = ", ".join(["%s"] * len(expired_keys))
                cursor.execute(
                    f'DELETE FROM {quoted_table} WHERE "cache_key" IN ({del_placeholders})',
                    expired_keys,
                )
            return active

        return self._execute(_op)

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None) -> None:
        cache_key = self._normalize_key(key, version=version)
        timeout_seconds = self._normalize_timeout_seconds(timeout)
        exp_sql, exp_params = self._expires_sql_expr(timeout_seconds)
        b64_val, int_val = self._encode_value(value)

        def _op(connection, cursor, quoted_table):
            cursor.execute(
                f"""
                INSERT INTO {quoted_table} ("cache_key", "value", "int_value", "expires")
                VALUES (%s, %s, %s, {exp_sql})
                ON CONFLICT ("cache_key") DO UPDATE
                SET
                    "value" = EXCLUDED."value",
                    "int_value" = EXCLUDED."int_value",
                    "expires" = EXCLUDED."expires"
                """,
                [cache_key, b64_val, int_val, *exp_params],
            )

        self._execute(_op)

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None) -> bool:
        cache_key = self._normalize_key(key, version=version)
        timeout_seconds = self._normalize_timeout_seconds(timeout)
        exp_sql, exp_params = self._expires_sql_expr(timeout_seconds)
        b64_val, int_val = self._encode_value(value)

        def _op(connection, cursor, quoted_table):
            cursor.execute(
                f"""
                INSERT INTO {quoted_table} ("cache_key", "value", "int_value", "expires")
                VALUES (%s, %s, %s, {exp_sql})
                ON CONFLICT ("cache_key") DO UPDATE
                SET
                    "value" = EXCLUDED."value",
                    "int_value" = EXCLUDED."int_value",
                    "expires" = EXCLUDED."expires"
                WHERE {quoted_table}."expires" <= STATEMENT_TIMESTAMP()
                RETURNING ("expires" > STATEMENT_TIMESTAMP())
                """,
                [cache_key, b64_val, int_val, *exp_params],
            )
            row = cursor.fetchone()
            return bool(row and row[0])

        return bool(self._execute(_op))

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None) -> bool:
        cache_key = self._normalize_key(key, version=version)
        timeout_seconds = self._normalize_timeout_seconds(timeout)
        exp_sql, exp_params = self._expires_sql_expr(timeout_seconds)

        def _op(connection, cursor, quoted_table):
            cursor.execute(
                f"""
                UPDATE {quoted_table}
                SET "expires" = {exp_sql}
                WHERE "cache_key" = %s AND "expires" > STATEMENT_TIMESTAMP()
                RETURNING "cache_key"
                """,
                [*exp_params, cache_key],
            )
            return cursor.fetchone() is not None

        return bool(self._execute(_op))

    def incr(self, key, delta=1, version=None) -> int:
        cache_key = self._normalize_key(key, version=version)
        delta_int = int(delta)

        def _op(connection, cursor, quoted_table):
            cursor.execute(
                f"""
                UPDATE {quoted_table}
                SET "int_value" = "int_value" + %s
                WHERE "cache_key" = %s
                  AND "expires" > STATEMENT_TIMESTAMP()
                  AND "int_value" IS NOT NULL
                RETURNING "int_value"
                """,
                [delta_int, cache_key],
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Key '{key}' not found")
            return int(row[0])

        return self._execute(_op)

    def decr(self, key, delta=1, version=None) -> int:
        return self.incr(key, -int(delta), version=version)

    def atomic_increment_with_expiry(
        self,
        key: str,
        delta: int = 1,
        timeout=DEFAULT_TIMEOUT,
        version=None,
    ) -> tuple[int, datetime]:
        """
        Atomically initialize or increment an integer counter and return
        ``(new_int_value, expires_timestamp)`` directly from PostgreSQL ``RETURNING``.
        """
        cache_key = self._normalize_key(key, version=version)
        timeout_seconds = self._normalize_timeout_seconds(timeout)
        exp_sql, exp_params = self._expires_sql_expr(timeout_seconds)
        delta_int = int(delta)
        b64_val, _ = self._encode_value(delta_int)

        def _op(connection, cursor, quoted_table):
            cursor.execute(
                f"""
                INSERT INTO {quoted_table} ("cache_key", "value", "int_value", "expires")
                VALUES (%s, %s, %s, {exp_sql})
                ON CONFLICT ("cache_key") DO UPDATE
                SET
                    "int_value" = CASE
                        WHEN {quoted_table}."expires" <= STATEMENT_TIMESTAMP() THEN EXCLUDED."int_value"
                        ELSE COALESCE({quoted_table}."int_value", 0) + %s
                    END,
                    "value" = EXCLUDED."value",
                    "expires" = CASE
                        WHEN {quoted_table}."expires" <= STATEMENT_TIMESTAMP() THEN EXCLUDED."expires"
                        ELSE {quoted_table}."expires"
                    END
                RETURNING "int_value", "expires"
                """,
                [cache_key, b64_val, delta_int, *exp_params, delta_int],
            )
            row = cursor.fetchone()
            int_val = int(row[0])
            expires_val = row[1]
            if (
                self._prune_batch_size > 0
                and (timeout_seconds is None or timeout_seconds > 0)
            ):
                self._prune_expired_cursor(
                    cursor, quoted_table, self._prune_batch_size
                )
            return int_val, expires_val

        int_val, expires_val = self._execute(_op)
        self._record_last_expires(cache_key, expires_val)
        return int_val, expires_val

    def atomic_increment(
        self,
        key: str,
        delta: int = 1,
        timeout=DEFAULT_TIMEOUT,
        version=None,
    ) -> int:
        """
        Atomically initialize or increment an integer counter in a single SQL statement.

        - If the key does not exist or has expired (``expires <= STATEMENT_TIMESTAMP()``),
          sets ``int_value = delta`` and initializes
          ``expires = STATEMENT_TIMESTAMP() + timeout``.
        - If the key is active, increments ``int_value`` by ``delta`` while preserving
          the existing ``expires`` window timestamp.
        - Opportunistically prunes a bounded batch of expired rows (up to 128) using
          the expiry index.
        """
        int_val, _ = self.atomic_increment_with_expiry(
            key, delta=delta, timeout=timeout, version=version
        )
        return int_val

    def atomic_release_slot(
        self,
        key: str,
        expected_expires: datetime | None,
        delta: int = 1,
        version=None,
    ) -> int:
        """
        Safely release a single admission reservation from ``key`` only if the exact
        rate-limit window ``expected_expires`` is still active.

        Never decrements below 0 and never modifies a subsequent window if the
        original window expired while password verification was in flight.
        Returns the post-release active integer count for ``key``.
        """
        cache_key = self._normalize_key(key, version=version)
        delta_int = max(1, int(delta))

        def _op(connection, cursor, quoted_table):
            if expected_expires is not None:
                cursor.execute(
                    f"""
                    UPDATE {quoted_table}
                    SET "int_value" = GREATEST(COALESCE("int_value", 0) - %s, 0)
                    WHERE "cache_key" = %s
                      AND "expires" = %s
                      AND "expires" > STATEMENT_TIMESTAMP()
                    RETURNING "int_value"
                    """,
                    [delta_int, cache_key, expected_expires],
                )
                row = cursor.fetchone()
                if row is not None:
                    return int(row[0])

            cursor.execute(
                f"""
                SELECT "int_value"
                FROM {quoted_table}
                WHERE "cache_key" = %s
                  AND "expires" > STATEMENT_TIMESTAMP()
                """,
                [cache_key],
            )
            active_row = cursor.fetchone()
            if active_row is None or active_row[0] is None:
                return 0
            return int(active_row[0])

        return int(self._execute(_op))

    def delete(self, key, version=None) -> bool:
        cache_key = self._normalize_key(key, version=version)
        return self._delete_raw_keys([cache_key])

    def delete_many(self, keys, version=None) -> None:
        raw_keys = [
            self._normalize_key(key, version=version) for key in keys
        ]
        if raw_keys:
            self._delete_raw_keys(raw_keys)

    def _delete_raw_keys(self, raw_keys: list[str]) -> bool:
        if not raw_keys:
            return False

        def _op(connection, cursor, quoted_table):
            placeholders = ", ".join(["%s"] * len(raw_keys))
            cursor.execute(
                f'DELETE FROM {quoted_table} WHERE "cache_key" IN ({placeholders})',
                raw_keys,
            )
            return cursor.rowcount > 0

        return bool(self._execute(_op))

    def has_key(self, key, version=None) -> bool:
        cache_key = self._normalize_key(key, version=version)

        def _op(connection, cursor, quoted_table):
            cursor.execute(
                f"""
                SELECT 1
                FROM {quoted_table}
                WHERE "cache_key" = %s AND "expires" > STATEMENT_TIMESTAMP()
                """,
                [cache_key],
            )
            return cursor.fetchone() is not None

        return bool(self._execute(_op))

    def clear(self) -> None:
        def _op(connection, cursor, quoted_table):
            cursor.execute(f"DELETE FROM {quoted_table}")

        self._execute(_op)


def ensure_shared_cache_table(**kwargs) -> None:
    """Ensure the shared PostgreSQL cache table exists after ``migrate``."""
    for alias in getattr(settings, "CACHES", {}):
        cache_backend = caches[alias]
        if isinstance(cache_backend, PostgresAtomicCache):
            cache_backend._ensure_table()


@dataclass(frozen=True)
class AdmissionReservation:
    """Represents a pre-authentication login rate-limit admission decision."""

    client_ip: str
    normalized_username: str
    admitted: bool
    user_ip_count: int
    ip_count: int
    user_ip_expires: datetime | None = None
    ip_expires: datetime | None = None


class LoginRateLimiter:
    """
    Production login rate limiter backed by shared atomic PostgreSQL cache storage.

    Enforces:
    - ``max_attempts`` failed attempts per normalized ``(username, client_ip)`` within
      ``window_seconds``.
    - ``max_ip_attempts`` failed attempts per ``client_ip`` across all usernames within
      ``window_seconds``.
    - Pre-authentication atomic admission via ``admit_attempt()``, which increments
      counters in a short committed transaction before PBKDF2 runs and rejects excess
      requests with ``admitted=False`` (HTTP 429) before password verification.
    - Safe post-authentication reconciliation via ``reconcile_successful_attempt()``,
      which releases only the single ``+1`` window-bound reservation acquired by a
      successful login without erasing concurrent or prior failed attempts on either
      ``(client_ip, username)`` or ``client_ip``.
    - Fails closed with ``RateLimitStorageUnavailableError`` if configuration values
      are invalid, if the configured backend is not an explicitly supported shared
      atomic backend, or if the storage backend raises an operational error.
    """

    def __init__(
        self,
        *,
        cache_backend=None,
        cache_alias: str | None = None,
        max_attempts: int | None = None,
        max_ip_attempts: int | None = None,
        window_seconds: int | None = None,
    ):
        self._cache_backend = cache_backend
        self._cache_alias = cache_alias
        self._max_attempts = max_attempts
        self._max_ip_attempts = max_ip_attempts
        self._window_seconds = window_seconds
        self.validate_configuration()

    def validate_configuration(self) -> tuple[int, int, int]:
        return validate_login_rate_limit_settings(
            max_attempts=self._max_attempts,
            max_ip_attempts=self._max_ip_attempts,
            window_seconds=self._window_seconds,
        )

    @property
    def max_attempts(self) -> int:
        return self.validate_configuration()[0]

    @property
    def max_ip_attempts(self) -> int:
        return self.validate_configuration()[1]

    @property
    def window_seconds(self) -> int:
        return self.validate_configuration()[2]

    def _get_backend(self):
        self.validate_configuration()
        try:
            if self._cache_backend is not None:
                backend = self._cache_backend
            else:
                alias = self._cache_alias or getattr(
                    settings, "CRM_LOGIN_RATELIMIT_CACHE_ALIAS", "crm_ratelimit"
                )
                backend = caches[alias]
        except RateLimitStorageUnavailableError:
            raise
        except Exception as exc:
            logger.error(
                "CRM login rate-limit cache initialization failed (%s).",
                exc.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate-limit storage could not be initialized."
            ) from exc

        if (
            isinstance(backend, (LocMemCache, DummyCache, RedisCache))
            or getattr(backend, "is_shared_backend", None) is not True
        ):
            logger.error(
                "CRM login rate limiter rejected non-shared or unsupported cache backend (%s).",
                backend.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate limiter requires an explicitly supported shared cache backend."
            )

        atomic_increment_fn = getattr(backend, "atomic_increment", None)
        if not callable(atomic_increment_fn):
            logger.error(
                "CRM login rate limiter rejected cache backend without atomic_increment (%s).",
                backend.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate limiter requires a callable atomic_increment method."
            )

        return backend

    @staticmethod
    def build_keys(client_ip: str, raw_username: str) -> tuple[str, str]:
        normalized_username = normalize_login_username(raw_username)
        user_ip_digest = hashlib.sha256(
            f"{client_ip}:{normalized_username}".encode("utf-8")
        ).hexdigest()
        ip_digest = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()
        return (
            f"webcrm:login:user_ip:{user_ip_digest}",
            f"webcrm:login:ip:{ip_digest}",
        )

    def get_counts(self, client_ip: str, raw_username: str) -> tuple[int, int]:
        backend = self._get_backend()
        user_ip_key, ip_key = self.build_keys(client_ip, raw_username)
        try:
            values = backend.get_many([user_ip_key, ip_key])
            return (
                int(values.get(user_ip_key, 0) or 0),
                int(values.get(ip_key, 0) or 0),
            )
        except RateLimitStorageUnavailableError:
            raise
        except Exception as exc:
            logger.error(
                "CRM login rate-limit check failed due to storage error (%s).",
                exc.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate-limit storage unavailable."
            ) from exc

    def is_rate_limited(self, client_ip: str, raw_username: str) -> bool:
        max_user, max_ip, _ = self.validate_configuration()
        user_ip_count, ip_count = self.get_counts(client_ip, raw_username)
        return user_ip_count >= max_user or ip_count >= max_ip

    def admit_attempt(
        self, client_ip: str, raw_username: str
    ) -> AdmissionReservation:
        """
        Atomically reserve an admission slot before password verification (PBKDF2).

        - Updates ``(client_ip, normalized_username)`` (first) and ``client_ip`` (second)
          inside a single PostgreSQL transaction using a consistent lock order.
        - If either counter would exceed its configured threshold (``> max_attempts`` or
          ``> max_ip_attempts``), marks the transaction for rollback so neither counter
          retains an increment from the rejected request, and returns
          ``AdmissionReservation(admitted=False, ...)`` so the caller immediately
          returns HTTP 429 before running PBKDF2.
        - If either counter update raises an error, the enclosing transaction rolls back
          both updates and raises ``RateLimitStorageUnavailableError``.
        """
        max_user, max_ip, window = self.validate_configuration()
        backend = self._get_backend()
        normalized_username = normalize_login_username(raw_username)
        user_ip_key, ip_key = self.build_keys(client_ip, normalized_username)

        def _admit_tx(db_alias: str | None = None) -> AdmissionReservation:
            user_ip_count = int(
                backend.atomic_increment(user_ip_key, delta=1, timeout=window)
            )
            ip_count = int(
                backend.atomic_increment(ip_key, delta=1, timeout=window)
            )
            pop_exp = getattr(backend, "pop_last_increment_expires", None)
            get_exp = getattr(backend, "get_last_increment_expires", None)
            if callable(pop_exp):
                user_ip_expires = pop_exp(user_ip_key)
                ip_expires = pop_exp(ip_key)
            elif callable(get_exp):
                user_ip_expires = get_exp(user_ip_key)
                ip_expires = get_exp(ip_key)
            else:
                user_ip_expires = None
                ip_expires = None

            admitted = user_ip_count <= max_user and ip_count <= max_ip
            if not admitted:
                if db_alias is not None:
                    transaction.set_rollback(True, using=db_alias)
                else:
                    transaction.set_rollback(True)
                return AdmissionReservation(
                    client_ip=client_ip,
                    normalized_username=normalized_username,
                    admitted=False,
                    user_ip_count=max(0, user_ip_count - 1),
                    ip_count=max(0, ip_count - 1),
                    user_ip_expires=None,
                    ip_expires=None,
                )

            return AdmissionReservation(
                client_ip=client_ip,
                normalized_username=normalized_username,
                admitted=True,
                user_ip_count=user_ip_count,
                ip_count=ip_count,
                user_ip_expires=user_ip_expires,
                ip_expires=ip_expires,
            )

        try:
            run_tx = getattr(backend, "run_in_transaction", None)
            if callable(run_tx):
                return run_tx(_admit_tx)
            with transaction.atomic(using=DEFAULT_DB_ALIAS):
                return _admit_tx(DEFAULT_DB_ALIAS)
        except RateLimitStorageUnavailableError:
            raise
        except Exception as exc:
            logger.error(
                "CRM login rate-limit admission failed due to storage error (%s).",
                exc.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate-limit storage unavailable."
            ) from exc

    def reconcile_successful_attempt(
        self, reservation: AdmissionReservation
    ) -> bool:
        """
        Reconcile a pre-authentication reservation after credentials are verified valid.

        Releases both ``user_ip_key`` and ``ip_key`` reservations in a single
        PostgreSQL transaction (using the same consistent lock order as admission)
        within their specific rate-limit windows, without erasing concurrent or
        prior failed attempts. If either release fails, both roll back.

        Returns ``True`` if the login remains within rate-limit thresholds after
        releasing its own reservation, or ``False`` if concurrent failed attempts
        saturated the threshold while password verification was in flight.
        """
        if not reservation.admitted:
            return False

        max_user, max_ip, _ = self.validate_configuration()
        backend = self._get_backend()
        user_ip_key, ip_key = self.build_keys(
            reservation.client_ip, reservation.normalized_username
        )

        def _reconcile_tx(_db_alias: str | None = None) -> tuple[int, int]:
            release_fn = getattr(backend, "atomic_release_slot", None)
            if callable(release_fn):
                rem_user_ip = int(
                    release_fn(
                        user_ip_key,
                        reservation.user_ip_expires,
                        delta=1,
                    )
                )
                rem_ip = int(
                    release_fn(
                        ip_key,
                        reservation.ip_expires,
                        delta=1,
                    )
                )
                return rem_user_ip, rem_ip
            return self.get_counts(
                reservation.client_ip, reservation.normalized_username
            )

        try:
            run_tx = getattr(backend, "run_in_transaction", None)
            if callable(run_tx):
                remaining_user_ip, remaining_ip = run_tx(_reconcile_tx)
            else:
                with transaction.atomic(using=DEFAULT_DB_ALIAS):
                    remaining_user_ip, remaining_ip = _reconcile_tx(
                        DEFAULT_DB_ALIAS
                    )
        except RateLimitStorageUnavailableError:
            raise
        except Exception as exc:
            logger.error(
                "CRM login rate-limit reconciliation failed due to storage error (%s).",
                exc.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate-limit storage unavailable."
            ) from exc

        return remaining_user_ip < max_user and remaining_ip < max_ip

    def record_failed_attempt(
        self, client_ip: str, raw_username: str
    ) -> tuple[int, int]:
        reservation = self.admit_attempt(client_ip, raw_username)
        return reservation.user_ip_count, reservation.ip_count

    def is_attempt_count_over_limit(
        self, user_ip_count: int, ip_count: int
    ) -> bool:
        max_user, max_ip, _ = self.validate_configuration()
        return user_ip_count > max_user or ip_count > max_ip

    def clear_attempts(self, client_ip: str, raw_username: str) -> None:
        """
        Delete only the ``(client_ip, normalized_username)`` counter.

        Preserves the IP-wide failure counter (``ip_key``) so failures accumulated
        across usernames from ``client_ip`` cannot be reset by signing into a known
        account and instead expire naturally at the end of the rate-limit window.
        """
        backend = self._get_backend()
        user_ip_key, _ = self.build_keys(client_ip, raw_username)
        try:
            backend.delete(user_ip_key)
        except RateLimitStorageUnavailableError:
            raise
        except Exception as exc:
            logger.error(
                "CRM login rate-limit clear failed due to storage error (%s).",
                exc.__class__.__name__,
            )
            raise RateLimitStorageUnavailableError(
                "Login rate-limit storage unavailable."
            ) from exc
