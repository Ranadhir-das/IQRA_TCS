"""Views for the IQRA TCS Admin Web CRM (/crm/)."""

import hashlib
import ipaddress

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.cache import cache
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied as DRFPermissionDenied,
    ValidationError as DRFValidationError,
)

from apps.accounts.api.serializers import (
    AdminEmployeePasswordResetSerializer,
    EmployeeCreateSerializer,
    EmployeeUpdateSerializer,
)
from apps.accounts.api.views import _employee_queryset, revoke_user_refresh_tokens
from apps.accounts.models import User
from apps.calls.api.serializers import (
    CallOutcomeCreateSerializer,
    CallOutcomeUpdateSerializer,
)
from apps.calls.models import Call, CallOutcome
from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import (
    FollowUpConflict,
    cancel_follow_up,
)
from apps.leads.api.serializers import (
    AdminLeadSerializer,
    BulkLeadAssignmentSerializer,
    LeadImportCommitSerializer,
    LeadImportPreviewSerializer,
)
from apps.leads.import_services import (
    cancel_staged_import,
    cleanup_expired_imports,
    commit_staged_import,
    create_staged_import,
)
from apps.leads.imports import ImportFileError, preview_upload
from apps.leads.models import Lead, LeadAssignmentHistory, StagedLeadImport
from apps.leads.services import (
    set_lead_assignment,
    validate_assignment_actor,
    validate_assignment_target,
)
from apps.reports.api.serializers import (
    DashboardFilterSerializer,
    EmployeePerformanceFilterSerializer,
    ReportExportFilterSerializer,
)
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
    resolve_kolkata_period,
)

from .forms import AdminWebLoginForm, LeadForm, LeadImportUploadForm
from .permissions import (
    crm_admin_required,
    is_authorized_crm_admin,
    require_GET_or_HEAD,
    require_POST_only,
)
from .rate_limit import (
    LoginRateLimiter,
    RateLimitStorageUnavailableError,
    resolve_client_ip,
)


def _format_drf_error(exc: Exception) -> str:
    """Flatten a DRF ValidationError / APIException detail into readable text."""
    detail = getattr(exc, "detail", str(exc))
    if isinstance(detail, dict):
        parts = []
        for key, val in detail.items():
            if isinstance(val, list):
                joined = " ".join(str(item) for item in val)
            else:
                joined = str(val)
            if key in {"detail", "non_field_errors"}:
                parts.append(joined)
            else:
                parts.append(f"{key}: {joined}")
        return " | ".join(parts)
    if isinstance(detail, list):
        return " ".join(str(item) for item in detail)
    return str(detail)


def _active_employees():
    """Return active non-superuser EMPLOYEE accounts ordered by username."""
    return User.objects.filter(
        role=User.Role.EMPLOYEE,
        is_active=True,
        is_superuser=False,
    ).order_by("username")


def _all_employees():
    """Return all non-superuser EMPLOYEE accounts ordered by active status then username."""
    return User.objects.filter(
        role=User.Role.EMPLOYEE,
        is_superuser=False,
    ).order_by("-is_active", "username", "id")


def _paginate(request, queryset, per_page: int = 20):
    paginator = Paginator(queryset, per_page)
    page_number = request.GET.get("page") or 1
    return paginator.get_page(page_number)


def _safe_next_url(request, default_name: str = "webcrm:dashboard") -> str:
    candidate = (
        request.POST.get("next")
        or request.GET.get("next")
        or ""
    ).strip()
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse(default_name)


# ─── 1. Authentication Views ──────────────────────────────────────────────────

RATE_LIMIT_ERROR_MESSAGE = "Too many sign-in attempts. Please wait a minute and try again."
RATE_LIMIT_STORAGE_ERROR_MESSAGE = (
    "Sign-in is temporarily unavailable. Please try again shortly."
)


def _get_client_ip(request) -> str:
    """Resolve client IP safely using explicit trusted-proxy verification."""
    return resolve_client_ip(request)


def _login_rate_limit_keys(client_ip: str, normalized_username: str) -> tuple[str, str]:
    return LoginRateLimiter.build_keys(client_ip, normalized_username)


def _get_login_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter()


def _is_login_rate_limited(client_ip: str, normalized_username: str) -> bool:
    return _get_login_rate_limiter().is_rate_limited(client_ip, normalized_username)


def _record_failed_login_attempt(
    client_ip: str, normalized_username: str
) -> tuple[int, int]:
    return _get_login_rate_limiter().record_failed_attempt(
        client_ip, normalized_username
    )


def _clear_login_attempts(client_ip: str, normalized_username: str) -> None:
    _get_login_rate_limiter().clear_attempts(client_ip, normalized_username)


def _render_login_error(
    request,
    *,
    next_url: str,
    message: str,
    status_code: int,
):
    from django.forms.utils import ErrorDict

    form = AdminWebLoginForm()
    form._errors = ErrorDict()
    form.cleaned_data = {}
    form.add_error(None, message)
    return render(
        request,
        "webcrm/login.html",
        {
            "form": form,
            "next": next_url,
        },
        status=status_code,
    )


def login_view(request):
    """GET/POST /crm/login/ — Session login for active ADMIN and superuser accounts."""
    if is_authorized_crm_admin(getattr(request, "user", None)):
        return redirect(_safe_next_url(request))

    next_url = request.GET.get("next", "")
    if request.method == "POST":
        next_url = request.POST.get("next", "")
        client_ip = _get_client_ip(request)
        raw_username = request.POST.get("username") or ""

        try:
            limiter = _get_login_rate_limiter()
            reservation = limiter.admit_attempt(client_ip, raw_username)
        except RateLimitStorageUnavailableError:
            return _render_login_error(
                request,
                next_url=next_url,
                message=RATE_LIMIT_STORAGE_ERROR_MESSAGE,
                status_code=503,
            )

        if not reservation.admitted:
            return _render_login_error(
                request,
                next_url=next_url,
                message=RATE_LIMIT_ERROR_MESSAGE,
                status_code=429,
            )

        form = AdminWebLoginForm(request.POST)
        if form.is_valid():
            try:
                allowed = limiter.reconcile_successful_attempt(reservation)
            except RateLimitStorageUnavailableError:
                return _render_login_error(
                    request,
                    next_url=next_url,
                    message=RATE_LIMIT_STORAGE_ERROR_MESSAGE,
                    status_code=503,
                )
            if not allowed:
                return _render_login_error(
                    request,
                    next_url=next_url,
                    message=RATE_LIMIT_ERROR_MESSAGE,
                    status_code=429,
                )
            login(request, form.authenticated_user)
            return redirect(_safe_next_url(request))
    else:
        form = AdminWebLoginForm()

    return render(
        request,
        "webcrm/login.html",
        {
            "form": form,
            "next": next_url,
        },
    )


