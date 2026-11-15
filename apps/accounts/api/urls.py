"""IQRA TCS Calling CRM — Accounts API URL Configuration."""

from django.urls import path

from .views import (
    ChangePasswordView,
    CustomTokenRefreshView,
    EmployeeDetailView,
    EmployeeListCreateView,
    EmployeePasswordResetView,
    LoginView,
    LogoutView,
    MeView,
)

urlpatterns = [
    # ── Authentication ─────────────────────────────────────────────────────
    path("login/", LoginView.as_view(), name="auth-login"),
    path("refresh/", CustomTokenRefreshView.as_view(), name="auth-refresh"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="auth-me"),
    path("change-password/", ChangePasswordView.as_view(), name="auth-change-password"),

    # ── Employee Management (Admin only) ───────────────────────────────────
    path("employees/", EmployeeListCreateView.as_view(), name="employee-list-create"),
    path("employees/<int:pk>/", EmployeeDetailView.as_view(), name="employee-detail"),
    path(
        "employees/<int:pk>/reset-password/",
        EmployeePasswordResetView.as_view(),
        name="employee-reset-password",
    ),
]
