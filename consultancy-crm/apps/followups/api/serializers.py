from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.calls.api.serializers import ExplicitOffsetDateTimeField
from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import MAX_NOTES_LENGTH, MAX_REASON_LENGTH
from apps.leads.api.serializers import LeadPagination


class FollowUpPagination(LeadPagination):
    """Paginate follow-ups with 20 default and 100 maximum page size."""


class FollowUpEventSerializer(serializers.ModelSerializer):
    """Read-only representation of an immutable follow-up lifecycle event."""

    actor_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = FollowUpEvent
        fields = [
            "id",
            "event_type",
            "actor_id",
            "actor_username_snapshot",
            "actor_name_snapshot",
            "previous_scheduled_at",
            "new_scheduled_at",
            "previous_status",
            "new_status",
            "reason",
            "created_at",
        ]
        read_only_fields = fields


class EmployeeFollowUpSerializer(serializers.ModelSerializer):
    lead_id = serializers.IntegerField(read_only=True)
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    call_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = FollowUp
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "call_id",
            "scheduled_at",
            "status",
            "notes",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AdminFollowUpSerializer(EmployeeFollowUpSerializer):
    owner_id = serializers.IntegerField(read_only=True)

    class Meta(EmployeeFollowUpSerializer.Meta):
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "call_id",
            "owner_id",
            "owner_username_snapshot",
            "owner_name_snapshot",
            "scheduled_at",
            "status",
            "notes",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FollowUpCreateSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField(min_value=1)
    call_id = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        default=None,
    )
    scheduled_at = ExplicitOffsetDateTimeField()
    notes = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=MAX_NOTES_LENGTH,
        trim_whitespace=False,
        default="",
    )

    allowed_fields = frozenset({"lead_id", "call_id", "scheduled_at", "notes"})

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if attrs.get("notes") is None:
            attrs["notes"] = ""
        return attrs


class FollowUpRescheduleSerializer(serializers.Serializer):
    scheduled_at = ExplicitOffsetDateTimeField()
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=MAX_NOTES_LENGTH,
        trim_whitespace=False,
    )
    reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=MAX_REASON_LENGTH,
        trim_whitespace=False,
        default="",
    )

    allowed_fields = frozenset({"scheduled_at", "notes", "reason"})

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if attrs.get("reason") is None:
            attrs["reason"] = ""
        return attrs


class FollowUpCompleteSerializer(serializers.Serializer):
    def validate(self, attrs):
        if self.initial_data:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(self.initial_data)}
            )
        return attrs


class FollowUpCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=MAX_REASON_LENGTH,
        trim_whitespace=False,
        default="",
    )

    allowed_fields = frozenset({"reason"})

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if attrs.get("reason") is None:
            attrs["reason"] = ""
        return attrs


class FollowUpFilterSerializer(serializers.Serializer):
    class DueState(serializers.ChoiceField):
        pass

    DUE_STATE_TODAY = "today"
    DUE_STATE_OVERDUE = "overdue"
    DUE_STATE_UPCOMING = "upcoming"
    DUE_STATE_CHOICES = (
        (DUE_STATE_TODAY, "Today"),
        (DUE_STATE_OVERDUE, "Overdue"),
        (DUE_STATE_UPCOMING, "Upcoming"),
    )

    status = serializers.ChoiceField(choices=FollowUp.Status.choices, required=False)
    lead_id = serializers.IntegerField(min_value=1, required=False)
    scheduled_from = serializers.DateField(required=False)
    scheduled_to = serializers.DateField(required=False)
    due_state = serializers.ChoiceField(choices=DUE_STATE_CHOICES, required=False)
    employee_id = serializers.IntegerField(min_value=1, required=False)

    allowed_fields = frozenset(
        {
            "status",
            "lead_id",
            "scheduled_from",
            "scheduled_to",
            "due_state",
            "employee_id",
        }
    )

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        start = attrs.get("scheduled_from")
        end = attrs.get("scheduled_to")
        if start and end and start > end:
            raise serializers.ValidationError(
                {"scheduled_to": "Must be greater than or equal to scheduled_from."}
            )
        if "employee_id" in attrs and not self.context["request"].user.is_admin:
            raise serializers.ValidationError(
                {"employee_id": "This filter is available to administrators only."}
            )
        return attrs
