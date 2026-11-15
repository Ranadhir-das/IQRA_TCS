"""PostgreSQL aggregation and export dataset builders for IQRA TCS reporting."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import (
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAssignmentHistory

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def kolkata_day_bounds(local_date: date) -> tuple[datetime, datetime]:
    """Return half-open [start_dt, end_exclusive_dt) bounds for one Asia/Kolkata calendar date."""
    start_dt = datetime.combine(local_date, time.min, tzinfo=KOLKATA_TZ)
    end_exclusive_dt = datetime.combine(
        local_date + timedelta(days=1),
        time.min,
        tzinfo=KOLKATA_TZ,
    )
    return start_dt, end_exclusive_dt


def resolve_kolkata_period(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Resolve optional date_from/date_to into Asia/Kolkata half-open timestamp bounds.

    - If both dates are omitted: last 30 local calendar days inclusive (today and the preceding 29 days).
    - If only one date is supplied: use that date for both boundaries.
    """
    today_local = timezone.now().astimezone(KOLKATA_TZ).date()
    default_days = int(getattr(settings, "REPORT_DEFAULT_DATE_RANGE_DAYS", 30))

    if date_from is None and date_to is None:
        resolved_to = today_local
        resolved_from = today_local - timedelta(days=default_days - 1)
    elif date_from is not None and date_to is None:
        resolved_from = date_from
        resolved_to = date_from
    elif date_from is None and date_to is not None:
        resolved_from = date_to
        resolved_to = date_to
    else:
        resolved_from = date_from
        resolved_to = date_to

    start_dt, _ = kolkata_day_bounds(resolved_from)
    _, end_exclusive_dt = kolkata_day_bounds(resolved_to)
    today_midnight, tomorrow_midnight = kolkata_day_bounds(today_local)

    return {
        "timezone": "Asia/Kolkata",
        "date_from": resolved_from,
        "date_to": resolved_to,
        "start_dt": start_dt,
        "end_exclusive_dt": end_exclusive_dt,
        "today_local": today_local,
        "today_midnight": today_midnight,
        "tomorrow_midnight": tomorrow_midnight,
    }


def format_period_metadata(period: dict, *, employee_id: int | None = None) -> dict:
    """Serialize resolved period dictionary for API responses."""
    return {
        "timezone": period["timezone"],
        "date_from": period["date_from"].isoformat(),
        "date_to": period["date_to"].isoformat(),
        "start_at": period["start_dt"].isoformat(),
        "end_exclusive_at": period["end_exclusive_dt"].isoformat(),
        "employee_id": employee_id,
    }


