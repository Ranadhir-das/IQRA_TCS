"""Views for Admin Dashboard, Employee Performance, and CSV/XLSX Export."""

from django.http import HttpResponse
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.permissions import IsAdminRole
from apps.reports.exporters import (
    CSV_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    render_csv_bytes,
    render_xlsx_bytes,
    sanitize_filename,
)
from apps.reports.services import (
    build_dashboard_summary,
    build_employee_performance_queryset,
    build_export_dataset,
    format_period_metadata,
    resolve_kolkata_period,
)

from .serializers import (
    DashboardFilterSerializer,
    EmployeePerformanceFilterSerializer,
    EmployeePerformanceSerializer,
    ReportExportFilterSerializer,
    ReportPagination,
)


def _extract_single_value_query_params(
    request,
    *,
    allowed_query_params: frozenset[str],
    filter_params: frozenset[str],
) -> dict:
    query_params = request.query_params
    unknown = set(query_params) - allowed_query_params
    if unknown:
        raise ValidationError(
            {field: "This filter is not supported." for field in sorted(unknown)}
        )
    repeated = [
        name for name in query_params if len(query_params.getlist(name)) > 1
    ]
    if repeated:
        raise ValidationError(
            {
                name: "Provide each query parameter only once."
                for name in sorted(repeated)
            }
        )
    return {
        name: query_params[name]
        for name in filter_params
        if name in query_params
    }


class AdminDashboardSummaryView(APIView):
    """GET /api/v1/reports/dashboard/ — Organization-wide and period CRM analytics."""

    permission_classes = [IsAuthenticated, IsAdminRole]
    allowed_query_params = frozenset(
        {"date_from", "date_to", "employee_id", "format"}
    )
    filter_params = frozenset({"date_from", "date_to", "employee_id"})

    def get(self, request):
        raw_filters = _extract_single_value_query_params(
            request,
            allowed_query_params=self.allowed_query_params,
            filter_params=self.filter_params,
        )
        serializer = DashboardFilterSerializer(data=raw_filters)
        serializer.is_valid(raise_exception=True)
        payload = build_dashboard_summary(**serializer.validated_data)
        return Response(payload, status=status.HTTP_200_OK)


class EmployeePerformanceReportView(generics.ListAPIView):
    """GET /api/v1/reports/employees/ — Paginated per-employee workload and period performance."""

    permission_classes = [IsAuthenticated, IsAdminRole]
    serializer_class = EmployeePerformanceSerializer
    pagination_class = ReportPagination

    allowed_query_params = frozenset(
        {
            "date_from",
            "date_to",
            "employee_id",
            "is_active",
            "page",
            "page_size",
            "format",
        }
    )
    filter_params = frozenset(
        {"date_from", "date_to", "employee_id", "is_active"}
    )

    CURRENT_WORKLOAD_FIELDS = (
        "assigned_active_leads_count",
        "converted_active_leads_count",
        "pending_followups_count",
        "overdue_followups_count",
    )
    PERIOD_PERFORMANCE_FIELDS = (
        "calls_count",
        "unique_leads_called_count",
        "total_call_duration_seconds",
        "average_call_duration_seconds",
        "followups_completed_count",
        "followups_cancelled_count",
    )

    def get_queryset(self):
        raw_filters = _extract_single_value_query_params(
            self.request,
            allowed_query_params=self.allowed_query_params,
            filter_params=self.filter_params,
        )
        serializer = EmployeePerformanceFilterSerializer(data=raw_filters)
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data

        period = resolve_kolkata_period(
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
        )
        if self.paginator is not None:
            period_meta = format_period_metadata(
                period,
                employee_id=filters.get("employee_id"),
            )
            period_meta["metric_scopes"] = {
                "current_workload_fields": list(self.CURRENT_WORKLOAD_FIELDS),
                "period_performance_fields": list(self.PERIOD_PERFORMANCE_FIELDS),
            }
            self.paginator.period_metadata = period_meta

        return build_employee_performance_queryset(
            start_dt=period["start_dt"],
            end_exclusive_dt=period["end_exclusive_dt"],
            today_midnight=period["today_midnight"],
            employee_id=filters.get("employee_id"),
            is_active=filters.get("is_active"),
        )


class ReportExportView(APIView):
    """GET /api/v1/reports/export/ — Download CSV or XLSX operational report."""

    permission_classes = [IsAuthenticated, IsAdminRole]
    allowed_query_params = ReportExportFilterSerializer.allowed_fields | {"format"}
    filter_params = ReportExportFilterSerializer.allowed_fields

    def get(self, request):
        raw_filters = _extract_single_value_query_params(
            request,
            allowed_query_params=self.allowed_query_params,
            filter_params=self.filter_params,
        )
        serializer = ReportExportFilterSerializer(data=raw_filters)
        serializer.is_valid(raise_exception=True)
        filters = dict(serializer.validated_data)
        report_type = filters.pop("report_type")
        export_format = filters.pop("export_format", "csv")

        headers, rows, filename_base = build_export_dataset(
            report_type=report_type,
            filters=filters,
        )
        safe_base = sanitize_filename(filename_base)

        if export_format == "xlsx":
            content = render_xlsx_bytes(headers, rows)
            content_type = XLSX_CONTENT_TYPE
            filename = f"{safe_base}.xlsx"
        else:
            content = render_csv_bytes(headers, rows)
            content_type = CSV_CONTENT_TYPE
            filename = f"{safe_base}.csv"

        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
