from django.contrib import admin

from .models import FollowUp, FollowUpEvent


class ActiveAdminReadOnlyMixin:
    def has_view_permission(self, request, obj=None):
        user = request.user
        return bool(user.is_active and user.is_admin)


@admin.register(FollowUp)
class FollowUpAdmin(ActiveAdminReadOnlyMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "scheduled_at",
        "status",
        "owner_username_snapshot",
        "lead_name",
        "call_id",
        "created_at",
    )
    list_filter = ("status", "scheduled_at")
    search_fields = (
        "owner_username_snapshot",
        "owner_name_snapshot",
        "lead__name",
    )
    list_select_related = ("lead", "owner", "call")
    readonly_fields = tuple(field.name for field in FollowUp._meta.fields)

    @admin.display(description="Lead")
    def lead_name(self, obj):
        return obj.lead.name

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FollowUpEvent)
class FollowUpEventAdmin(ActiveAdminReadOnlyMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "follow_up_id",
        "event_type",
        "actor_username_snapshot",
        "previous_status",
        "new_status",
        "created_at",
    )
    list_filter = ("event_type", "created_at")
    search_fields = ("actor_username_snapshot", "actor_name_snapshot")
    list_select_related = ("follow_up", "actor")
    readonly_fields = tuple(field.name for field in FollowUpEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