@require_POST_only
def logout_view(request):
    """POST /crm/logout/ — Terminate the admin web session."""
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("webcrm:login")


# ─── 2. Admin Dashboard ───────────────────────────────────────────────────────

@crm_admin_required
def dashboard_view(request):
    """GET /crm/ — Organization-wide and period CRM analytics dashboard."""
    raw_filters = {}
    for key in ("date_from", "date_to", "employee_id"):
        val = (request.GET.get(key) or "").strip()
        if val:
            raw_filters[key] = val

    filter_errors = None
    serializer = DashboardFilterSerializer(data=raw_filters)
    if serializer.is_valid():
        validated = serializer.validated_data
    else:
        filter_errors = _format_drf_error(DRFValidationError(serializer.errors))
        validated = {}

    period = resolve_kolkata_period(
        date_from=validated.get("date_from"),
        date_to=validated.get("date_to"),
    )
    summary = build_dashboard_summary(**validated, period=period)
    employee_performance = list(
        build_employee_performance_queryset(
            start_dt=period["start_dt"],
            end_exclusive_dt=period["end_exclusive_dt"],
            today_midnight=period["today_midnight"],
            employee_id=validated.get("employee_id"),
        )[:10]
    )
    for emp in employee_performance:
        emp.average_call_duration_seconds = (
            round(emp.total_call_duration_seconds / emp.calls_count, 2)
            if emp.calls_count > 0
            else 0.0
        )

    max_daily_calls = max(
        (row["total_calls"] for row in summary["calls"]["daily_trends"]),
        default=1,
    ) or 1

    recent_calls_qs = Call.objects.select_related("lead").order_by("-started_at", "-id")
    recent_leads_qs = (
        Lead.objects.select_related("assigned_to")
        .filter(is_archived=False)
        .order_by("-created_at", "-id")
    )
    if validated.get("employee_id"):
        recent_calls_qs = recent_calls_qs.filter(caller_id=validated["employee_id"])
        recent_leads_qs = recent_leads_qs.filter(assigned_to_id=validated["employee_id"])

    total_leads_all = (
        summary["leads"]["total_active"] + summary["leads"]["total_archived"]
    )
    filtered_emp_leads = summary["leads"].get("filtered_employee_leads")
    employee_total_leads = (
        filtered_emp_leads["assigned_active"] + filtered_emp_leads["assigned_archived"]
        if filtered_emp_leads is not None
        else None
    )

    context = {
        "active_nav": "dashboard",
        "summary": summary,
        "total_leads_all": total_leads_all,
        "employee_total_leads": employee_total_leads,
        "employee_performance": employee_performance,
        "recent_calls": list(recent_calls_qs[:6]),
        "recent_leads": list(recent_leads_qs[:6]),
        "employees": _all_employees(),
        "selected_date_from": summary["period"]["date_from"],
        "selected_date_to": summary["period"]["date_to"],
        "selected_employee_id": validated.get("employee_id"),
        "filter_errors": filter_errors,
        "max_daily_calls": max_daily_calls,
    }
    if request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "dashboard-content":
        return render(request, "webcrm/partials/dashboard_content.html", context)
    return render(request, "webcrm/dashboard.html", context)


# ─── 3. Lead Management Views ─────────────────────────────────────────────────

@crm_admin_required
def lead_list_view(request):
    """GET /crm/leads/ — Search, filter, paginate, and bulk-assign leads."""
    qs = Lead.objects.select_related("assigned_to", "created_by")

    archived = (request.GET.get("archived") or "false").strip().lower()
    if archived not in {"true", "false", "all"}:
        archived = "false"
    if archived == "true":
        qs = qs.filter(is_archived=True)
    elif archived == "false":
        qs = qs.filter(is_archived=False)

    status_filter = (request.GET.get("status") or "").strip()
    if status_filter in Lead.Status.values:
        qs = qs.filter(status=status_filter)
    else:
        status_filter = ""

    unassigned = (request.GET.get("unassigned") or "").strip().lower()
    if unassigned == "true":
        qs = qs.filter(assigned_to__isnull=True)
    elif unassigned == "false":
        qs = qs.filter(assigned_to__isnull=False)
    else:
        unassigned = ""

    assigned_to_raw = (request.GET.get("assigned_to") or "").strip()
    selected_assigned_to = None
    if assigned_to_raw.isdigit() and int(assigned_to_raw) >= 1:
        selected_assigned_to = int(assigned_to_raw)
        qs = qs.filter(assigned_to_id=selected_assigned_to)

    search_term = (request.GET.get("search") or "").strip()
    if search_term:
        qs = qs.filter(
            Q(name__icontains=search_term)
            | Q(phone__icontains=search_term)
            | Q(alternate_phone__icontains=search_term)
            | Q(email__icontains=search_term)
            | Q(course_interest__icontains=search_term)
        )

    qs = qs.order_by("-created_at", "-id")
    page_obj = _paginate(request, qs, per_page=20)

    context = {
        "active_nav": "leads",
        "page_obj": page_obj,
        "lead_statuses": Lead.Status.choices,
        "employees": _all_employees(),
        "active_employees": _active_employees(),
        "filters": {
            "search": search_term,
            "status": status_filter,
            "assigned_to": selected_assigned_to,
            "unassigned": unassigned,
            "archived": archived,
        },
    }
    if request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "leads-table-container":
        return render(request, "webcrm/partials/leads_table.html", context)
    return render(request, "webcrm/leads/list.html", context)


