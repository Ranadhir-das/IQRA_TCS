from django.contrib import admin

from .models import Call, CallOutcome


@admin.register(CallOutcome)
class CallOutcomeAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "is_active", "display_order", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "label")
    ordering = ("display_order", "label")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append("code")
        return tuple(fields)


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lead_reference",
        "caller_identity",
        "outcome_label_snapshot",
        "started_at",
        "ended_at",
        "duration_seconds",
        "created_at",
    )
    list_filter = ("outcome", "started_at")
    search_fields = (
        "lead__name",
        "caller__username",
        "caller_username_snapshot",
        "outcome_code_snapshot",
        "outcome_label_snapshot",
    )
    list_select_related = ("lead", "caller", "outcome")
    ordering = ("-started_at", "-id")
    date_hierarchy = "started_at"
    readonly_fields = tuple(field.name for field in Call._meta.fields)

    @admin.display(description="Lead")
    def lead_reference(self, obj):
        return f"Lead {obj.lead_id}: {obj.lead.name}"

    @admin.display(description="Caller")
    def caller_identity(self, obj):
        return obj.caller_name_snapshot or obj.caller_username_snapshot

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
