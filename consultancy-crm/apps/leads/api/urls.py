from django.urls import path

from .views import (
    BulkLeadAssignmentView,
    EmployeeLeadStatusUpdateView,
    LeadAssignmentHistoryListView,
    LeadDetailView,
    LeadListCreateView,
    LeadImportPreviewView,
    LeadImportCommitView,
    LeadImportCancelView,
)

urlpatterns = [
    path("import/preview/", LeadImportPreviewView.as_view(), name="lead-import-preview"),
    path("import/commit/", LeadImportCommitView.as_view(), name="lead-import-commit"),
    path("import/<int:pk>/cancel/", LeadImportCancelView.as_view(), name="lead-import-cancel"),
    path("bulk-assign/", BulkLeadAssignmentView.as_view(), name="lead-bulk-assign"),
    path("", LeadListCreateView.as_view(), name="lead-list-create"),
    path(
        "<int:pk>/assignment-history/",
        LeadAssignmentHistoryListView.as_view(),
        name="lead-assignment-history",
    ),
    path(
        "<int:pk>/status/",
        EmployeeLeadStatusUpdateView.as_view(),
        name="lead-status-update",
    ),
    path("<int:pk>/", LeadDetailView.as_view(), name="lead-detail"),
]
