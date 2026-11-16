from django.urls import path

from .views import (
    AdminDashboardSummaryView,
    EmployeePerformanceReportView,
    ReportExportView,
)

urlpatterns = [
    path(
        "dashboard/",
        AdminDashboardSummaryView.as_view(),
        name="report-dashboard-summary",
    ),
    path(
        "employees/",
        EmployeePerformanceReportView.as_view(),
        name="report-employee-performance",
    ),
    path(
        "export/",
        ReportExportView.as_view(),
        name="report-export",
    ),
]
