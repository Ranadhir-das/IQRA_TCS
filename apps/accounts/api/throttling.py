"""
IQRA TCS Calling CRM — Login Rate Throttle

Applies a 5-per-minute throttle to the login endpoint, keyed by client IP.
Defined as a custom scope ('login') configured in REST_FRAMEWORK.DEFAULT_THROTTLE_RATES.
"""

from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """
    Throttle unauthenticated login attempts to 5 per minute per IP address.
    Configured via REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"] in settings.
    """

    scope = "login"
