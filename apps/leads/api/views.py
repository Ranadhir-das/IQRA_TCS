from django.db import transaction
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.permissions import IsAdminRole, IsEmployeeRole
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.services import (
    set_lead_assignment,
    update_employee_lead_lifecycle,
    validate_assignment_actor,
    validate_assignment_target,
)

from .permissions import IsAdminOrEmployeeRole
from .serializers import (
    AdminLeadSerializer,
    BulkLeadAssignmentSerializer,
    EmployeeLeadSerializer,
    EmployeeLeadStatusUpdateSerializer,
    LeadAssignmentHistorySerializer,
    LeadFilterSerializer,
    LeadImportCommitSerializer,
    LeadImportPreviewSerializer,
    LeadPagination,
)
from apps.leads.import_services import (
    cancel_staged_import,
    cleanup_expired_imports,
    commit_staged_import,
    create_staged_import,
)
from apps.leads.imports import ImportFileError, preview_upload


class LeadListCreateView(generics.ListCreateAPIView):
    pagination_class = LeadPagination

    allowed_query_params = frozenset(
        {
            "search",
            "status",
            "assigned_to",
            "unassigned",
            "archived",
            "page",
            "page_size",
            "format",
        }
    )
    filter_params = frozenset(
        {"search", "status", "assigned_to", "unassigned", "archived"}
    )

    def get_permissions(self):
        role_permission = IsAdminRole if self.request.method == "POST" else IsAdminOrEmployeeRole
        return [IsAuthenticated(), role_permission()]

    def get_serializer_class(self):
        if self.request.user.is_admin:
            return AdminLeadSerializer
        return EmployeeLeadSerializer

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
                {name: "Provide each query parameter only once." for name in sorted(repeated)}
            )
        data = {
            name: query_params[name]
            for name in self.filter_params
            if name in query_params
        }
        serializer = LeadFilterSerializer(
            data=data,
            context={"request": self.request},
        )
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_queryset(self) -> QuerySet[Lead]:
        filters = self.get_filters()
        user = self.request.user
        if user.is_admin:
            queryset = Lead.objects.all()
            archived = filters.get("archived", "false")
            if archived == "true":
                queryset = queryset.filter(is_archived=True)
            elif archived != "all":
                queryset = queryset.filter(is_archived=False)

            if "assigned_to" in filters:
                queryset = queryset.filter(assigned_to_id=filters["assigned_to"])
            if "unassigned" in filters:
                queryset = queryset.filter(
                    assigned_to__isnull=filters["unassigned"]
                )
        else:
            # The employee scope is applied on the server and cannot be widened
            # by query parameters supplied by the client.
            queryset = Lead.objects.filter(assigned_to=user, is_archived=False)

        if "status" in filters:
            queryset = queryset.filter(status=filters["status"])

        search_term = filters.get("search", "")
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term)
                | Q(phone__icontains=search_term)
                | Q(alternate_phone__icontains=search_term)
                | Q(email__icontains=search_term)
                | Q(course_interest__icontains=search_term)
            )

        return queryset.select_related("assigned_to", "created_by").order_by(
            "-created_at",
            "-id",
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class LeadDetailView(generics.RetrieveUpdateAPIView):
    lookup_url_kwarg = "pk"

    def get_permissions(self):
        role_permission = IsAdminRole if self.request.method in {"PUT", "PATCH"} else IsAdminOrEmployeeRole
        return [IsAuthenticated(), role_permission()]

    def get_serializer_class(self):
        if self.request.user.is_admin:
            return AdminLeadSerializer
        return EmployeeLeadSerializer

    def get_queryset(self) -> QuerySet[Lead]:
        user = self.request.user
        if user.is_admin:
            queryset = Lead.objects.all()
        else:
            queryset = Lead.objects.filter(assigned_to=user, is_archived=False)
        return queryset.select_related("assigned_to", "created_by")


class EmployeeLeadStatusUpdateView(APIView):
    """Allow an active assigned employee to update allowed lifecycle fields on a lead."""

    permission_classes = [IsAuthenticated, IsEmployeeRole]

    def post(self, request, pk):
        serializer = EmployeeLeadStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = update_employee_lead_lifecycle(
            lead_id=pk,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(
            EmployeeLeadSerializer(lead).data,
            status=status.HTTP_200_OK,
        )


class BulkLeadAssignmentView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        serializer = BulkLeadAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead_ids = serializer.validated_data["lead_ids"]
        employee = serializer.validated_data["employee_id"]
        reason = serializer.validated_data.get("reason", "")

        with transaction.atomic():
            leads = list(
                Lead.objects.select_for_update()
                .filter(pk__in=lead_ids)
                .order_by("pk")
            )
            found_ids = {lead.pk for lead in leads}
            missing_ids = sorted(set(lead_ids) - found_ids)
            if missing_ids:
                raise ValidationError({"lead_ids": f"Unknown lead IDs: {missing_ids}."})

            archived_ids = [lead.pk for lead in leads if lead.is_archived]
            if archived_ids:
                raise ValidationError(
                    {"lead_ids": f"Archived leads cannot be assigned: {archived_ids}."}
                )

            # Lock and revalidate both actor and target before changing any row.
            actor = validate_assignment_actor(request.user, lock=True)
            employee = validate_assignment_target(employee, lock=True)

            changed = 0
            unchanged = 0
            for lead in leads:
                _, did_change = set_lead_assignment(lead, employee, actor, reason)
                if did_change:
                    changed += 1
                else:
                    unchanged += 1

        return Response(
            {
                "requested": len(lead_ids),
                "changed": changed,
                "unchanged": unchanged,
            }
        )


class LeadAssignmentHistoryListView(generics.ListAPIView):
    serializer_class = LeadAssignmentHistorySerializer
    pagination_class = LeadPagination
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self) -> QuerySet[LeadAssignmentHistory]:
        lead = get_object_or_404(Lead, pk=self.kwargs["pk"])
        return (
            LeadAssignmentHistory.objects.filter(lead=lead)
            .select_related("previous_employee", "new_employee", "changed_by")
        )


class LeadImportPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        cleanup_expired_imports()
        serializer = LeadImportPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        policy = serializer.validated_data["duplicate_policy"]
        try:
            preview = preview_upload(upload, duplicate_policy=policy)
        except ImportFileError as exc:
            raise ValidationError({"file": str(exc)}) from None

        staged, token = create_staged_import(
            owner=request.user, preview=preview, duplicate_policy=policy
        )
        return Response(
            {
                "staged_import_id": staged.pk,
                "commit_token": token,
                "expires_at": staged.expires_at,
                "duplicate_policy": policy,
                "header_mapping": preview["mapping"],
                "summary": preview["summary"],
                "rows": preview["row_results"],
            },
            status=201,
        )


class LeadImportCommitView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        cleanup_expired_imports()
        serializer = LeadImportCommitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = commit_staged_import(
            staged_id=serializer.validated_data["staged_import_id"],
            token=serializer.validated_data["commit_token"],
            owner=request.user,
        )
        return Response(result)


class LeadImportCancelView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        cancel_staged_import(staged_id=pk, owner=request.user)
        return Response(status=204)