@crm_admin_required
def lead_create_view(request):
    """GET/POST /crm/leads/create/ — Create a new lead using AdminLeadSerializer."""
    if request.method == "POST":
        form = LeadForm(request.POST)
        payload = {
            "name": request.POST.get("name", ""),
            "phone": request.POST.get("phone", ""),
            "alternate_phone": request.POST.get("alternate_phone", ""),
            "email": request.POST.get("email", ""),
            "course_interest": request.POST.get("course_interest", ""),
            "enquiry_details": request.POST.get("enquiry_details", ""),
            "status": request.POST.get("status") or Lead.Status.NEW,
            "internal_notes": request.POST.get("internal_notes", ""),
            "is_archived": bool(request.POST.get("is_archived")),
        }
        assigned_raw = (request.POST.get("assigned_to") or "").strip()
        if assigned_raw:
            payload["assigned_to"] = int(assigned_raw) if assigned_raw.isdigit() else assigned_raw
            reason_raw = (request.POST.get("assignment_reason") or "").strip()
            if reason_raw:
                payload["assignment_reason"] = reason_raw

        serializer = AdminLeadSerializer(data=payload, context={"request": request})
        if serializer.is_valid():
            lead = serializer.save(created_by=request.user)
            messages.success(request, f"Lead '{lead.name}' created successfully.")
            return redirect("webcrm:lead-detail", pk=lead.pk)

        for field, errors in serializer.errors.items():
            msg = " ".join(str(e) for e in (errors if isinstance(errors, list) else [errors]))
            if field in form.fields:
                form.add_error(field, msg)
            else:
                form.add_error(None, msg)
    else:
        form = LeadForm()

    return render(
        request,
        "webcrm/leads/form.html",
        {
            "active_nav": "leads",
            "form": form,
            "is_edit": False,
        },
    )


@crm_admin_required
def lead_detail_view(request, pk: int):
    """GET /crm/leads/<pk>/ — Lead detail, assignment controls, call & follow-up history."""
    lead = get_object_or_404(
        Lead.objects.select_related("assigned_to", "created_by"),
        pk=pk,
    )
    assignment_history = list(
        LeadAssignmentHistory.objects.filter(lead=lead)
        .select_related("previous_employee", "new_employee", "changed_by")
        .order_by("-changed_at", "-id")[:50]
    )
    calls = list(
        Call.objects.filter(lead=lead)
        .order_by("-started_at", "-id")[:50]
    )
    follow_ups = list(
        FollowUp.objects.filter(lead=lead)
        .select_related("owner")
        .order_by("scheduled_at", "id")[:50]
    )
    return render(
        request,
        "webcrm/leads/detail.html",
        {
            "active_nav": "leads",
            "lead": lead,
            "active_employees": _active_employees(),
            "assignment_history": assignment_history,
            "calls": calls,
            "follow_ups": follow_ups,
        },
    )


@crm_admin_required
def lead_edit_view(request, pk: int):
    """GET/POST /crm/leads/<pk>/edit/ — Edit non-assignment lead fields via AdminLeadSerializer."""
    lead = get_object_or_404(
        Lead.objects.select_related("assigned_to", "created_by"),
        pk=pk,
    )
    if request.method == "POST":
        form = LeadForm(request.POST, is_edit=True)
        payload = {
            "name": request.POST.get("name", ""),
            "phone": request.POST.get("phone", ""),
            "alternate_phone": request.POST.get("alternate_phone", ""),
            "email": request.POST.get("email", ""),
            "course_interest": request.POST.get("course_interest", ""),
            "enquiry_details": request.POST.get("enquiry_details", ""),
            "status": request.POST.get("status") or lead.status,
            "internal_notes": request.POST.get("internal_notes", ""),
            "is_archived": bool(request.POST.get("is_archived")),
        }
        serializer = AdminLeadSerializer(
            lead,
            data=payload,
            partial=True,
            context={"request": request},
        )
        if serializer.is_valid():
            updated = serializer.save()
            messages.success(request, f"Lead '{updated.name}' updated successfully.")
            return redirect("webcrm:lead-detail", pk=updated.pk)

        for field, errors in serializer.errors.items():
            msg = " ".join(str(e) for e in (errors if isinstance(errors, list) else [errors]))
            if field in form.fields:
                form.add_error(field, msg)
            else:
                form.add_error(None, msg)
    else:
        form = LeadForm(
            is_edit=True,
            initial={
                "name": lead.name,
                "phone": lead.phone,
                "alternate_phone": lead.alternate_phone,
                "email": lead.email,
                "course_interest": lead.course_interest,
                "enquiry_details": lead.enquiry_details,
                "status": lead.status,
                "internal_notes": lead.internal_notes,
                "is_archived": lead.is_archived,
            },
        )

    return render(
        request,
        "webcrm/leads/form.html",
        {
            "active_nav": "leads",
            "form": form,
            "lead": lead,
            "is_edit": True,
        },
    )


@crm_admin_required
@require_POST_only
def lead_assign_view(request, pk: int):
    """POST /crm/leads/<pk>/assign/ — Assign, reassign, or unassign a single lead."""
    lead = get_object_or_404(Lead, pk=pk)
    assigned_raw = (request.POST.get("assigned_to") or "").strip()
    reason = (request.POST.get("reason") or "").strip()

    if assigned_raw == "":
        assigned_val = None
    elif assigned_raw.isdigit():
        assigned_val = int(assigned_raw)
    else:
        assigned_val = assigned_raw

    serializer = AdminLeadSerializer(
        lead,
        data={"assigned_to": assigned_val, "assignment_reason": reason},
        partial=True,
        context={"request": request},
    )
    if not serializer.is_valid():
        messages.error(request, _format_drf_error(DRFValidationError(serializer.errors)))
        return HttpResponse(
            render(
                request,
                "webcrm/leads/detail.html",
                {
                    "active_nav": "leads",
                    "lead": Lead.objects.select_related("assigned_to", "created_by").get(pk=lead.pk),
                    "active_employees": _active_employees(),
                    "assignment_history": list(
                        LeadAssignmentHistory.objects.filter(lead=lead)
                        .select_related("previous_employee", "new_employee", "changed_by")
                        .order_by("-changed_at", "-id")[:50]
                    ),
                    "calls": list(Call.objects.filter(lead=lead).order_by("-started_at", "-id")[:50]),
                    "follow_ups": list(FollowUp.objects.filter(lead=lead).select_related("owner").order_by("scheduled_at", "id")[:50]),
                },
            ).content,
            status=400,
        )

    try:
        serializer.save()
    except DRFValidationError as exc:
        messages.error(request, _format_drf_error(exc))
        return HttpResponseBadRequest(_format_drf_error(exc))

    messages.success(request, "Lead assignment updated.")
    return redirect("webcrm:lead-detail", pk=lead.pk)


