from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calls.models import Call, CallOutcome
from apps.leads.api.serializers import LeadPagination

from apps.calls.services import submit_call

from .permissions import (
    IsActiveCallAdmin,
    IsActiveCallHistoryUser,
    IsCallSubmitter,
)
from .serializers import (
    AdminCallHistorySerializer,
    CallHistoryFilterSerializer,
    CallHistorySerializer,
    CallOutcomeCreateSerializer,
    CallOutcomeSerializer,
    CallOutcomeUpdateSerializer,
    CallSubmissionResponseSerializer,
    CallSubmissionSerializer,
)


class CallSubmissionView(generics.ListAPIView):
    """Call collection endpoint: employee submission plus scoped history list."""

    serializer_class = CallHistorySerializer
    pagination_class = LeadPagination
    permission_classes = [IsAuthenticated, IsCallSubmitter]

    allowed_query_params = frozenset(
        {
            "lead_id",
            "outcome_code",
            "started_at_from",
            "started_at_to",
            "caller_id",
            "page",
            "page_size",
            "format",
        }
    )
    filter_params = frozenset(
        {"lead_id", "outcome_code", "started_at_from", "started_at_to", "caller_id"}
    )

    def get_permissions(self):
        permission_classes = (
            [IsAuthenticated, IsActiveCallHistoryUser]
            if self.request.method in {"GET", "HEAD"}
            else [IsAuthenticated, IsCallSubmitter]
        )
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.request.user.is_admin:
            return AdminCallHistorySerializer
        return CallHistorySerializer

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
        data = {name: query_params[name] for name in self.filter_params if name in query_params}
        serializer = CallHistoryFilterSerializer(data=data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_queryset(self) -> QuerySet[Call]:
        filters = self.get_filters()
        user = self.request.user
        queryset = Call.objects.all()
        if not user.is_admin:
            queryset = queryset.filter(caller=user)
        elif "caller_id" in filters:
            queryset = queryset.filter(caller_id=filters["caller_id"])

        if "lead_id" in filters:
            queryset = queryset.filter(lead_id=filters["lead_id"])
        if "outcome_code" in filters:
            queryset = queryset.filter(outcome_code_snapshot=filters["outcome_code"])
        if "started_at_from" in filters:
            queryset = queryset.filter(started_at__gte=filters["started_at_from"])
        if "started_at_to" in filters:
            queryset = queryset.filter(started_at__lte=filters["started_at_to"])

        return queryset.select_related("lead").order_by("-started_at", "-id")

    def post(self, request):
        serializer = CallSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call, created = submit_call(
            caller=request.user,
            **serializer.validated_data,
        )
        response_serializer = CallSubmissionResponseSerializer(call)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CallHistoryDetailView(generics.RetrieveAPIView):
    serializer_class = CallHistorySerializer
    permission_classes = [IsAuthenticated, IsActiveCallHistoryUser]
    lookup_url_kwarg = "pk"

    def get_serializer_class(self):
        if self.request.user.is_admin:
            return AdminCallHistorySerializer
        return CallHistorySerializer

    def get_queryset(self) -> QuerySet[Call]:
        queryset = Call.objects.all()
        if not self.request.user.is_admin:
            queryset = queryset.filter(caller=self.request.user)
        return queryset.select_related("lead")


class CallOutcomeListView(generics.ListAPIView):
    serializer_class = CallOutcomeSerializer
    permission_classes = [IsAuthenticated, IsActiveCallHistoryUser]

    def get_permissions(self):
        permission_classes = (
            [IsAuthenticated, IsActiveCallHistoryUser]
            if self.request.method in {"GET", "HEAD"}
            else [IsAuthenticated, IsActiveCallAdmin]
        )
        return [permission() for permission in permission_classes]

    def get_queryset(self) -> QuerySet[CallOutcome]:
        queryset = CallOutcome.objects.all()
        if not self.request.user.is_admin:
            queryset = queryset.filter(is_active=True)
        return queryset.order_by("display_order", "label", "id")

    def post(self, request):
        serializer = CallOutcomeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                outcome = CallOutcome.objects.create(**serializer.validated_data)
        except IntegrityError:
            # The unique constraint is the final guard against concurrent code reuse.
            raise ValidationError(
                {"code": "An outcome with this code already exists."}
            ) from None
        return Response(
            CallOutcomeSerializer(outcome).data,
            status=status.HTTP_201_CREATED,
        )


class CallOutcomeDetailView(APIView):
    permission_classes = [IsAuthenticated, IsActiveCallAdmin]

    def patch(self, request, pk):
        if "code" in request.data:
            raise ValidationError(
                {"code": "Outcome codes are immutable and cannot be changed."}
            )
        serializer = CallOutcomeUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            outcome = get_object_or_404(
                CallOutcome.objects.select_for_update(),
                pk=pk,
            )
            for field, value in serializer.validated_data.items():
                setattr(outcome, field, value)
            if serializer.validated_data:
                outcome.save(
                    update_fields=[*serializer.validated_data.keys(), "updated_at"]
                )

        return Response(CallOutcomeSerializer(outcome).data)
