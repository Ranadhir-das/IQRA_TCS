from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import (
    _validate_idempotency_key,
    cancel_follow_up,
    complete_follow_up,
    create_follow_up,
    reschedule_follow_up,
)

from .permissions import (
    IsActiveFollowUpAdmin,
    IsActiveFollowUpEmployee,
    IsActiveFollowUpUser,
)
from .serializers import (
    AdminFollowUpSerializer,
    EmployeeFollowUpSerializer,
    FollowUpCancelSerializer,
    FollowUpCompleteSerializer,
    FollowUpCreateSerializer,
    FollowUpEventSerializer,
    FollowUpFilterSerializer,
    FollowUpPagination,
    FollowUpRescheduleSerializer,
)


KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def _require_idempotency_key(request):
    raw_key = request.headers.get("Idempotency-Key")
    if raw_key is None or not str(raw_key).strip():
        raise ValidationError(
            {"Idempotency-Key": "The Idempotency-Key header is required."}
        )
    return _validate_idempotency_key(raw_key)


def _kolkata_day_bounds(local_date):
    start_dt = datetime.combine(local_date, time.min, tzinfo=KOLKATA_TZ)
    end_exclusive_dt = datetime.combine(
        local_date + timedelta(days=1),
        time.min,
        tzinfo=KOLKATA_TZ,
    )
    return start_dt, end_exclusive_dt


class FollowUpListCreateView(generics.ListAPIView):
    """Follow-up collection endpoint: employee creation and scoped list."""

    pagination_class = FollowUpPagination
    permission_classes = [IsAuthenticated, IsActiveFollowUpUser]

    allowed_query_params = frozenset(
        {
            "status",
            "lead_id",
            "scheduled_from",
            "scheduled_to",
            "due_state",
            "employee_id",
            "page",
            "page_size",
            "format",
        }
    )
    filter_params = frozenset(
        {
            "status",
            "lead_id",
            "scheduled_from",
            "scheduled_to",
            "due_state",
            "employee_id",
        }
    )

    def get_permissions(self):
        permission_classes = (
            [IsAuthenticated, IsActiveFollowUpUser]
            if self.request.method in {"GET", "HEAD", "OPTIONS"}
            else [IsAuthenticated, IsActiveFollowUpEmployee]
        )
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.request.user.is_admin:
            return AdminFollowUpSerializer
        return EmployeeFollowUpSerializer

    def get_filters(self):
        query_params = self.request.query_params
        unknown = set(query_params) - self.allowed_query_params
        if unknown:
            raise ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        repeated = [
            name for name in query_params if len(query_params.getlist(name)) > 1
        ]
        if repeated:
            raise ValidationError(
                {name: "Provide each filter only once." for name in sorted(repeated)}
            )
        data = {
            name: query_params[name]
            for name in self.filter_params
            if name in query_params
        }
        serializer = FollowUpFilterSerializer(
            data=data,
            context={"request": self.request},
        )
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_queryset(self) -> QuerySet[FollowUp]:
        filters = self.get_filters()
        user = self.request.user
        queryset = FollowUp.objects.all()

        if not user.is_admin:
            queryset = queryset.filter(owner=user)
        elif "employee_id" in filters:
            queryset = queryset.filter(owner_id=filters["employee_id"])

        if "status" in filters:
            queryset = queryset.filter(status=filters["status"])
        if "lead_id" in filters:
            queryset = queryset.filter(lead_id=filters["lead_id"])
        if "scheduled_from" in filters:
            start_dt, _ = _kolkata_day_bounds(filters["scheduled_from"])
            queryset = queryset.filter(scheduled_at__gte=start_dt)
        if "scheduled_to" in filters:
            _, end_exclusive_dt = _kolkata_day_bounds(filters["scheduled_to"])
            queryset = queryset.filter(scheduled_at__lt=end_exclusive_dt)
        if "due_state" in filters:
            today_local = timezone.now().astimezone(KOLKATA_TZ).date()
            today_midnight, tomorrow_midnight = _kolkata_day_bounds(today_local)
            queryset = queryset.filter(status=FollowUp.Status.PENDING)
            due_state = filters["due_state"]
            if due_state == FollowUpFilterSerializer.DUE_STATE_TODAY:
                queryset = queryset.filter(
                    scheduled_at__gte=today_midnight,
                    scheduled_at__lt=tomorrow_midnight,
                )
            elif due_state == FollowUpFilterSerializer.DUE_STATE_OVERDUE:
                queryset = queryset.filter(scheduled_at__lt=today_midnight)
            elif due_state == FollowUpFilterSerializer.DUE_STATE_UPCOMING:
                queryset = queryset.filter(scheduled_at__gte=tomorrow_midnight)

        return queryset.select_related("lead", "owner", "call").order_by(
            "scheduled_at",
            "id",
        )

    def post(self, request):
        idempotency_key = _require_idempotency_key(request)
        serializer = FollowUpCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        follow_up = create_follow_up(
            actor=request.user,
            idempotency_key=idempotency_key,
            **serializer.validated_data,
        )
        created = getattr(follow_up, "_created", True)
        return Response(
            EmployeeFollowUpSerializer(follow_up).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class FollowUpDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsActiveFollowUpUser]
    lookup_url_kwarg = "pk"

    def get_serializer_class(self):
        if self.request.user.is_admin:
            return AdminFollowUpSerializer
        return EmployeeFollowUpSerializer

    def get_queryset(self) -> QuerySet[FollowUp]:
        queryset = FollowUp.objects.all()
        if not self.request.user.is_admin:
            queryset = queryset.filter(owner=self.request.user)
        return queryset.select_related("lead", "owner", "call").order_by(
            "scheduled_at",
            "id",
        )


