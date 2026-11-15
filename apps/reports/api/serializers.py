"""Serializers, filter validators, and pagination for Phase 4.1 reporting APIs."""

from datetime import date
import re

from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.models import User
from apps.calls.models import OUTCOME_CODE_VALIDATOR
from apps.followups.models import FollowUp
from apps.leads.api.serializers import LeadPagination
from apps.leads.models import Lead

STRICT_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
STRICT_INT_RE = re.compile(r"\A\d+\Z")


class StrictIsoDateField(serializers.DateField):
    """Accept only strict YYYY-MM-DD calendar date strings."""

    default_error_messages = {
        "invalid": "Date has wrong format. Use YYYY-MM-DD.",
    }

    def to_internal_value(self, value):
        if isinstance(value, date) and not hasattr(value, "hour"):
            return value
        if not isinstance(value, str) or not STRICT_DATE_RE.fullmatch(value):
            self.fail("invalid")
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.fail("invalid")


class StrictPositiveIntegerField(serializers.IntegerField):
    """Accept only base-10 positive integers (>= 1)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("min_value", 1)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        if isinstance(data, str):
            if not STRICT_INT_RE.fullmatch(data):
                self.fail("invalid")
        elif not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictBooleanQueryField(serializers.Field):
    """Accept only 'true' or 'false' (case-insensitive) in query strings."""

    default_error_messages = {
        "invalid": "Must be 'true' or 'false'.",
    }

    def to_internal_value(self, data):
        if isinstance(data, bool):
            return data
        if isinstance(data, str):
            lowered = data.strip().lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
        self.fail("invalid")


class ReportPagination(LeadPagination):
    """Strict integer pagination for employee performance reports."""

    period_metadata = None

    def get_page_size(self, request):
        if self.page_size_query_param in request.query_params:
            raw_value = request.query_params[self.page_size_query_param]
            if not isinstance(raw_value, str) or not STRICT_INT_RE.fullmatch(
                raw_value
            ):
                raise ValidationError(
                    {self.page_size_query_param: "A valid positive integer is required."}
                )
            size = int(raw_value)
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

    def paginate_queryset(self, queryset, request, view=None):
        if self.page_query_param in request.query_params:
            raw_page = request.query_params[self.page_query_param]
            if not isinstance(raw_page, str) or not STRICT_INT_RE.fullmatch(
                raw_page
            ):
                raise ValidationError(
                    {self.page_query_param: "A valid positive integer is required."}
                )
            if int(raw_page) < 1:
                raise ValidationError(
                    {
                        self.page_query_param: (
                            "Ensure this value is greater than or equal to 1."
                        )
                    }
                )
        return super().paginate_queryset(queryset, request, view=view)

    def get_paginated_response(self, data):
        payload = {
            "count": self.page.paginator.count,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        }
        if self.period_metadata is not None:
            payload["period"] = self.period_metadata
        return Response(payload)


class BaseReportFilterSerializer(serializers.Serializer):
    """Common Asia/Kolkata date-range and employee_id validation."""

    date_from = StrictIsoDateField(required=False)
    date_to = StrictIsoDateField(required=False)
    employee_id = StrictPositiveIntegerField(required=False)

    allowed_fields = frozenset({"date_from", "date_to", "employee_id"})

    def validate_employee_id(self, value: int) -> int:
        exists = User.objects.filter(
            pk=value,
            role=User.Role.EMPLOYEE,
            is_superuser=False,
        ).exists()
        if not exists:
            raise serializers.ValidationError(
                "Select a valid non-superuser employee."
            )
        return value

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if start and end:
            if start > end:
                raise serializers.ValidationError(
                    {"date_to": "Must be greater than or equal to date_from."}
                )
            max_days = int(getattr(settings, "REPORT_MAX_DATE_RANGE_DAYS", 366))
            inclusive_days = (end - start).days + 1
            if inclusive_days > max_days:
                raise serializers.ValidationError(
                    {
                        "date_to": (
                            f"Date range cannot exceed {max_days} calendar days."
                        )
                    }
                )
        return attrs


class DashboardFilterSerializer(BaseReportFilterSerializer):
    """Validate query parameters for GET /api/v1/reports/dashboard/."""


class EmployeePerformanceFilterSerializer(BaseReportFilterSerializer):
    """Validate query parameters for GET /api/v1/reports/employees/."""

    is_active = StrictBooleanQueryField(required=False)

    allowed_fields = frozenset(
        {"date_from", "date_to", "employee_id", "is_active"}
    )


class ReportExportFilterSerializer(BaseReportFilterSerializer):
    """Validate query parameters for GET /api/v1/reports/export/."""

    REPORT_TYPE_CHOICES = (
        ("employee_performance", "Employee Performance"),
        ("calls", "Calls"),
        ("leads", "Leads"),
        ("followups", "Follow-ups"),
    )
    EXPORT_FORMAT_CHOICES = (
        ("csv", "CSV"),
        ("xlsx", "XLSX"),
    )
    DUE_STATE_CHOICES = (
        ("today", "Today"),
        ("overdue", "Overdue"),
        ("upcoming", "Upcoming"),
    )

    report_type = serializers.ChoiceField(choices=REPORT_TYPE_CHOICES)
    export_format = serializers.ChoiceField(
        choices=EXPORT_FORMAT_CHOICES,
        required=False,
        default="csv",
    )
    is_active = StrictBooleanQueryField(required=False)
    lead_id = StrictPositiveIntegerField(required=False)
    outcome_code = serializers.CharField(
        max_length=50,
        validators=[OUTCOME_CODE_VALIDATOR],
        required=False,
    )
    status = serializers.CharField(max_length=32, required=False)
    archived = serializers.CharField(required=False, trim_whitespace=True)
    unassigned = StrictBooleanQueryField(required=False)
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=200,
        trim_whitespace=True,
    )
    due_state = serializers.ChoiceField(choices=DUE_STATE_CHOICES, required=False)

    allowed_fields = frozenset(
        {
            "report_type",
            "export_format",
            "date_from",
            "date_to",
            "employee_id",
            "is_active",
            "lead_id",
            "outcome_code",
            "status",
            "archived",
            "unassigned",
            "search",
            "due_state",
        }
    )

    allowed_by_report_type = {
        "employee_performance": frozenset(
            {"report_type", "export_format", "date_from", "date_to", "employee_id", "is_active"}
        ),
        "calls": frozenset(
            {
                "report_type",
                "export_format",
                "date_from",
                "date_to",
                "employee_id",
                "lead_id",
                "outcome_code",
            }
        ),
        "leads": frozenset(
            {
                "report_type",
                "export_format",
                "date_from",
                "date_to",
                "employee_id",
                "status",
                "archived",
                "unassigned",
                "search",
            }
        ),
        "followups": frozenset(
            {
                "report_type",
                "export_format",
                "date_from",
                "date_to",
                "employee_id",
                "lead_id",
                "status",
                "due_state",
            }
        ),
    }

    def validate_archived(self, value: str) -> str:
        lowered = value.lower()
        if lowered in {"true", "false", "all"}:
            return lowered
        raise serializers.ValidationError("Must be 'true', 'false', or 'all'.")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        report_type = attrs.get("report_type")
        allowed_for_type = self.allowed_by_report_type[report_type]
        disallowed = set(self.initial_data) - allowed_for_type
        if disallowed:
            raise serializers.ValidationError(
                {
                    field: f"Filter '{field}' is not supported for report_type='{report_type}'."
                    for field in sorted(disallowed)
                }
            )
        status_val = attrs.get("status")
        if status_val is not None:
            if report_type == "leads" and status_val not in Lead.Status.values:
                raise serializers.ValidationError(
                    {"status": f"'{status_val}' is not a valid lead status."}
                )
            if report_type == "followups" and status_val not in FollowUp.Status.values:
                raise serializers.ValidationError(
                    {"status": f"'{status_val}' is not a valid follow-up status."}
                )
        return attrs


class EmployeePerformanceSerializer(serializers.ModelSerializer):
    """Read-only per-employee performance and workload representation."""

    full_name = serializers.SerializerMethodField()
    assigned_active_leads_count = serializers.IntegerField(read_only=True)
    converted_active_leads_count = serializers.IntegerField(read_only=True)
    pending_followups_count = serializers.IntegerField(read_only=True)
    overdue_followups_count = serializers.IntegerField(read_only=True)
    calls_count = serializers.IntegerField(read_only=True)
    unique_leads_called_count = serializers.IntegerField(read_only=True)
    total_call_duration_seconds = serializers.IntegerField(read_only=True)
    average_call_duration_seconds = serializers.SerializerMethodField()
    followups_completed_count = serializers.IntegerField(read_only=True)
    followups_cancelled_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "designation",
            "is_active",
            "assigned_active_leads_count",
            "converted_active_leads_count",
            "pending_followups_count",
            "overdue_followups_count",
            "calls_count",
            "unique_leads_called_count",
            "total_call_duration_seconds",
            "average_call_duration_seconds",
            "followups_completed_count",
            "followups_cancelled_count",
        ]
        read_only_fields = fields

    def get_full_name(self, obj: User) -> str:
        return obj.get_full_name()

    def get_average_call_duration_seconds(self, obj: User) -> float:
        calls = getattr(obj, "calls_count", 0)
        total_dur = getattr(obj, "total_call_duration_seconds", 0)
        return round(total_dur / calls, 2) if calls > 0 else 0.0
