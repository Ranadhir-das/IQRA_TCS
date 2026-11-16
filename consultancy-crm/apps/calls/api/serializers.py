from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from apps.calls.models import Call, CallOutcome, OUTCOME_CODE_VALIDATOR


class ExplicitOffsetDateTimeField(serializers.DateTimeField):
    default_error_messages = {
        "offset_required": "Use an ISO-8601 timestamp with an explicit timezone offset.",
    }

    def to_internal_value(self, value):
        if not isinstance(value, str):
            self.fail("offset_required")
        parsed = parse_datetime(value)
        if parsed is None or timezone.is_naive(parsed):
            self.fail("offset_required")
        return super().to_internal_value(parsed)


class CallSubmissionSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField(min_value=1)
    client_event_id = serializers.UUIDField()
    outcome_code = serializers.CharField(
        max_length=50,
        validators=[OUTCOME_CODE_VALIDATOR],
    )
    started_at = ExplicitOffsetDateTimeField()
    ended_at = ExplicitOffsetDateTimeField()
    employee_notes = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=5000,
        trim_whitespace=False,
    )

    allowed_fields = frozenset(
        {
            "lead_id",
            "client_event_id",
            "outcome_code",
            "started_at",
            "ended_at",
            "employee_notes",
        }
    )

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        return attrs


class CallSubmissionResponseSerializer(serializers.ModelSerializer):
    lead_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Call
        fields = [
            "id",
            "lead_id",
            "client_event_id",
            "outcome_code_snapshot",
            "outcome_label_snapshot",
            "started_at",
            "ended_at",
            "duration_seconds",
            "employee_notes",
            "created_at",
        ]
        read_only_fields = fields


class CallHistorySerializer(serializers.ModelSerializer):
    lead_id = serializers.IntegerField(read_only=True)
    lead_name = serializers.CharField(source="lead.name", read_only=True)

    class Meta:
        model = Call
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "caller_username_snapshot",
            "caller_name_snapshot",
            "outcome_code_snapshot",
            "outcome_label_snapshot",
            "started_at",
            "ended_at",
            "duration_seconds",
            "employee_notes",
            "created_at",
        ]
        read_only_fields = fields


class AdminCallHistorySerializer(CallHistorySerializer):
    caller_id = serializers.IntegerField(read_only=True)

    class Meta(CallHistorySerializer.Meta):
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "caller_id",
            "caller_username_snapshot",
            "caller_name_snapshot",
            "outcome_code_snapshot",
            "outcome_label_snapshot",
            "started_at",
            "ended_at",
            "duration_seconds",
            "employee_notes",
            "created_at",
        ]
        read_only_fields = fields


class CallHistoryFilterSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField(min_value=1, required=False)
    outcome_code = serializers.CharField(
        max_length=50,
        validators=[OUTCOME_CODE_VALIDATOR],
        required=False,
    )
    started_at_from = ExplicitOffsetDateTimeField(required=False)
    started_at_to = ExplicitOffsetDateTimeField(required=False)
    caller_id = serializers.IntegerField(min_value=1, required=False)

    allowed_fields = frozenset(
        {"lead_id", "outcome_code", "started_at_from", "started_at_to", "caller_id"}
    )

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        start = attrs.get("started_at_from")
        end = attrs.get("started_at_to")
        if start and end and start > end:
            raise serializers.ValidationError(
                {"started_at_to": "Must be greater than or equal to started_at_from."}
            )
        if "caller_id" in attrs and not self.context["request"].user.is_admin:
            raise serializers.ValidationError(
                {"caller_id": "This filter is available to administrators only."}
            )
        return attrs


class CallOutcomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallOutcome
        fields = ["id", "code", "label", "is_active", "display_order"]
        read_only_fields = fields


class CallOutcomeCreateSerializer(serializers.Serializer):
    code = serializers.CharField(
        max_length=50,
        validators=[OUTCOME_CODE_VALIDATOR],
    )
    label = serializers.CharField(max_length=200)
    display_order = serializers.IntegerField(min_value=0, max_value=2147483647)
    is_active = serializers.BooleanField(required=False, default=True)

    allowed_fields = frozenset({"code", "label", "display_order", "is_active"})

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        return attrs

    def validate_code(self, value):
        if CallOutcome.objects.filter(code=value).exists():
            raise serializers.ValidationError("An outcome with this code already exists.")
        return value


class CallOutcomeUpdateSerializer(serializers.Serializer):
    code = serializers.CharField(required=False, write_only=True, max_length=50)
    label = serializers.CharField(required=False, max_length=200)
    is_active = serializers.BooleanField(required=False)
    display_order = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=2147483647,
    )

    allowed_fields = frozenset({"code", "label", "is_active", "display_order"})

    def validate(self, attrs):
        unknown = set(self.initial_data) - self.allowed_fields
        if unknown:
            raise serializers.ValidationError(
                {field: "This field is not accepted." for field in sorted(unknown)}
            )
        if "code" in self.initial_data:
            raise serializers.ValidationError(
                {"code": "Outcome codes are immutable and cannot be changed."}
            )
        return attrs
