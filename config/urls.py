"""IQRA TCS Calling CRM — Root URL Configuration."""

from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "IQRA TCS Admin"
admin.site.site_title = "IQRA TCS"
admin.site.index_title = "Administration"

urlpatterns = [
    # Django admin — maintenance interface only (not the IQRA web CRM interface)
    path("admin/", admin.site.urls),

    # Auth & Employee Management — Phase 1
    path("api/v1/auth/", include("apps.accounts.api.urls")),

    # Lead management — Phase 2.1
    path("api/v1/leads/", include("apps.leads.api.urls")),

    # Call submission — Phase 3.2.1
    path("api/v1/calls/", include("apps.calls.api.urls")),

    # Call outcomes — Phase 3.2.2
    path("api/v1/call-outcomes/", include("apps.calls.api.outcome_urls")),

    # Follow-ups — Phase 3.3.2
    path("api/v1/follow-ups/", include("apps.followups.api.urls")),
]
