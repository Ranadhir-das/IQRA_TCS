from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class IsAdminOrEmployeeRole(BasePermission):
    """Permit active IQRA admins and employees to read lead resources."""

    message = "Lead access requires an active admin or employee account."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_admin or user.role == User.Role.EMPLOYEE)
        )
