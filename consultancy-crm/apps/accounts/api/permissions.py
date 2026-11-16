"""
IQRA TCS Calling CRM — Role-based Permission Classes

IsAdminRole:   Grants access to ADMIN users and Django superusers.
               Always queries is_active; never relies solely on token claims.

IsEmployeeRole: Grants access to EMPLOYEE users only.
                ADMIN users are explicitly excluded from employee-only paths.
"""

from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class IsAdminRole(BasePermission):
    """
    Allow access only to active users with the ADMIN role, or Django superusers.

    Usage:
        permission_classes = [IsAuthenticated, IsAdminRole]
    """

    message = "Admin access required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and (
                request.user.role == User.Role.ADMIN
                or request.user.is_superuser
            )
        )


class IsEmployeeRole(BasePermission):
    """
    Allow access only to active, non-superuser users with the EMPLOYEE role.

    Usage:
        permission_classes = [IsAuthenticated, IsEmployeeRole]
    """

    message = "Employee access required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.role == User.Role.EMPLOYEE
            and not request.user.is_superuser
        )


class IsActiveAuthenticatedUser(BasePermission):
    """Allow access only to active authenticated users."""

    message = "An active account is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
        )
