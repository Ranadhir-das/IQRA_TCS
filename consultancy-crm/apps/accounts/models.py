"""
IQRA TCS Calling CRM — User Model

Roles: ADMIN and EMPLOYEE only.
No gamification, points, attendance, location tracking, or multi-tenant logic.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom User for IQRA TCS Calling CRM.

    Two business roles:
      - ADMIN:    Organisation-wide access. Manages employees, leads and reports.
      - EMPLOYEE: Sees only their own assigned leads and calls.

    Django superuser (is_superuser=True) is treated as ADMIN for all business-logic
    checks but should only be used for emergency access — not day-to-day operations.
    """

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        EMPLOYEE = "EMPLOYEE", "Employee"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.EMPLOYEE,
        db_index=True,
    )

    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Employee contact number (optional).",
    )

    designation = models.CharField(
        max_length=100,
        blank=True,
        help_text="Job title or designation (optional).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Convenience properties ──────────────────────────────────────────────

    @property
    def is_admin(self) -> bool:
        """True for ADMIN role users and Django superusers."""
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_employee(self) -> bool:
        """True only for non-superuser EMPLOYEE role users."""
        return self.role == self.Role.EMPLOYEE and not self.is_superuser

    def __str__(self) -> str:
        name = self.get_full_name()
        label = name if name else self.username
        return f"{label} ({self.get_role_display()})"

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["username"]
