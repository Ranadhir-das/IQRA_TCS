from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

from apps.accounts.models import User
from apps.leads.models import Lead, LeadAssignmentHistory, StagedLeadImport
from apps.leads.services import EMPLOYEE_MUTABLE_LEAD_FIELDS, set_lead_assignment


class AdminLeadSerializer(serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            role=User.Role.EMPLOYEE,
            is_active=True,
            is_superuser=False,
        ),
        allow_null=True,
        required=False,
    )
    assigned_to_username = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()
    assignment_reason = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=1000,
    )

    allowed_write_fields = frozenset(
        {
            "name",
            "phone",
            "alternate_phone",
            "email",
            "course_interest",
            "enquiry_details",
            "status",
            "assigned_to",
            "assignment_reason",
            "internal_notes",
            "is_archived",
        }
    )

    class Meta:
        model = Lead
        fields = [
            "id",
            "name",
            "phone",
            "alternate_phone",
            "email",
            "course_interest",
            "enquiry_details",
            "status",
            "assigned_to",
            "assigned_to_username",
            "assigned_to_name",
            "assignment_reason",
            "internal_notes",
            "is_archived",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "assigned_to_username",
            "assigned_to_name",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def get_assigned_to_username(self, obj):
        user = obj.assigned_to
        return user.username if user is not None else None

    def get_assigned_to_name(self, obj):
        user = obj.assigned_to
        if user is None:
            return None
        return user.get_full_name() or user.username

    def validate_phone(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A primary phone number is required.")
        return value

    def validate_alternate_phone(self, value):
        return value.strip()

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_write_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if "assignment_reason" in attrs and "assigned_to" not in attrs:
            raise serializers.ValidationError(
                {"assignment_reason": "Provide assigned_to with an assignment reason."}
            )
        if (
            self.instance is None
            and attrs.get("is_archived")
            and attrs.get("assigned_to") is not None
        ):
            raise serializers.ValidationError(
                {"assigned_to": "Archived leads cannot be assigned."}
            )
        return attrs

    def create(self, validated_data):
        employee = validated_data.pop("assigned_to", None)
        reason = validated_data.pop("assignment_reason", "")
        actor = self.context["request"].user
        with transaction.atomic():
            lead = Lead.objects.create(assigned_to=None, **validated_data)
            lead, _ = set_lead_assignment(lead, employee, actor, reason)
        return lead

    def update(self, instance, validated_data):
        has_assignment = "assigned_to" in validated_data
        employee = validated_data.pop("assigned_to", None)
        reason = validated_data.pop("assignment_reason", "")
        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=instance.pk)
            will_be_archived = validated_data.get("is_archived", lead.is_archived)
            if has_assignment and employee is not None and will_be_archived:
                raise serializers.ValidationError(
                    {"assigned_to": "Archived leads cannot be assigned."}
                )
            if has_assignment:
                lead, _ = set_lead_assignment(
                    lead,
                    employee,
                    self.context["request"].user,
                    reason,
                )
            for field, value in validated_data.items():
                setattr(lead, field, value)
            if validated_data:
                lead.save()
        return lead


class EmployeeLeadSerializer(serializers.ModelSerializer):
    """Read-only employee representation; internal notes and ownership are hidden."""

    class Meta:
        model = Lead
        fields = [
            "id",
            "name",
            "phone",
            "alternate_phone",
            "email",
            "course_interest",
            "enquiry_details",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class EmployeeLeadStatusUpdateSerializer(serializers.Serializer):
    """Serializer for employee lead lifecycle updates on assigned active leads."""

    status = serializers.ChoiceField(choices=Lead.Status.choices, required=False)
    course_interest = serializers.CharField(
        max_length=200,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )
    enquiry_details = serializers.CharField(
        max_length=settings.LEAD_IMPORT_MAX_TEXT_LENGTH,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )
    alternate_phone = serializers.CharField(
        max_length=32,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )
    email = serializers.EmailField(
        max_length=254,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )

    allowed_fields = EMPLOYEE_MUTABLE_LEAD_FIELDS

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if not attrs:
            raise serializers.ValidationError(
                {"detail": "Provide at least one field to update."}
            )
        return attrs


class LeadFilterSerializer(serializers.Serializer):
    """Validate query filters on GET /api/v1/leads/."""

    search = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=200,
        trim_whitespace=True,
    )
    status = serializers.ChoiceField(choices=Lead.Status.choices, required=False)
    assigned_to = serializers.IntegerField(min_value=1, required=False)
    unassigned = serializers.CharField(required=False, trim_whitespace=True)
    archived = serializers.CharField(required=False, trim_whitespace=True)

    allowed_fields = frozenset(
        {"search", "status", "assigned_to", "unassigned", "archived"}
    )
    admin_only_fields = ("assigned_to", "unassigned", "archived")

    def validate_unassigned(self, value):
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        raise serializers.ValidationError("Must be 'true' or 'false'.")

    def validate_archived(self, value):
        lowered = value.lower()
        if lowered in {"true", "false", "all"}:
            return lowered
        raise serializers.ValidationError("Must be 'true', 'false', or 'all'.")

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        if not self.context["request"].user.is_admin:
            forbidden = [
                field for field in self.admin_only_fields if field in self.initial_data
            ]
            if forbidden:
                raise serializers.ValidationError(
                    {
                        field: "This filter is available to administrators only."
                        for field in forbidden
                    }
                )
        return attrs


class LeadPagination(PageNumberPagination):
    """Small configurable page size for CRM lists with strict page_size validation."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_size(self, request):
        if self.page_size_query_param in request.query_params:
            raw_value = request.query_params[self.page_size_query_param]
            try:
                size = int(raw_value)
            except (TypeError, ValueError):
                raise ValidationError(
                    {self.page_size_query_param: "A valid integer is required."}
                ) from None
            if size < 1:
                raise ValidationError(
                    {
                        self.page_size_query_param: (
                            "Ensure this value is greater than or equal to 1."
                        )
                    }
                )
            return min(size, self.max_page_size)
        return self.page_size


class BulkLeadAssignmentSerializer(serializers.Serializer):
    lead_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=500,
    )
    employee_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            role=User.Role.EMPLOYEE,
            is_active=True,
            is_superuser=False,
        ),
        allow_null=True,
        required=True,
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    allowed_fields = frozenset({"lead_ids", "employee_id", "reason"})

    def validate_lead_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Duplicate lead IDs are not allowed.")
        return value

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        return attrs


class LeadAssignmentHistorySerializer(serializers.ModelSerializer):
    previous_employee = serializers.SerializerMethodField()
    new_employee = serializers.SerializerMethodField()
    changed_by = serializers.SerializerMethodField()

    class Meta:
        model = LeadAssignmentHistory
        fields = [
            "id",
            "previous_employee",
            "new_employee",
            "changed_by",
            "changed_at",
            "reason",
        ]
        read_only_fields = fields

    @staticmethod
    def _identity(obj, relation_name):
        user = getattr(obj, relation_name)
        username = getattr(obj, f"{relation_name}_username")
        full_name = getattr(obj, f"{relation_name}_name")
        if not username and not full_name:
            return None
        return {
            "id": user.pk if user is not None else None,
            "username": username,
            "full_name": full_name or username,
        }

    def get_previous_employee(self, obj):
        return self._identity(obj, "previous_employee")

    def get_new_employee(self, obj):
        return self._identity(obj, "new_employee")

    def get_changed_by(self, obj):
        return self._identity(obj, "changed_by")


class LeadImportPreviewSerializer(serializers.Serializer):
    file = serializers.FileField()
    duplicate_policy = serializers.ChoiceField(
        choices=StagedLeadImport.DuplicatePolicy.choices,
        default=StagedLeadImport.DuplicatePolicy.SKIP,
        required=False,
    )

    def validate(self, attrs):
        if set(self.initial_data) - {"file", "duplicate_policy"}:
            raise serializers.ValidationError(
                "Preview accepts only a file and optional duplicate_policy."
            )
        return attrs


class LeadImportCommitSerializer(serializers.Serializer):
    staged_import_id = serializers.IntegerField(min_value=1)
    commit_token = serializers.CharField(max_length=100, trim_whitespace=True)

    def validate(self, attrs):
        if set(self.initial_data) != {"staged_import_id", "commit_token"}:
            raise serializers.ValidationError(
                "Commit accepts only staged_import_id and commit_token."
            )
        return attrs