def build_dashboard_summary(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    employee_id: int | None = None,
) -> dict:
    """Compute the complete Admin Dashboard Summary using bounded PostgreSQL queries."""
    period = resolve_kolkata_period(date_from=date_from, date_to=date_to)
    start_dt = period["start_dt"]
    end_exclusive_dt = period["end_exclusive_dt"]
    today_midnight = period["today_midnight"]
    tomorrow_midnight = period["tomorrow_midnight"]

    # ── A. Leads (1 aggregate query) ─────────────────────────────────────────
    lead_period_q = Q(created_at__gte=start_dt, created_at__lt=end_exclusive_dt)
    inactive_emp_owner_q = Q(
        assigned_to__role=User.Role.EMPLOYEE,
        assigned_to__is_superuser=False,
        assigned_to__is_active=False,
    )
    lead_agg_kwargs = {
        "total_active": Count("id", filter=Q(is_archived=False)),
        "total_archived": Count("id", filter=Q(is_archived=True)),
        "assigned_active": Count(
            "id",
            filter=Q(is_archived=False, assigned_to__isnull=False),
        ),
        "unassigned_active": Count(
            "id",
            filter=Q(is_archived=False, assigned_to__isnull=True),
        ),
        "created_in_period": Count("id", filter=lead_period_q),
        "active_leads_with_inactive_owner_count": Count(
            "id",
            filter=Q(is_archived=False) & inactive_emp_owner_q,
        ),
    }
    for status_code in Lead.Status.values:
        lead_agg_kwargs[f"active_status_{status_code}"] = Count(
            "id",
            filter=Q(is_archived=False, status=status_code),
        )
        lead_agg_kwargs[f"period_status_{status_code}"] = Count(
            "id",
            filter=lead_period_q & Q(status=status_code),
        )

    if employee_id is not None:
        emp_q = Q(assigned_to_id=employee_id)
        lead_agg_kwargs["emp_assigned_active"] = Count(
            "id",
            filter=emp_q & Q(is_archived=False),
        )
        lead_agg_kwargs["emp_assigned_archived"] = Count(
            "id",
            filter=emp_q & Q(is_archived=True),
        )
        lead_agg_kwargs["emp_created_in_period"] = Count(
            "id",
            filter=emp_q & lead_period_q,
        )
        for status_code in Lead.Status.values:
            lead_agg_kwargs[f"emp_active_status_{status_code}"] = Count(
                "id",
                filter=emp_q & Q(is_archived=False, status=status_code),
            )

    lead_totals = Lead.objects.aggregate(**lead_agg_kwargs)

    current_active_by_status = {
        status_code: lead_totals[f"active_status_{status_code}"]
        for status_code in Lead.Status.values
    }
    created_in_period_by_current_status = {
        status_code: lead_totals[f"period_status_{status_code}"]
        for status_code in Lead.Status.values
    }

    filtered_employee_leads = None
    if employee_id is not None:
        filtered_employee_leads = {
            "employee_id": employee_id,
            "assigned_active": lead_totals["emp_assigned_active"],
            "assigned_archived": lead_totals["emp_assigned_archived"],
            "created_in_period_assigned_to_employee": lead_totals[
                "emp_created_in_period"
            ],
            "current_active_by_status": {
                status_code: lead_totals[f"emp_active_status_{status_code}"]
                for status_code in Lead.Status.values
            },
        }

    leads_section = {
        "total_active": lead_totals["total_active"],
        "total_archived": lead_totals["total_archived"],
        "assigned_active": lead_totals["assigned_active"],
        "unassigned_active": lead_totals["unassigned_active"],
        "created_in_period": lead_totals["created_in_period"],
        "current_active_by_status": current_active_by_status,
        "created_in_period_by_current_status": created_in_period_by_current_status,
        "filtered_employee_leads": filtered_employee_leads,
    }

    # ── B. Calls (3 bounded queries: summary, outcome breakdown, daily trends) ─
    calls_qs = Call.objects.filter(
        started_at__gte=start_dt,
        started_at__lt=end_exclusive_dt,
    )
    if employee_id is not None:
        calls_qs = calls_qs.filter(caller_id=employee_id)

    call_summary = calls_qs.aggregate(
        total_calls=Count("id"),
        unique_leads_called=Count("lead_id", distinct=True),
        total_duration_seconds=Coalesce(Sum("duration_seconds"), 0),
    )
    total_calls = call_summary["total_calls"]
    total_duration = call_summary["total_duration_seconds"]
    avg_duration = (
        round(total_duration / total_calls, 2) if total_calls > 0 else 0.0
    )

    outcome_rows = list(
        calls_qs.values("outcome_code_snapshot", "outcome_label_snapshot")
        .annotate(count=Count("id"))
        .order_by("-count", "outcome_code_snapshot", "outcome_label_snapshot")
    )
    by_outcome = [
        {
            "outcome_code": row["outcome_code_snapshot"],
            "outcome_label": row["outcome_label_snapshot"],
            "count": row["count"],
        }
        for row in outcome_rows
    ]

    trend_rows = list(
        calls_qs.annotate(local_date=TruncDate("started_at", tzinfo=KOLKATA_TZ))
        .values("local_date")
        .annotate(
            total_calls=Count("id"),
            unique_leads_called=Count("lead_id", distinct=True),
            total_duration_seconds=Coalesce(Sum("duration_seconds"), 0),
        )
        .order_by("local_date")
    )
    daily_trends = [
        {
            "date": row["local_date"].isoformat(),
            "total_calls": row["total_calls"],
            "unique_leads_called": row["unique_leads_called"],
            "total_duration_seconds": row["total_duration_seconds"],
        }
        for row in trend_rows
    ]

    calls_section = {
        "total_calls": total_calls,
        "unique_leads_called": call_summary["unique_leads_called"],
        "total_duration_seconds": total_duration,
        "average_duration_seconds": avg_duration,
        "by_outcome": by_outcome,
        "daily_trends": daily_trends,
    }

    # ── C. Follow-ups (1 aggregate query) ────────────────────────────────────
    emp_followup_filter = (
        Q(owner_id=employee_id) if employee_id is not None else Q()
    )
    pending_q = emp_followup_filter & Q(status=FollowUp.Status.PENDING)
    inactive_followup_owner_q = Q(
        owner__role=User.Role.EMPLOYEE,
        owner__is_superuser=False,
        owner__is_active=False,
    )
    followup_totals = FollowUp.objects.aggregate(
        pending_total=Count("id", filter=pending_q),
        overdue=Count(
            "id",
            filter=pending_q & Q(scheduled_at__lt=today_midnight),
        ),
        due_today=Count(
            "id",
            filter=pending_q
            & Q(
                scheduled_at__gte=today_midnight,
                scheduled_at__lt=tomorrow_midnight,
            ),
        ),
        upcoming=Count(
            "id",
            filter=pending_q & Q(scheduled_at__gte=tomorrow_midnight),
        ),
        created_in_period=Count(
            "id",
            filter=emp_followup_filter
            & Q(
                created_at__gte=start_dt,
                created_at__lt=end_exclusive_dt,
            ),
        ),
        completed_in_period=Count(
            "id",
            filter=emp_followup_filter
            & Q(
                status=FollowUp.Status.COMPLETED,
                completed_at__gte=start_dt,
                completed_at__lt=end_exclusive_dt,
            ),
        ),
        cancelled_in_period=Count(
            "id",
            filter=emp_followup_filter
            & Q(
                status=FollowUp.Status.CANCELLED,
                cancelled_at__gte=start_dt,
                cancelled_at__lt=end_exclusive_dt,
            ),
        ),
        org_pending_with_inactive_owner=Count(
            "id",
            filter=Q(status=FollowUp.Status.PENDING) & inactive_followup_owner_q,
        ),
    )

    follow_ups_section = {
        "pending_total": followup_totals["pending_total"],
        "overdue": followup_totals["overdue"],
        "due_today": followup_totals["due_today"],
        "upcoming": followup_totals["upcoming"],
        "created_in_period": followup_totals["created_in_period"],
        "completed_in_period": followup_totals["completed_in_period"],
        "cancelled_in_period": followup_totals["cancelled_in_period"],
    }

    # ── D. Workload & Assignment Statistics (2 aggregate queries) ────────────
    has_active_lead_subq = Lead.objects.filter(
        assigned_to=OuterRef("pk"),
        is_archived=False,
    )
    has_pending_followup_subq = FollowUp.objects.filter(
        owner=OuterRef("pk"),
        status=FollowUp.Status.PENDING,
    )
    employee_totals = (
        User.objects.filter(role=User.Role.EMPLOYEE, is_superuser=False)
        .annotate(
            _has_active_lead=Exists(has_active_lead_subq),
            _has_pending_followup=Exists(has_pending_followup_subq),
        )
        .aggregate(
            total_employees=Count("id"),
            active_employees=Count("id", filter=Q(is_active=True)),
            inactive_employees=Count("id", filter=Q(is_active=False)),
            inactive_employees_with_active_leads_count=Count(
                "id",
                filter=Q(is_active=False, _has_active_lead=True),
            ),
            inactive_employees_with_pending_followups_count=Count(
                "id",
                filter=Q(is_active=False, _has_pending_followup=True),
            ),
        )
    )

    assign_period_q = Q(changed_at__gte=start_dt, changed_at__lt=end_exclusive_dt)
    assign_agg_kwargs = {
        "assignments_changed_in_period": Count("id", filter=assign_period_q),
    }
    if employee_id is not None:
        assign_agg_kwargs["emp_assignments_changed_in_period"] = Count(
            "id",
            filter=assign_period_q
            & (
                Q(previous_employee_id=employee_id)
                | Q(new_employee_id=employee_id)
            ),
        )
    assignment_totals = LeadAssignmentHistory.objects.aggregate(
        **assign_agg_kwargs
    )

    filtered_employee_workload = None
    if employee_id is not None:
        filtered_employee_workload = {
            "employee_id": employee_id,
            "assigned_active_leads_count": lead_totals["emp_assigned_active"],
            "pending_followups_count": followup_totals["pending_total"],
            "overdue_followups_count": followup_totals["overdue"],
            "assignments_changed_in_period": assignment_totals[
                "emp_assignments_changed_in_period"
            ],
        }

    workload_section = {
        "total_employees": employee_totals["total_employees"],
        "active_employees": employee_totals["active_employees"],
        "inactive_employees": employee_totals["inactive_employees"],
        "inactive_employees_with_active_leads_count": employee_totals[
            "inactive_employees_with_active_leads_count"
        ],
        "inactive_employees_with_pending_followups_count": employee_totals[
            "inactive_employees_with_pending_followups_count"
        ],
        "active_leads_with_inactive_owner_count": lead_totals[
            "active_leads_with_inactive_owner_count"
        ],
        "pending_followups_with_inactive_owner_count": followup_totals[
            "org_pending_with_inactive_owner"
        ],
        "assignments_changed_in_period": assignment_totals[
            "assignments_changed_in_period"
        ],
        "filtered_employee_workload": filtered_employee_workload,
    }

    return {
        "period": format_period_metadata(period, employee_id=employee_id),
        "leads": leads_section,
        "calls": calls_section,
        "follow_ups": follow_ups_section,
        "workload": workload_section,
    }