@crm_admin_required
@require_POST_only
def lead_archive_toggle_view(request, pk: int):
    """POST /crm/leads/<pk>/archive/ — Archive or unarchive a lead."""
    lead = get_object_or_404(Lead, pk=pk)
    action = (request.POST.get("action") or "").strip().lower()
    if action == "archive":
        target_archived = True
    elif action == "unarchive":
        target_archived = False
    else:
        target_archived = not lead.is_archived

    serializer = AdminLeadSerializer(
        lead,
        data={"is_archived": target_archived},
        partial=True,
        context={"request": request},
    )
    if serializer.is_valid():
        serializer.save()
        state_word = "archived" if target_archived else "unarchived"
        messages.success(request, f"Lead '{lead.name}' has been {state_word}.")
    else:
        messages.error(request, _format_drf_error(DRFValidationError(serializer.errors)))

    return redirect("webcrm:lead-detail", pk=lead.pk)


@crm_admin_required
@require_POST_only
def lead_bulk_assign_view(request):
    """POST /crm/leads/bulk-assign/ — Atomic bulk assignment reusing BulkLeadAssignmentSerializer."""
    raw_lead_ids = request.POST.getlist("lead_ids")
    parsed_lead_ids = []
    for raw_id in raw_lead_ids:
        raw_id_str = str(raw_id).strip()
        if raw_id_str.isdigit():
            parsed_lead_ids.append(int(raw_id_str))
        elif raw_id_str:
            parsed_lead_ids.append(raw_id_str)

    emp_raw = (request.POST.get("employee_id") or "").strip()
    if emp_raw == "" or emp_raw.lower() == "unassign":
        emp_val = None
    elif emp_raw.isdigit():
        emp_val = int(emp_raw)
    else:
        emp_val = emp_raw

    reason = (request.POST.get("reason") or "").strip()

    serializer = BulkLeadAssignmentSerializer(
        data={
            "lead_ids": parsed_lead_ids,
            "employee_id": emp_val,
            "reason": reason,
        }
    )
    if not serializer.is_valid():
        err_msg = _format_drf_error(DRFValidationError(serializer.errors))
        messages.error(request, err_msg)
        return redirect("webcrm:lead-list")

    lead_ids = serializer.validated_data["lead_ids"]
    employee = serializer.validated_data["employee_id"]
    clean_reason = serializer.validated_data.get("reason", "")

    try:
        with transaction.atomic():
            leads = list(
                Lead.objects.select_for_update()
                .filter(pk__in=lead_ids)
                .order_by("pk")
            )
            found_ids = {lead.pk for lead in leads}
            missing_ids = sorted(set(lead_ids) - found_ids)
            if missing_ids:
                raise DRFValidationError({"lead_ids": f"Unknown lead IDs: {missing_ids}."})

            archived_ids = [lead.pk for lead in leads if lead.is_archived]
            if archived_ids:
                raise DRFValidationError(
                    {"lead_ids": f"Archived leads cannot be assigned: {archived_ids}."}
                )

            actor = validate_assignment_actor(request.user, lock=True)
            target_emp = validate_assignment_target(employee, lock=True)

            changed = 0
            unchanged = 0
            for lead in leads:
                _, did_change = set_lead_assignment(lead, target_emp, actor, clean_reason)
                if did_change:
                    changed += 1
                else:
                    unchanged += 1
    except DRFValidationError as exc:
        messages.error(request, _format_drf_error(exc))
        return redirect("webcrm:lead-list")

    messages.success(
        request,
        f"Bulk assignment complete: {changed} updated, {unchanged} unchanged (of {len(lead_ids)} selected).",
    )
    return redirect("webcrm:lead-list")


@crm_admin_required
def lead_import_view(request):
    """GET /crm/leads/import/ — Render the CSV/XLSX import upload screen."""
    cleanup_expired_imports()
    form = LeadImportUploadForm()
    return render(
        request,
        "webcrm/leads/import.html",
        {
            "active_nav": "leads",
            "form": form,
        },
    )


@crm_admin_required
@require_POST_only
def lead_import_preview_view(request):
    """POST /crm/leads/import/preview/ — Stage and preview CSV/XLSX upload without committing."""
    cleanup_expired_imports()
    form = LeadImportUploadForm(request.POST, request.FILES)
    data = {}
    if "file" in request.FILES:
        data["file"] = request.FILES["file"]
    if "duplicate_policy" in request.POST:
        data["duplicate_policy"] = request.POST["duplicate_policy"]

    serializer = LeadImportPreviewSerializer(data=data)
    if not serializer.is_valid():
        err_text = _format_drf_error(DRFValidationError(serializer.errors))
        form.add_error(None, err_text)
        return render(
            request,
            "webcrm/leads/import.html",
            {"active_nav": "leads", "form": form},
            status=400,
        )

    upload = serializer.validated_data["file"]
    policy = serializer.validated_data["duplicate_policy"]
    try:
        preview = preview_upload(upload, duplicate_policy=policy)
    except ImportFileError as exc:
        form.add_error("file", str(exc))
        return render(
            request,
            "webcrm/leads/import.html",
            {"active_nav": "leads", "form": form},
            status=400,
        )

    staged, token = create_staged_import(
        owner=request.user,
        preview=preview,
        duplicate_policy=policy,
    )
    return render(
        request,
        "webcrm/leads/import.html",
        {
            "active_nav": "leads",
            "form": LeadImportUploadForm(initial={"duplicate_policy": policy}),
            "staged": staged,
            "commit_token": token,
            "header_mapping": preview["mapping"],
            "summary": preview["summary"],
            "preview_rows": preview["row_results"][:200],
            "total_preview_rows": len(preview["row_results"]),
        },
    )