class FollowUpRescheduleView(APIView):
    permission_classes = [IsAuthenticated, IsActiveFollowUpEmployee]

    def post(self, request, pk):
        idempotency_key = _require_idempotency_key(request)
        serializer = FollowUpRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        follow_up = reschedule_follow_up(
            follow_up_id=pk,
            actor=request.user,
            idempotency_key=idempotency_key,
            **serializer.validated_data,
        )
        return Response(
            EmployeeFollowUpSerializer(follow_up).data,
            status=status.HTTP_200_OK,
        )


class FollowUpCompleteView(APIView):
    permission_classes = [IsAuthenticated, IsActiveFollowUpEmployee]

    def post(self, request, pk):
        serializer = FollowUpCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        follow_up = complete_follow_up(
            follow_up_id=pk,
            actor=request.user,
        )
        return Response(
            EmployeeFollowUpSerializer(follow_up).data,
            status=status.HTTP_200_OK,
        )


class FollowUpCancelView(APIView):
    permission_classes = [IsAuthenticated, IsActiveFollowUpUser]

    def post(self, request, pk):
        serializer = FollowUpCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        follow_up = cancel_follow_up(
            follow_up_id=pk,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        serializer_class = (
            AdminFollowUpSerializer
            if request.user.is_admin
            else EmployeeFollowUpSerializer
        )
        return Response(
            serializer_class(follow_up).data,
            status=status.HTTP_200_OK,
        )


class FollowUpEventListView(generics.ListAPIView):
    """Administrator-only chronological audit history for a follow-up."""

    serializer_class = FollowUpEventSerializer
    pagination_class = FollowUpPagination
    permission_classes = [IsAuthenticated, IsActiveFollowUpAdmin]
    allowed_query_params = frozenset({"page", "page_size", "format"})

    def _validate_query_params(self):
        query_params = self.request.query_params
        unknown = set(query_params) - self.allowed_query_params
        if unknown:
            raise ValidationError(
                {field: "This filter is not supported." for field in sorted(unknown)}
            )
        repeated = [
            name for name in query_params if len(query_params.getlist(name)) > 1
        ]
        if repeated:
            raise ValidationError(
                {name: "Provide each filter only once." for name in sorted(repeated)}
            )

    def get_queryset(self) -> QuerySet[FollowUpEvent]:
        self._validate_query_params()
        follow_up_id = self.kwargs["pk"]
        if not FollowUp.objects.filter(pk=follow_up_id).exists():
            raise NotFound("Follow-up not found.")
        return FollowUpEvent.objects.filter(follow_up_id=follow_up_id).order_by(
            "created_at",
            "id",
        )
