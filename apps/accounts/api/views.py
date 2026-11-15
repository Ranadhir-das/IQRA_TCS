"""
IQRA TCS Calling CRM — Auth & Employee Management Views

Endpoints:
    POST /api/v1/auth/login/                           — LoginView
    POST /api/v1/auth/refresh/                         — CustomTokenRefreshView (checks is_active)
    POST /api/v1/auth/logout/                          — LogoutView (blacklists refresh token)
    GET  /api/v1/auth/me/                              — MeView (current authenticated user)
    POST /api/v1/auth/change-password/                 — ChangePasswordView (self-service)

    GET  /api/v1/auth/employees/                       — EmployeeListCreateView (admin only)
    POST /api/v1/auth/employees/                       — EmployeeListCreateView (admin only)
    GET  /api/v1/auth/employees/<pk>/                  — EmployeeDetailView (admin only)
    PATCH /api/v1/auth/employees/<pk>/                 — EmployeeDetailView (admin only)
    DELETE /api/v1/auth/employees/<pk>/                — EmployeeDetailView (soft-deactivate, admin only)
    POST /api/v1/auth/employees/<pk>/reset-password/   — EmployeePasswordResetView (admin only)

Token lifecycle:
    - Access token:  30 min.  Sent as Authorization: Bearer <token> on every request.
    - Refresh token: 7 days.  Used ONLY on /refresh/. Rotated and blacklisted each use.
    - Logout:        Blacklists current refresh token; active access tokens expire naturally.
    - Password change/reset: Blacklists all outstanding refresh tokens for the target user.
"""

import logging

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.models import User
from apps.followups.models import FollowUp
from apps.leads.models import Lead

from .permissions import IsActiveAuthenticatedUser, IsAdminRole
from .serializers import (
    ActiveUserTokenRefreshSerializer,
    AdminEmployeePasswordResetSerializer,
    ChangePasswordSerializer,
    EmployeeCreateSerializer,
    EmployeeDetailSerializer,
    EmployeeListSerializer,
    EmployeeUpdateSerializer,
    LoginSerializer,
    UserProfileSerializer,
)
from .throttling import LoginRateThrottle

logger = logging.getLogger("apps.accounts")


def revoke_user_refresh_tokens(user: User) -> int:
    """Blacklist all outstanding refresh tokens belonging to ``user``."""
    outstanding = list(OutstandingToken.objects.filter(user=user))
    if not outstanding:
        return 0
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in outstanding],
        ignore_conflicts=True,
    )
    return len(outstanding)


def _employee_queryset():
    """Return non-superuser EMPLOYEE users annotated with workload counts."""
    active_leads_subquery = (
        Lead.objects.filter(assigned_to=OuterRef("pk"), is_archived=False)
        .order_by()
        .values("assigned_to")
        .annotate(count=Count("pk"))
        .values("count")
    )
    pending_followups_subquery = (
        FollowUp.objects.filter(owner=OuterRef("pk"), status=FollowUp.Status.PENDING)
        .order_by()
        .values("owner")
        .annotate(count=Count("pk"))
        .values("count")
    )
    return (
        User.objects.filter(role=User.Role.EMPLOYEE, is_superuser=False)
        .annotate(
            assigned_active_leads_count=Coalesce(
                Subquery(active_leads_subquery, output_field=IntegerField()),
                0,
            ),
            pending_followups_count=Coalesce(
                Subquery(pending_followups_subquery, output_field=IntegerField()),
                0,
            ),
        )
        .order_by("username")
    )


# ─── Authentication Views ─────────────────────────────────────────────────────