@crm_admin_required
@require_POST_only
def lead_import_commit_view(request):
    """POST /crm/leads/import/commit/ — Explicitly commit a staged import."""
    cleanup_expired_imports()
    if request.POST.get("confirm_commit") != "yes":
        messages.error(
            request,
            "Explicit confirmation is required before committing a staged import.",
        )
        return redirect("webcrm:lead-import")

    raw_id = (request.POST.get("staged_import_id") or "").strip()
    payload = {
        "staged_import_id": int(raw_id) if raw_id.isdigit() else raw_id,
        "commit_token": request.POST.get("commit_token", ""),
    }
    serializer = LeadImportCommitSerializer(data=payload)
    if not serializer.is_valid():
        err_text = _format_drf_error(DRFValidationError(serializer.errors))
        messages.error(request, err_text)
        return render(
            request,
            "webcrm/leads/import.html",
            {"active_nav": "leads", "form": LeadImportUploadForm(), "commit_error": err_text},
            status=400,
        )

    try:
        result = commit_staged_import(
            staged_id=serializer.validated_data["staged_import_id"],
            token=serializer.validated_data["commit_token"],
            owner=request.user,
        )
    except DRFValidationError as exc:
        err_text = _format_drf_error(exc)
        messages.error(request, err_text)
        return render(
            request,
            "webcrm/leads/import.html",
            {"active_nav": "leads", "form": LeadImportUploadForm(), "commit_error": err_text},
            status=400,
        )

    messages.success(
        request,
        f"Import committed: {result['created']} created, {result['skipped']} skipped, {result['failed']} failed.",
    )
    return render(
        request,
        "webcrm/leads/import.html",
        {
            "active_nav": "leads",
            "form": LeadImportUploadForm(),
            "commit_result": result,
        },
    )


@crm_admin_required
@require_POST_only
def lead_import_cancel_view(request, pk: int):
    """POST /crm/leads/import/<pk>/cancel/ — Cancel a staged import owned by request.user."""
    try:
        cancel_staged_import(staged_id=pk, owner=request.user)
    except DRFValidationError as exc:
        err_text = _format_drf_error(exc)
        messages.error(request, err_text)
        return render(
            request,
            "webcrm/leads/import.html",
            {"active_nav": "leads", "form": LeadImportUploadForm(), "commit_error": err_text},
            status=400,
        )

    messages.info(request, "Staged import has been cancelled and discarded.")
    return redirect("webcrm:lead-import")


# ─── 4. Employee Management Views ─────────────────────────────────────────────

def _get_non_superuser_employee_or_404(pk: int) -> User:
    try:
        return _employee_queryset().get(pk=pk)
    except User.DoesNotExist:
        raise Http404("Employee not found.") from None


@crm_admin_required
def employee_list_view(request):
    """GET /crm/employees/ — List non-superuser employees with workload & period performance."""
    is_active_raw = (request.GET.get("is_active") or "").strip().lower()
    is_active_filter = None
    if is_active_raw == "true":
        is_active_filter = True
    elif is_active_raw == "false":
        is_active_filter = False
    else:
        is_active_raw = ""

    search_term = (request.GET.get("search") or "").strip()

    period = resolve_kolkata_period()
    qs = build_employee_performance_queryset(
        start_dt=period["start_dt"],
        end_exclusive_dt=period["end_exclusive_dt"],
        today_midnight=period["today_midnight"],
        is_active=is_active_filter,
    )
    if search_term:
        qs = qs.filter(
            Q(username__icontains=search_term)
            | Q(first_name__icontains=search_term)
            | Q(last_name__icontains=search_term)
            | Q(designation__icontains=search_term)
            | Q(phone__icontains=search_term)
        )

    page_obj = _paginate(request, qs, per_page=20)
    for emp in page_obj.object_list:
        emp.average_call_duration_seconds = (
            round(emp.total_call_duration_seconds / emp.calls_count, 2)
            if emp.calls_count > 0
            else 0.0
        )

    return render(
        request,
        "webcrm/employees/list.html",
        {
            "active_nav": "employees",
            "page_obj": page_obj,
            "period": period,
            "filters": {
                "is_active": is_active_raw,
                "search": search_term,
            },
        },
    )


@crm_admin_required
def employee_create_view(request):
    """GET/POST /crm/employees/create/ — Create a non-superuser EMPLOYEE via EmployeeCreateSerializer."""
    form_data = {}
    errors = None
    if request.method == "POST":
        form_data = {
            "username": request.POST.get("username", "").strip(),
            "email": request.POST.get("email", "").strip(),
            "first_name": request.POST.get("first_name", "").strip(),
            "last_name": request.POST.get("last_name", "").strip(),
            "phone": request.POST.get("phone", "").strip(),
            "designation": request.POST.get("designation", "").strip(),
            "password": request.POST.get("password", ""),
        }
        serializer = EmployeeCreateSerializer(data=form_data)
        if serializer.is_valid():
            employee = serializer.save()
            messages.success(
                request,
                f"Employee '{employee.username}' created successfully.",
            )
            return redirect("webcrm:employee-detail", pk=employee.pk)
        errors = _format_drf_error(DRFValidationError(serializer.errors))

    return render(
        request,
        "webcrm/employees/create.html",
        {
            "active_nav": "employees",
            "form_data": form_data,
            "errors": errors,
        },
        status=400 if errors else 200,
    )


def _build_employee_detail_context(
    employee: User,
    *,
    password_error: str | None = None,
) -> dict:
    """Build complete template context for employee detail and error re-renders."""
    period = resolve_kolkata_period()
    perf_obj = (
        build_employee_performance_queryset(
            start_dt=period["start_dt"],
            end_exclusive_dt=period["end_exclusive_dt"],
            today_midnight=period["today_midnight"],
            employee_id=employee.pk,
        ).first()
    )
    if perf_obj is not None:
        perf_obj.average_call_duration_seconds = (
            round(perf_obj.total_call_duration_seconds / perf_obj.calls_count, 2)
            if perf_obj.calls_count > 0
            else 0.0
        )

    assigned_leads = list(
        Lead.objects.filter(assigned_to=employee, is_archived=False)
        .order_by("-created_at", "-id")[:25]
    )
    recent_calls = list(
        Call.objects.filter(caller=employee)
        .select_related("lead")
        .order_by("-started_at", "-id")[:25]
    )
    pending_followups = list(
        FollowUp.objects.filter(owner=employee, status=FollowUp.Status.PENDING)
        .select_related("lead")
        .order_by("scheduled_at", "id")[:25]
    )
    context = {
        "active_nav": "employees",
        "employee": employee,
        "performance": perf_obj,
        "period": period,
        "assigned_leads": assigned_leads,
        "recent_calls": recent_calls,
        "pending_followups": pending_followups,
    }
    if password_error is not None:
        context["password_error"] = password_error
    return context


