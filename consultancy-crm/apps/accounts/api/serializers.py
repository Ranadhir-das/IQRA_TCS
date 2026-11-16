"""
IQRA TCS Calling CRM — Accounts API Serializers

LoginSerializer:          Validates credentials, enforces is_active, returns User.
UserProfileSerializer:    Read-only user profile (used for /me/).
EmployeeCreateSerializer: Admin creates a new EMPLOYEE user with hashed password.
EmployeeUpdateSerializer: Admin updates employee profile and is_active; role is immutable.
EmployeeListSerializer:   Lightweight listing for the employee roster.
EmployeeDetailSerializer: Full detail for a single employee record.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User


class ActiveUserTokenRefreshSerializer(TokenRefreshSerializer):
    """Reject inactive accounts before SimpleJWT rotates/blacklists the token."""

    def validate(self, attrs):
        # Constructing the configured refresh token validates its signature,
        # expiry, and blacklist status before we inspect its subject.
        refresh = self.token_class(attrs["refresh"])
        user_id = refresh.payload.get(jwt_settings.USER_ID_CLAIM)
        try:
            user = User.objects.get(**{jwt_settings.USER_ID_FIELD: user_id})
        except (User.DoesNotExist, TypeError, ValueError):
            raise InvalidToken("Token is invalid or the account no longer exists.")

        if not user.is_active:
            raise InvalidToken("This account has been deactivated.")

        return super().validate(attrs)


# ─── Authentication Serializers ───────────────────────────────────────────────

class LoginSerializer(serializers.Serializer):
    """
    Validates login credentials.

    Security notes:
    - Passwords are never returned or logged.
    - Wrong-password and non-existent-username both return "Invalid credentials."
    - Deactivated accounts receive a distinct message so users can contact admin.
    - check_password() is used (not authenticate()) to allow distinct inactive check.
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        username = attrs.get("username", "").strip()
        password = attrs.get("password", "")

        # Find by username (case-insensitive)
        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"detail": "Invalid credentials."},
                code="authentication_failed",
            )

        # Verify password before checking active status to prevent timing attacks
        if not user.check_password(password):
            raise serializers.ValidationError(
                {"detail": "Invalid credentials."},
                code="authentication_failed",
            )

        # Explicit inactive check with a helpful, distinct message
        if not user.is_active:
            raise serializers.ValidationError(
                {"detail": "This account has been deactivated. Please contact your administrator."},
                code="account_deactivated",
            )

        attrs["user"] = user
        return attrs


# ─── User Profile Serializers ─────────────────────────────────────────────────

class UserProfileSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for the authenticated user's own profile (/me/).
    """

    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "phone",
            "designation",
            "is_active",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        return obj.get_full_name()


# ─── Employee Management Serializers ──────────────────────────────────────────

class EmployeeListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for the employee roster list.
    """

    full_name = serializers.SerializerMethodField()
    assigned_active_leads_count = serializers.IntegerField(read_only=True, default=0)
    pending_followups_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "email",
            "role",
            "designation",
            "is_active",
            "assigned_active_leads_count",
            "pending_followups_count",
            "created_at",
        ]
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        return obj.get_full_name()


class EmployeeDetailSerializer(serializers.ModelSerializer):
    """
    Full detail serializer for a single employee (read path after create/update).
    """

    full_name = serializers.SerializerMethodField()
    assigned_active_leads_count = serializers.IntegerField(read_only=True, default=0)
    pending_followups_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "phone",
            "designation",
            "is_active",
            "assigned_active_leads_count",
            "pending_followups_count",
            "date_joined",
            "last_login",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        return obj.get_full_name()


class EmployeeCreateSerializer(serializers.ModelSerializer):
    """
    Admin creates a new EMPLOYEE. Password is hashed before storage.
    Role is always set to EMPLOYEE — admins are created via create_admin command.
    """

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
        help_text="Minimum 8 characters.",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "designation",
            "password",
        ]

    def validate_username(self, value: str) -> str:
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate_email(self, value: str) -> str:
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        # Run Django's built-in password validators
        validate_password(value)
        return value

    def create(self, validated_data: dict) -> User:
        password = validated_data.pop("password")
        user = User(
            **validated_data,
            role=User.Role.EMPLOYEE,
            is_active=True,
        )
        user.set_password(password)
        user.save()
        user.assigned_active_leads_count = 0
        user.pending_followups_count = 0
        return user


class EmployeeUpdateSerializer(serializers.ModelSerializer):
    """
    Admin updates an existing employee's profile.

    Allowed fields: first_name, last_name, email, phone, designation, is_active.
    Password updates are handled by dedicated password endpoints.
    """

    allowed_fields = frozenset(
        {
            "first_name",
            "last_name",
            "email",
            "phone",
            "designation",
            "is_active",
        }
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "designation",
            "is_active",
        ]

    def validate(self, attrs):
        if "role" in self.initial_data:
            raise serializers.ValidationError(
                {"role": "Employee roles cannot be changed through this endpoint."}
            )
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        return attrs

    def validate_email(self, value: str) -> str:
        # Allow same email on update (exclude current instance)
        if value and User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """Validate self-service password change requests."""

    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    confirm_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    allowed_fields = frozenset(
        {"current_password", "new_password", "confirm_password"}
    )

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "New password and confirmation do not match."}
            )
        if attrs["new_password"] == attrs["current_password"]:
            raise serializers.ValidationError(
                {"new_password": "New password must be different from the current password."}
            )
        return attrs


class AdminEmployeePasswordResetSerializer(serializers.Serializer):
    """Validate administrator employee password reset requests."""

    new_password = serializers.CharField(
        required=False,
        write_only=True,
        min_length=8,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    password = serializers.CharField(
        required=False,
        write_only=True,
        min_length=8,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    confirm_password = serializers.CharField(
        required=False,
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    allowed_fields = frozenset({"new_password", "password", "confirm_password"})

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        new_password = attrs.get("new_password")
        password = attrs.get("password")
        if new_password is not None and password is not None and new_password != password:
            raise serializers.ValidationError(
                {"new_password": "Conflicting password fields provided."}
            )
        resolved = new_password if new_password is not None else password
        if not resolved:
            raise serializers.ValidationError(
                {"new_password": "This field is required."}
            )
        if "confirm_password" in self.initial_data:
            if attrs.get("confirm_password") != resolved:
                raise serializers.ValidationError(
                    {"confirm_password": "New password and confirmation do not match."}
                )
        user = self.context.get("employee")
        try:
            validate_password(resolved, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from None
        attrs["resolved_password"] = resolved
        return attrs
