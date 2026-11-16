"""URL routing for the IQRA TCS Admin Web CRM (/crm/)."""

from django.urls import path

from . import views

app_name = "webcrm"

urlpatterns = [
    # Auth
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Dashboard
    path("", views.dashboard_view, name="dashboard"),

    # Leads
    path("leads/", views.lead_list_view, name="lead-list"),
    path("leads/create/", views.lead_create_view, name="lead-create"),
    path("leads/bulk-assign/", views.lead_bulk_assign_view, name="lead-bulk-assign"),
    path("leads/import/", views.lead_import_view, name="lead-import"),
    path("leads/import/preview/", views.lead_import_preview_view, name="lead-import-preview"),
    path("leads/import/commit/", views.lead_import_commit_view, name="lead-import-commit"),
    path("leads/import/<int:pk>/cancel/", views.lead_import_cancel_view, name="lead-import-cancel"),
    path("leads/<int:pk>/", views.lead_detail_view, name="lead-detail"),
    path("leads/<int:pk>/edit/", views.lead_edit_view, name="lead-edit"),
    path("leads/<int:pk>/assign/", views.lead_assign_view, name="lead-assign"),
    path("leads/<int:pk>/archive/", views.lead_archive_toggle_view, name="lead-archive-toggle"),

    # Employees
    path("employees/", views.employee_list_view, name="employee-list"),
    path("employees/create/", views.employee_create_view, name="employee-create"),
    path("employees/<int:pk>/", views.employee_detail_view, name="employee-detail"),
    path("employees/<int:pk>/edit/", views.employee_edit_view, name="employee-edit"),
    path("employees/<int:pk>/toggle-active/", views.employee_toggle_active_view, name="employee-toggle-active"),
    path("employees/<int:pk>/reset-password/", views.employee_reset_password_view, name="employee-reset-password"),

    # Calls & Outcomes
    path("calls/", views.call_list_view, name="call-list"),
    path("calls/<int:pk>/", views.call_detail_view, name="call-detail"),
    path("call-outcomes/", views.call_outcome_list_view, name="call-outcome-list"),
    path("call-outcomes/<int:pk>/update/", views.call_outcome_update_view, name="call-outcome-update"),

    # Follow-ups
    path("follow-ups/", views.followup_list_view, name="followup-list"),
    path("follow-ups/<int:pk>/", views.followup_detail_view, name="followup-detail"),
    path("follow-ups/<int:pk>/cancel/", views.followup_cancel_view, name="followup-cancel"),

    # Reports & Exports
    path("reports/", views.reports_hub_view, name="reports-hub"),
    path("reports/export/", views.reports_export_view, name="reports-export"),
]