@crm_admin_required
def employee_detail_view(request, pk: int):
    """GET /crm/employees/<pk>/ — Employee profile, workload, performance, and password reset controls."""
    employee = _get_non_superuser_employee_or_404(pk)
    return render(
        request,
        "webcrm/employees/detail.html",
        _build_employee_detail_context(employee),
    )


@crm_admin_required
@require_POST_only
def employee_edit_view(request, pk: int):
    """POST /crm/employees/<pk>/edit/ — Update employee profile fields via EmployeeUpdateSerializer."""
    employee = _get_non_superuser_employee_or_404(pk)
    payload = {
        "first_name": request.POST.get("first_name", "").strip(),
        "last_name": request.POST.get("last_name", "").strip(),
        "email": request.POST.get("email", "").strip(),
        "phone": request.POST.get("phone", "").strip(),
        "designation": request.POST.get("designation", "").strip(),
    }

    serializer = EmployeeUpdateSerializer(employee, data=payload, partial=True)
    if not serializer.is_valid():
        err_text = _format_drf_error(DRFValidationError(serializer.errors))
        messages.error(request, err_text)
        return redirect("webcrm:employee-detail", pk=employee.pk)

    serializer.save()
    messages.success(request, f"Profile for '{employee.username}' updated.")
    return redirect("webcrm:employee-detail", pk=employee.pk)


@crm_admin_required
@require_POST_only
def employee_toggle_active_view(request, pk: int):
    """POST /crm/employees/<pk>/toggle-active/ — Activate or deactivate a non-superuser employee."""
    employee = _get_non_superuser_employee_or_404(pk)
    action = (request.POST.get("action") or "").strip().lower()
    if action == "deactivate":
        target_active = False
    elif action == "activate":
        target_active = True
    else:
        target_active = not employee.is_active

    serializer = EmployeeUpdateSerializer(
        employee,
        data={"is_active": target_active},
        partial=True,
    )
    if serializer.is_valid():
        updated = serializer.save()
        if not updated.is_active:
            revoke_user_refresh_tokens(updated)
            messages.warning(
                request,
                f"Employee '{updated.username}' has been deactivated and active sessions revoked.",
            )
        else:
            messages.success(
                request,
                f"Employee '{updated.username}' has been reactivated.",
            )
    else:
        messages.error(request, _format_drf_error(DRFValidationError(serializer.errors)))

    return redirect("webcrm:employee-detail", pk=employee.pk)


@crm_admin_required
@require_POST_only
def employee_reset_password_view(request, pk: int):
    """POST /crm/employees/<pk>/reset-password/ — Reset employee password and revoke tokens."""
    with transaction.atomic():
        try:
            employee = User.objects.select_for_update().get(
                pk=pk,
                role=User.Role.EMPLOYEE,
                is_superuser=False,
            )
        except User.DoesNotExist:
            raise Http404("Employee not found.") from None

        payload = {
            "new_password": request.POST.get("new_password", ""),
            "confirm_password": request.POST.get("confirm_password", ""),
        }
        serializer = AdminEmployeePasswordResetSerializer(
            data=payload,
            context={"employee": employee},
        )
        if not serializer.is_valid():
            err_text = _format_drf_error(DRFValidationError(serializer.errors))
            messages.error(request, err_text)
            return HttpResponse(
                render(
                    request,
                    "webcrm/employees/detail.html",
                    _build_employee_detail_context(
                        employee,
                        password_error=err_text,
                    ),
                ).content,
                status=400,
            )

        employee.set_password(serializer.validated_data["resolved_password"])
        employee.save(update_fields=["password", "updated_at"])
        revoke_user_refresh_tokens(employee)

    messages.success(
        request,
        f"Password for employee '{employee.username}' has been reset and active tokens revoked.",
    )
    return redirect("webcrm:employee-detail", pk=pk)


# ─── 5. Call History & Call Outcomes Views ────────────────────────────────────

@crm_admin_required
@require_GET_or_HEAD
def call_list_view(request):
    """GET /crm/calls/ — Read-only organization-wide call history using immutable snapshots."""
    qs = Call.objects.select_related("lead")

    employee_id_raw = (request.GET.get("employee_id") or "").strip()
    selected_employee_id = None
    if employee_id_raw.isdigit() and int(employee_id_raw) >= 1:
        selected_employee_id = int(employee_id_raw)
        qs = qs.filter(caller_id=selected_employee_id)

    lead_id_raw = (request.GET.get("lead_id") or "").strip()
    selected_lead_id = None
    if lead_id_raw.isdigit() and int(lead_id_raw) >= 1:
        selected_lead_id = int(lead_id_raw)
        qs = qs.filter(lead_id=selected_lead_id)

    outcome_code = (request.GET.get("outcome_code") or "").strip()
    if outcome_code:
        qs = qs.filter(outcome_code_snapshot=outcome_code)

    date_from_raw = (request.GET.get("date_from") or "").strip()
    date_to_raw = (request.GET.get("date_to") or "").strip()
    if date_from_raw or date_to_raw:
        date_ser = DashboardFilterSerializer(
            data={
                k: v
                for k, v in (("date_from", date_from_raw), ("date_to", date_to_raw))
                if v
            }
        )
        if date_ser.is_valid():
            period = resolve_kolkata_period(
                date_from=date_ser.validated_data.get("date_from"),
                date_to=date_ser.validated_data.get("date_to"),
            )
            qs = qs.filter(
                started_at__gte=period["start_dt"],
                started_at__lt=period["end_exclusive_dt"],
            )

    search_term = (request.GET.get("search") or "").strip()
    if search_term:
        qs = qs.filter(
            Q(lead__name__icontains=search_term)
            | Q(lead__phone__icontains=search_term)
            | Q(caller_username_snapshot__icontains=search_term)
            | Q(caller_name_snapshot__icontains=search_term)
            | Q(outcome_label_snapshot__icontains=search_term)
            | Q(employee_notes__icontains=search_term)
        )

    qs = qs.order_by("-started_at", "-id")
    page_obj = _paginate(request, qs, per_page=20)

    return render(
        request,
        "webcrm/calls/list.html",
        {
            "active_nav": "calls",
            "page_obj": page_obj,
            "employees": _all_employees(),
            "outcomes": CallOutcome.objects.order_by("display_order", "label", "id"),
            "filters": {
                "employee_id": selected_employee_id,
                "lead_id": selected_lead_id,
                "outcome_code": outcome_code,
                "date_from": date_from_raw,
                "date_to": date_to_raw,
                "search": search_term,
            },
        },
    )


