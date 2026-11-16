from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "status", "assigned_to", "is_archived")
    list_filter = ("status", "is_archived")
    search_fields = ("name", "phone", "email")
    # Assignment changes must go through the service-backed API so they are audited.
    readonly_fields = ("assigned_to",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.assigned_to_id = None
            obj.save()
            return

        # Avoid saving the stale read-only assigned_to value from a form that
        # was opened before a concurrent API assignment.
        changed_fields = [
            field for field in form.changed_data if field != "assigned_to"
        ]
        if changed_fields:
            obj.save(update_fields=[*changed_fields, "updated_at"])