def build_employee_performance_queryset(
    *,
    start_dt: datetime,
    end_exclusive_dt: datetime,
    today_midnight: datetime,
    employee_id: int | None = None,
    is_active: bool | None = None,
) -> QuerySet[User]:
    """Return a non-superuser EMPLOYEE queryset annotated with current workload and period metrics."""
    qs = User.objects.filter(role=User.Role.EMPLOYEE, is_superuser=False)
    if employee_id is not None:
        qs = qs.filter(pk=employee_id)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)

    active_leads_subq = (
        Lead.objects.filter(assigned_to=OuterRef("pk"), is_archived=False)
        .values("assigned_to")
        .annotate(c=Count("pk"))
        .values("c")[:1]
    )
    converted_leads_subq = (
        Lead.objects.filter(
            assigned_to=OuterRef("pk"),
            is_archived=False,
            status=Lead.Status.CONVERTED,
        )
        .values("assigned_to")
        .annotate(c=Count("pk"))
        .values("c")[:1]
    )
    pending_followups_subq = (
        FollowUp.objects.filter(
            owner=OuterRef("pk"),
            status=FollowUp.Status.PENDING,
        )
        .values("owner")
        .annotate(c=Count("pk"))
        .values("c")[:1]
    )
    overdue_followups_subq = (
        FollowUp.objects.filter(
            owner=OuterRef("pk"),
            status=FollowUp.Status.PENDING,
            scheduled_at__lt=today_midnight,
        )
        .values("owner")
        .annotate(c=Count("pk"))
        .values("c")[:1]
    )
    calls_in_period_base = Call.objects.filter(
        caller=OuterRef("pk"),
        started_at__gte=start_dt,
        started_at__lt=end_exclusive_dt,
    ).values("caller")

    calls_count_subq = calls_in_period_base.annotate(c=Count("pk")).values("c")[:1]
    unique_leads_called_subq = calls_in_period_base.annotate(
        c=Count("lead_id", distinct=True)
    ).values("c")[:1]
    total_call_duration_subq = calls_in_period_base.annotate(
        s=Sum("duration_seconds")
    ).values("s")[:1]

    followups_completed_subq = (
        FollowUp.objects.filter(
            owner=OuterRef("pk"),
            status=FollowUp.Status.COMPLETED,
            completed_at__gte=start_dt,
            completed_at__lt=end_exclusive_dt,
        )
        .values("owner")
        .annotate(c=Count("pk"))
        .values("c")[:1]
    )
    followups_cancelled_subq = (
        FollowUp.objects.filter(
            owner=OuterRef("pk"),
            status=FollowUp.Status.CANCELLED,
            cancelled_at__gte=start_dt,
            cancelled_at__lt=end_exclusive_dt,
        )
        .values("owner")
        .annotate(c=Count("pk"))
        .values("c")[:1]
    )

    return qs.annotate(
        assigned_active_leads_count=Coalesce(
            Subquery(active_leads_subq, output_field=IntegerField()),
            0,
        ),
        converted_active_leads_count=Coalesce(
            Subquery(converted_leads_subq, output_field=IntegerField()),
            0,
        ),
        pending_followups_count=Coalesce(
            Subquery(pending_followups_subq, output_field=IntegerField()),
            0,
        ),
        overdue_followups_count=Coalesce(
            Subquery(overdue_followups_subq, output_field=IntegerField()),
            0,
        ),
        calls_count=Coalesce(
            Subquery(calls_count_subq, output_field=IntegerField()),
            0,
        ),
        unique_leads_called_count=Coalesce(
            Subquery(unique_leads_called_subq, output_field=IntegerField()),
            0,
        ),
        total_call_duration_seconds=Coalesce(
            Subquery(total_call_duration_subq, output_field=IntegerField()),
            0,
        ),
        followups_completed_count=Coalesce(
            Subquery(followups_completed_subq, output_field=IntegerField()),
            0,
        ),
        followups_cancelled_count=Coalesce(
            Subquery(followups_cancelled_subq, output_field=IntegerField()),
            0,
        ),
    ).order_by("-is_active", "username", "id")