@crm_admin_required
@require_GET_or_HEAD
def call_detail_view(request, pk: int):
    """GET /crm/calls/<pk>/ — Read-only detail for a single historical call record."""
    call = get_object_or_404(Call.objects.select_related("lead", "outcome"), pk=pk)
    follow_ups = list(
        FollowUp.objects.filter(call=call)
        .select_related("lead", "owner")
        .order_by("scheduled_at", "id")
    )
    return render(
        request,
        "webcrm/calls/detail.html",
        {
            "active_nav": "calls",
            "call": call,
            "follow_ups": follow_ups,
        },
    )


@crm_admin_required
def call_outcome_list_view(request):
    """GET/POST /crm/call-outcomes/ — Manage call outcome catalog."""
    errors = None
    form_data = {}
    if request.method == "POST":
        order_raw = (request.POST.get("display_order") or "0").strip()
        form_data = {
            "code": (request.POST.get("code") or "").strip(),
            "label": (request.POST.get("label") or "").strip(),
            "display_order": int(order_raw) if order_raw.isdigit() else order_raw,
            "is_active": bool(request.POST.get("is_active")),
        }
        serializer = CallOutcomeCreateSerializer(data=form_data)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    outcome = CallOutcome.objects.create(**serializer.validated_data)
                messages.success(
                    request,
                    f"Call outcome '{outcome.code}' created.",
                )
                return redirect("webcrm:call-outcome-list")
            except IntegrityError:
                errors = "An outcome with this code already exists."
        else:
            errors = _format_drf_error(DRFValidationError(serializer.errors))

    outcomes = CallOutcome.objects.order_by("display_order", "label", "id")
    return render(
        request,
        "webcrm/calls/outcomes.html",
        {
            "active_nav": "outcomes",
            "outcomes": outcomes,
            "form_data": form_data,
            "errors": errors,
        },
        status=400 if errors else 200,
    )


@crm_admin_required
@require_POST_only
def call_outcome_update_view(request, pk: int):
    """POST /crm/call-outcomes/<pk>/update/ — Update label, display_order, or is_active (code is immutable)."""
    payload = {}
    if "code" in request.POST:
        payload["code"] = request.POST["code"]
    if "label" in request.POST:
        payload["label"] = request.POST["label"].strip()
    if "display_order" in request.POST:
        raw_ord = request.POST["display_order"].strip()
        payload["display_order"] = int(raw_ord) if raw_ord.isdigit() else raw_ord
    if "is_active" in request.POST:
        payload["is_active"] = request.POST["is_active"].strip().lower() in {"true", "1", "on", "yes"}

    serializer = CallOutcomeUpdateSerializer(data=payload, partial=True)
    if not serializer.is_valid():
        err_text = _format_drf_error(DRFValidationError(serializer.errors))
        messages.error(request, err_text)
        return HttpResponseBadRequest(err_text)

    with transaction.atomic():
        outcome = get_object_or_404(CallOutcome.objects.select_for_update(), pk=pk)
        for field, value in serializer.validated_data.items():
            setattr(outcome, field, value)
        if serializer.validated_data:
            outcome.save(update_fields=[*serializer.validated_data.keys(), "updated_at"])

    messages.success(request, f"Call outcome '{outcome.code}' updated.")
    return redirect("webcrm:call-outcome-list")


# ─── 6. Follow-up Management Views ────────────────────────────────────────────

@crm_admin_required
def followup_list_view(request):
    """GET /crm/follow-ups/ — Organization-wide follow-up list with due_state & status views."""
    period = resolve_kolkata_period()
    today_midnight = period["today_midnight"]
    tomorrow_midnight = period["tomorrow_midnight"]

    qs = FollowUp.objects.select_related("lead", "owner", "call")

    due_state = (request.GET.get("due_state") or "").strip().lower()
    status_filter = (request.GET.get("status") or "").strip().upper()

    if due_state in {"today", "overdue", "upcoming"}:
        qs = qs.filter(status=FollowUp.Status.PENDING)
        if due_state == "today":
            qs = qs.filter(
                scheduled_at__gte=today_midnight,
                scheduled_at__lt=tomorrow_midnight,
            )
        elif due_state == "overdue":
            qs = qs.filter(scheduled_at__lt=today_midnight)
        elif due_state == "upcoming":
            qs = qs.filter(scheduled_at__gte=tomorrow_midnight)
    else:
        due_state = ""

    if status_filter in FollowUp.Status.values:
        qs = qs.filter(status=status_filter)
    else:
        status_filter = ""

    employee_id_raw = (request.GET.get("employee_id") or "").strip()
    selected_employee_id = None
    if employee_id_raw.isdigit() and int(employee_id_raw) >= 1:
        selected_employee_id = int(employee_id_raw)
        qs = qs.filter(owner_id=selected_employee_id)

    lead_id_raw = (request.GET.get("lead_id") or "").strip()
    selected_lead_id = None
    if lead_id_raw.isdigit() and int(lead_id_raw) >= 1:
        selected_lead_id = int(lead_id_raw)
        qs = qs.filter(lead_id=selected_lead_id)

    date_from_raw = (request.GET.get("date_from") or "").strip()
    date_to_raw = (request.GET.get("date_to") or "").strip()
    if date_from_raw or date_to_raw:
        date_ser = DashboardFilterSerializer(
            data={
                k: v
                for k, v in (("date_from", date_from_raw), ("date_to", date_to_raw))
                if v
            }
        )
        if date_ser.is_valid():
            sched_period = resolve_kolkata_period(
                date_from=date_ser.validated_data.get("date_from"),
                date_to=date_ser.validated_data.get("date_to"),
            )
            qs = qs.filter(
                scheduled_at__gte=sched_period["start_dt"],
                scheduled_at__lt=sched_period["end_exclusive_dt"],
            )

    qs = qs.order_by("scheduled_at", "id")
    page_obj = _paginate(request, qs, per_page=20)

    return render(
        request,
        "webcrm/followups/list.html",
        {
            "active_nav": "followups",
            "page_obj": page_obj,
            "employees": _all_employees(),
            "followup_statuses": FollowUp.Status.choices,
            "today_midnight": today_midnight,
            "tomorrow_midnight": tomorrow_midnight,
            "filters": {
                "due_state": due_state,
                "status": status_filter,
                "employee_id": selected_employee_id,
                "lead_id": selected_lead_id,
                "date_from": date_from_raw,
                "date_to": date_to_raw,
            },
        },
    )


