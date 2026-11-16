"""Session authentication and active-administrator authorization for Web CRM views."""

from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import User


def is_authorized_crm_admin(user) -> bool:
    """Return True only for authenticated, active ADMIN users or active superusers."""
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and (user.role == User.Role.ADMIN or user.is_superuser)
    )


def crm_admin_required(view_func):
    """Require an active session-authenticated ADMIN or superuser.

    - Unauthenticated users are redirected to /crm/login/?next=<path>.
    - Inactive authenticated users have their session cleared and are redirected
      to /crm/login/ (or receive 403 on non-GET requests).
    - Active ordinary EMPLOYEE users receive 403 Forbidden.
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            login_url = reverse("webcrm:login")
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{login_url}?{query}")

        # Re-read fresh user state from DB so deactivated users lose access immediately
        fresh_user = User.objects.filter(pk=user.pk).first()
        if fresh_user is None or not fresh_user.is_active:
            if hasattr(request, "session"):
                logout(request)
            raise PermissionDenied("An active administrator account is required.")

        request.user = fresh_user
        if not is_authorized_crm_admin(fresh_user):
            raise PermissionDenied("Administrator access is required.")

        return view_func(request, *args, **kwargs)

    return _wrapped_view


def require_POST_only(view_func):
    """Allow only HTTP POST requests (returning 405 Method Not Allowed otherwise)."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        return view_func(request, *args, **kwargs)

    return _wrapped


def require_GET_or_HEAD(view_func):
    """Allow only HTTP GET/HEAD requests (returning 405 Method Not Allowed otherwise)."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.method not in ("GET", "HEAD"):
            return HttpResponseNotAllowed(["GET", "HEAD"])
        return view_func(request, *args, **kwargs)

    return _wrapped