def _enforce_export_row_limit(queryset: QuerySet) -> int:
    max_rows = int(getattr(settings, "REPORT_EXPORT_MAX_ROWS", 10000))
    total_rows = queryset.count()
    if total_rows > max_rows:
        raise ValidationError(
            {
                "detail": (
                    f"Export exceeds the maximum limit of {max_rows} rows "
                    f"({total_rows} matching rows). Narrow the filter criteria or date range."
                )
            }
        )
    return total_rows


def build_export_dataset(*, report_type: str, filters: dict) -> tuple[list[str], list[tuple], str]:
    """Build headers, row tuples, and base filename for the requested report_type."""
    period = resolve_kolkata_period(
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
    )
    start_dt = period["start_dt"]
    end_exclusive_dt = period["end_exclusive_dt"]
    today_midnight = period["today_midnight"]
    tomorrow_midnight = period["tomorrow_midnight"]
    date_suffix = f"{period['date_from'].isoformat()}_to_{period['date_to'].isoformat()}"

    if report_type == "employee_performance":
        qs = build_employee_performance_queryset(
            start_dt=start_dt,
            end_exclusive_dt=end_exclusive_dt,
            today_midnight=today_midnight,
            employee_id=filters.get("employee_id"),
            is_active=filters.get("is_active"),
        )
        _enforce_export_row_limit(qs)
        headers = [
            "employee_id",
            "username",
            "full_name",
            "designation",
            "is_active",
            "assigned_active_leads_count",
            "converted_active_leads_count",
            "pending_followups_count",
            "overdue_followups_count",
            "calls_count",
            "unique_leads_called_count",
            "total_call_duration_seconds",
            "average_call_duration_seconds",
            "followups_completed_count",
            "followups_cancelled_count",
            "period_date_from",
            "period_date_to",
        ]
        rows = []
        for emp in qs.iterator(chunk_size=1000):
            avg_dur = (
                round(emp.total_call_duration_seconds / emp.calls_count, 2)
                if emp.calls_count > 0
                else 0.0
            )
            rows.append(
                (
                    emp.pk,
                    emp.username,
                    emp.get_full_name(),
                    emp.designation,
                    emp.is_active,
                    emp.assigned_active_leads_count,
                    emp.converted_active_leads_count,
                    emp.pending_followups_count,
                    emp.overdue_followups_count,
                    emp.calls_count,
                    emp.unique_leads_called_count,
                    emp.total_call_duration_seconds,
                    avg_dur,
                    emp.followups_completed_count,
                    emp.followups_cancelled_count,
                    period["date_from"].isoformat(),
                    period["date_to"].isoformat(),
                )
            )
        return headers, rows, f"iqra_tcs_employee_performance_{date_suffix}"

    if report_type == "calls":
        qs = Call.objects.filter(
            started_at__gte=start_dt,
            started_at__lt=end_exclusive_dt,
        )
        if filters.get("employee_id") is not None:
            qs = qs.filter(caller_id=filters["employee_id"])
        if filters.get("lead_id") is not None:
            qs = qs.filter(lead_id=filters["lead_id"])
        if filters.get("outcome_code"):
            qs = qs.filter(outcome_code_snapshot=filters["outcome_code"])

        qs = qs.order_by("-started_at", "-id")
        _enforce_export_row_limit(qs)
        headers = [
            "call_id",
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
        value_rows = qs.values_list(
            "id",
            "lead_id",
            "lead__name",
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
        ).iterator(chunk_size=1000)
        return headers, list(value_rows), f"iqra_tcs_calls_{date_suffix}"

    if report_type == "leads":
        qs = Lead.objects.all()
        archived = filters.get("archived", "false")
        if archived == "true":
            qs = qs.filter(is_archived=True)
        elif archived != "all":
            qs = qs.filter(is_archived=False)

        if filters.get("date_from") is not None or filters.get("date_to") is not None:
            qs = qs.filter(
                created_at__gte=start_dt,
                created_at__lt=end_exclusive_dt,
            )
        if filters.get("employee_id") is not None:
            qs = qs.filter(assigned_to_id=filters["employee_id"])
        if filters.get("unassigned") is not None:
            qs = qs.filter(assigned_to__isnull=filters["unassigned"])
        if filters.get("status"):
            qs = qs.filter(status=filters["status"])
        search_term = filters.get("search", "")
        if search_term:
            qs = qs.filter(
                Q(name__icontains=search_term)
                | Q(phone__icontains=search_term)
                | Q(alternate_phone__icontains=search_term)
                | Q(email__icontains=search_term)
                | Q(course_interest__icontains=search_term)
            )

        qs = qs.order_by("-created_at", "-id")
        _enforce_export_row_limit(qs)
        headers = [
            "lead_id",
            "name",
            "phone",
            "alternate_phone",
            "email",
            "course_interest",
            "enquiry_details",
            "status",
            "assigned_to_id",
            "assigned_to_username",
            "assigned_to_name",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        rows = []
        for (
            lead_id,
            name,
            phone,
            alternate_phone,
            email,
            course_interest,
            enquiry_details,
            status_val,
            assigned_to_id,
            assigned_username,
            assigned_first,
            assigned_last,
            is_archived,
            created_at,
            updated_at,
        ) in qs.values_list(
            "id",
            "name",
            "phone",
            "alternate_phone",
            "email",
            "course_interest",
            "enquiry_details",
            "status",
            "assigned_to_id",
            "assigned_to__username",
            "assigned_to__first_name",
            "assigned_to__last_name",
            "is_archived",
            "created_at",
            "updated_at",
        ).iterator(chunk_size=1000):
            if assigned_to_id is not None:
                full = f"{assigned_first or ''} {assigned_last or ''}".strip()
                assigned_name = full or assigned_username or ""
            else:
                assigned_name = ""
            rows.append(
                (
                    lead_id,
                    name,
                    phone,
                    alternate_phone,
                    email,
                    course_interest,
                    enquiry_details,
                    status_val,
                    assigned_to_id,
                    assigned_username or "",
                    assigned_name,
                    is_archived,
                    created_at,
                    updated_at,
                )
            )
        return headers, rows, f"iqra_tcs_leads_{date_suffix}"

    if report_type == "followups":
        qs = FollowUp.objects.all()
        if filters.get("date_from") is not None or filters.get("date_to") is not None:
            qs = qs.filter(
                scheduled_at__gte=start_dt,
                scheduled_at__lt=end_exclusive_dt,
            )
        if filters.get("employee_id") is not None:
            qs = qs.filter(owner_id=filters["employee_id"])
        if filters.get("lead_id") is not None:
            qs = qs.filter(lead_id=filters["lead_id"])
        if filters.get("status"):
            qs = qs.filter(status=filters["status"])
        if filters.get("due_state"):
            qs = qs.filter(status=FollowUp.Status.PENDING)
            due_state = filters["due_state"]
            if due_state == "today":
                qs = qs.filter(
                    scheduled_at__gte=today_midnight,
                    scheduled_at__lt=tomorrow_midnight,
                )
            elif due_state == "overdue":
                qs = qs.filter(scheduled_at__lt=today_midnight)
            elif due_state == "upcoming":
                qs = qs.filter(scheduled_at__gte=tomorrow_midnight)

        qs = qs.order_by("scheduled_at", "id")
        _enforce_export_row_limit(qs)
        headers = [
            "followup_id",
            "lead_id",
            "lead_name",
            "call_id",
            "owner_id",
            "owner_username_snapshot",
            "owner_name_snapshot",
            "scheduled_at",
            "status",
            "notes",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]
        value_rows = qs.values_list(
            "id",
            "lead_id",
            "lead__name",
            "call_id",
            "owner_id",
            "owner_username_snapshot",
            "owner_name_snapshot",
            "scheduled_at",
            "status",
            "notes",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ).iterator(chunk_size=1000)
        return headers, list(value_rows), f"iqra_tcs_followups_{date_suffix}"

    raise ValidationError({"report_type": "Unsupported report_type."})
