from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class IsActiveFollowUpEmployee(BasePermission):
    """Allow active, ordinary employee accounts to create, reschedule, and complete follow-ups."""

    message = "Follow-up actions require an active employee account."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.role == User.Role.EMPLOYEE
            and not user.is_superuser
        )


class IsActiveFollowUpUser(BasePermission):
    """Allow active ordinary employees and active administrators (including superusers)."""

    message = "An active employee or administrator account is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (
                user.is_admin
                or (user.role == User.Role.EMPLOYEE and not user.is_superuser)
            )
        )


class IsActiveFollowUpAdmin(BasePermission):
    """Allow active administrators, including Django superusers."""

    message = "An active administrator account is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_admin)
