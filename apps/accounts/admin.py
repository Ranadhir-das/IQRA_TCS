"""IQRA TCS Calling CRM — Accounts Admin Registration."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Django admin registration for the IQRA TCS User model.

    Note: Django admin is a maintenance/superuser interface only.
    It is NOT the IQRA web CRM interface (which is a separate Phase deliverable).
    """

    list_display = ("username", "email", "get_full_name", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)

    # Extend the default fieldsets with IQRA-specific fields
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "IQRA TCS",
            {
                "fields": ("role", "phone", "designation"),
            },
        ),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "IQRA TCS",
            {
                "fields": ("role", "phone", "designation", "first_name", "last_name", "email"),
            },
        ),
    )

    readonly_fields = ("created_at", "updated_at")