class LoginView(APIView):
    """
    POST /api/v1/auth/login/

    Accepts { "username": "...", "password": "..." }.
    Returns access token, refresh token, and basic user profile.

    Throttled to 5 requests/minute per IP (LoginRateThrottle).
    Returns 400 for invalid credentials or deactivated accounts.
    Does NOT require authentication (public endpoint).
    """

    permission_classes = []
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        refresh = RefreshToken.for_user(user)

        logger.info("User '%s' logged in. Role: %s.", user.username, user.role)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {
                    "id": user.pk,
                    "username": user.username,
                    "email": user.email,
                    "full_name": user.get_full_name(),
                    "role": user.role,
                    "is_active": user.is_active,
                },
            },
            status=status.HTTP_200_OK,
        )


class CustomTokenRefreshView(TokenRefreshView):
    """
    POST /api/v1/auth/refresh/

    Extends SimpleJWT's TokenRefreshView with an active-user check.
    If the user has been deactivated since the refresh token was issued,
    the refresh is rejected with 401 even if the token is valid.

    Token rotation: each call issues a new refresh token and blacklists the old one
    (configured via SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] = True).
    """

    serializer_class = ActiveUserTokenRefreshSerializer


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/

    Accepts { "refresh": "<refresh_token>" }.
    Blacklists the refresh token so it cannot be used again.
    Active access tokens will continue to work until they expire naturally (max 30 min).

    Returns 200 on success, 400 if token is missing or invalid.
    """

    permission_classes = [IsAuthenticated, IsActiveAuthenticatedUser]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"detail": "Refresh token is required for logout."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            token_user_id = token.payload.get(jwt_settings.USER_ID_CLAIM)
            if str(token_user_id) != str(request.user.pk):
                return Response(
                    {"detail": "Refresh token does not belong to the authenticated user."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid or already revoked token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info("User pk=%s logged out (refresh token blacklisted).", request.user.pk)
        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)


class MeView(APIView):
    """
    GET /api/v1/auth/me/

    Returns the authenticated user's own profile.
    Requires a valid access token (Bearer header).
    """

    permission_classes = [IsAuthenticated, IsActiveAuthenticatedUser]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """
    POST /api/v1/auth/change-password/

    Allows an active authenticated user to change their own password and
    blacklists all of their outstanding refresh tokens.
    """

    permission_classes = [IsAuthenticated, IsActiveAuthenticatedUser]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            try:
                user = User.objects.select_for_update().get(pk=request.user.pk)
            except User.DoesNotExist:
                raise PermissionDenied("An active account is required.") from None
            if not user.is_active:
                raise PermissionDenied("An active account is required.")

            if not user.check_password(serializer.validated_data["current_password"]):
                raise ValidationError(
                    {"current_password": ["Current password is incorrect."]}
                )

            new_password = serializer.validated_data["new_password"]
            try:
                validate_password(new_password, user=user)
            except DjangoValidationError as exc:
                raise ValidationError({"new_password": list(exc.messages)}) from None

            user.set_password(new_password)
            user.save(update_fields=["password", "updated_at"])
            revoke_user_refresh_tokens(user)

        logger.info("User pk=%s changed their password.", user.pk)
        return Response(
            {"detail": "Password has been updated successfully."},
            status=status.HTTP_200_OK,
        )


# ─── Employee Management Views ────────────────────────────────────────────────

class EmployeeListCreateView(APIView):
    """
    GET  /api/v1/auth/employees/  — List all non-superuser EMPLOYEE users.
    POST /api/v1/auth/employees/  — Create a new EMPLOYEE user.

    Admin access only. Returns 403 for non-admins.
    Listing includes both active and inactive employees (filterable by ?is_active=).
    """

    permission_classes = [IsAuthenticated, IsAdminRole]
    allowed_query_params = frozenset({"is_active", "format"})

    def get(self, request):
        query_params = request.query_params
        unknown = set(query_params) - self.allowed_query_params
        if unknown:
            raise ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        repeated = [
            name for name in query_params if len(query_params.getlist(name)) > 1
        ]
        if repeated:
            raise ValidationError(
                {name: "Provide each filter only once." for name in sorted(repeated)}
            )

        qs = _employee_queryset()

        if "is_active" in query_params:
            is_active_param = query_params["is_active"].strip().lower()
            if is_active_param == "true":
                qs = qs.filter(is_active=True)
            elif is_active_param == "false":
                qs = qs.filter(is_active=False)
            else:
                raise ValidationError(
                    {"is_active": "Must be 'true' or 'false'."}
                )

        serializer = EmployeeListSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = EmployeeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        logger.info(
            "Admin pk=%s created employee '%s' (pk=%s).",
            request.user.pk,
            employee.username,
            employee.pk,
        )
        return Response(
            EmployeeDetailSerializer(employee).data,
            status=status.HTTP_201_CREATED,
        )


class EmployeeDetailView(APIView):
    """
    GET    /api/v1/auth/employees/<pk>/  — Retrieve employee detail.
    PATCH  /api/v1/auth/employees/<pk>/  — Partial update (profile + is_active).
    DELETE /api/v1/auth/employees/<pk>/  — Soft-deactivate (is_active = False).

    Admin access only.
    The lookup is scoped to non-superuser EMPLOYEE records. ADMIN and superuser
    accounts cannot be targeted here.
    """

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_object(self, pk: int) -> User:
        """
        Retrieve any active or inactive non-superuser employee by PK.
        """
        try:
            return _employee_queryset().get(pk=pk)
        except User.DoesNotExist:
            raise NotFound("Employee not found.") from None

    def get(self, request, pk: int):
        employee = self.get_object(pk)
        return Response(EmployeeDetailSerializer(employee).data)

    def patch(self, request, pk: int):
        employee = self.get_object(pk)
        active_leads = employee.assigned_active_leads_count
        pending_followups = employee.pending_followups_count

        serializer = EmployeeUpdateSerializer(employee, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        employee.assigned_active_leads_count = active_leads
        employee.pending_followups_count = pending_followups

        logger.info(
            "Admin pk=%s updated employee pk=%s. Fields: %s.",
            request.user.pk,
            employee.pk,
            list(request.data.keys()),
        )
        return Response(EmployeeDetailSerializer(employee).data)

    def delete(self, request, pk: int):
        employee = self.get_object(pk)

        if not employee.is_active:
            return Response(
                {"detail": "This employee is already deactivated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee.is_active = False
        employee.save(update_fields=["is_active", "updated_at"])

        logger.info(
            "Admin pk=%s deactivated employee pk=%s ('%s').",
            request.user.pk,
            employee.pk,
            employee.username,
        )
        return Response(
            {
                "detail": f"Employee '{employee.username}' has been deactivated.",
                "assigned_active_leads_count": employee.assigned_active_leads_count,
                "pending_followups_count": employee.pending_followups_count,
            },
            status=status.HTTP_200_OK,
        )


class EmployeePasswordResetView(APIView):
    """
    POST /api/v1/auth/employees/<pk>/reset-password/

    Allows an active administrator to reset a non-superuser employee's password
    and blacklists all of that employee's outstanding refresh tokens.
    """

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk: int):
        with transaction.atomic():
            try:
                employee = User.objects.select_for_update().get(
                    pk=pk,
                    role=User.Role.EMPLOYEE,
                    is_superuser=False,
                )
            except User.DoesNotExist:
                raise NotFound("Employee not found.") from None

            serializer = AdminEmployeePasswordResetSerializer(
                data=request.data,
                context={"employee": employee},
            )
            serializer.is_valid(raise_exception=True)

            employee.set_password(serializer.validated_data["resolved_password"])
            employee.save(update_fields=["password", "updated_at"])
            revoke_user_refresh_tokens(employee)

        logger.info(
            "Admin pk=%s reset password for employee pk=%s ('%s').",
            request.user.pk,
            employee.pk,
            employee.username,
        )
        return Response(
            {"detail": f"Password for employee '{employee.username}' has been reset."},
            status=status.HTTP_200_OK,
        )