@crm_admin_required
def followup_detail_view(request, pk: int):
    """GET /crm/follow-ups/<pk>/ — Follow-up detail, audit event history, and inactive-owner cancellation."""
    followup = get_object_or_404(
        FollowUp.objects.select_related("lead", "owner", "call"),
        pk=pk,
    )
    events = list(
        FollowUpEvent.objects.filter(follow_up=followup)
        .select_related("actor")
        .order_by("created_at", "id")
    )
    can_admin_cancel = (
        followup.status == FollowUp.Status.PENDING
        and not followup.owner.is_active
    )
    return render(
        request,
        "webcrm/followups/detail.html",
        {
            "active_nav": "followups",
            "followup": followup,
            "events": events,
            "can_admin_cancel": can_admin_cancel,
        },
    )


@crm_admin_required
@require_POST_only
def followup_cancel_view(request, pk: int):
    """POST /crm/follow-ups/<pk>/cancel/ — Admin cancels a pending follow-up for an inactive employee."""
    reason = request.POST.get("reason", "")
    try:
        cancel_follow_up(
            follow_up_id=pk,
            actor=request.user,
            reason=reason,
        )
    except NotFound:
        raise Http404("Follow-up not found.") from None
    except DRFPermissionDenied as exc:
        raise DjangoPermissionDenied(_format_drf_error(exc)) from None
    except (DRFValidationError, FollowUpConflict, APIException) as exc:
        err_text = _format_drf_error(exc)
        messages.error(request, err_text)
        followup = get_object_or_404(
            FollowUp.objects.select_related("lead", "owner", "call"),
            pk=pk,
        )
        events = list(
            FollowUpEvent.objects.filter(follow_up=followup)
            .select_related("actor")
            .order_by("created_at", "id")
        )
        return render(
            request,
            "webcrm/followups/detail.html",
            {
                "active_nav": "followups",
                "followup": followup,
                "events": events,
                "can_admin_cancel": (
                    followup.status == FollowUp.Status.PENDING
                    and not followup.owner.is_active
                ),
                "cancel_error": err_text,
            },
            status=getattr(exc, "status_code", 400),
        )

    messages.success(request, "Follow-up cancelled and audit event recorded.")
    return redirect("webcrm:followup-detail", pk=pk)


# ─── 7. Reports & Export Views ────────────────────────────────────────────────

@crm_admin_required
def reports_hub_view(request):
    """GET /crm/reports/ — Interactive reporting center & export launcher."""
    report_type = (request.GET.get("report_type") or "employee_performance").strip()
    if report_type not in {"employee_performance", "calls", "leads", "followups"}:
        report_type = "employee_performance"

    allowed_for_type = ReportExportFilterSerializer.allowed_by_report_type[report_type]
    raw_filters = {"report_type": report_type}
    for key in allowed_for_type:
        if key in {"report_type", "export_format"}:
            continue
        val = (request.GET.get(key) or "").strip()
        if val:
            raw_filters[key] = val

    filter_errors = None
    preview_headers = []
    preview_rows = []
    total_matching_rows = 0

    serializer = ReportExportFilterSerializer(data=raw_filters)
    if serializer.is_valid():
        filters = dict(serializer.validated_data)
        filters.pop("report_type", None)
        filters.pop("export_format", None)
        try:
            headers, rows, _ = build_export_dataset(
                report_type=report_type,
                filters=filters,
            )
            preview_headers = headers
            total_matching_rows = len(rows)
            preview_rows = rows[:50]
        except DRFValidationError as exc:
            filter_errors = _format_drf_error(exc)
    else:
        filter_errors = _format_drf_error(DRFValidationError(serializer.errors))

    period = resolve_kolkata_period(
        date_from=serializer.validated_data.get("date_from") if serializer.is_valid() else None,
        date_to=serializer.validated_data.get("date_to") if serializer.is_valid() else None,
    )

    return render(
        request,
        "webcrm/reports/index.html",
        {
            "active_nav": "reports",
            "report_type": report_type,
            "period": period,
            "employees": _all_employees(),
            "outcomes": CallOutcome.objects.order_by("display_order", "label", "id"),
            "lead_statuses": Lead.Status.choices,
            "followup_statuses": FollowUp.Status.choices,
            "preview_headers": preview_headers,
            "preview_rows": preview_rows,
            "total_matching_rows": total_matching_rows,
            "filter_errors": filter_errors,
            "query_params": request.GET,
        },
        status=400 if filter_errors else 200,
    )


@crm_admin_required
def reports_export_view(request):
    """GET /crm/reports/export/ — Download CSV or XLSX report using apps.reports services."""
    allowed_fields = ReportExportFilterSerializer.allowed_fields
    unknown = set(request.GET) - allowed_fields
    if unknown:
        return HttpResponseBadRequest(
            f"Unsupported filter parameters: {', '.join(sorted(unknown))}."
        )
    repeated = [
        name for name in request.GET if len(request.GET.getlist(name)) > 1
    ]
    if repeated:
        return HttpResponseBadRequest(
            f"Provide each filter parameter only once: {', '.join(sorted(repeated))}."
        )

    raw_filters = {
        name: request.GET[name]
        for name in allowed_fields
        if name in request.GET and request.GET[name] != ""
    }
    serializer = ReportExportFilterSerializer(data=raw_filters)
    if not serializer.is_valid():
        return HttpResponseBadRequest(
            _format_drf_error(DRFValidationError(serializer.errors))
        )

    filters = dict(serializer.validated_data)
    report_type = filters.pop("report_type")
    export_format = filters.pop("export_format", "csv")

    try:
        headers, rows, filename_base = build_export_dataset(
            report_type=report_type,
            filters=filters,
        )
    except DRFValidationError as exc:
        return HttpResponseBadRequest(_format_drf_error(exc))

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
