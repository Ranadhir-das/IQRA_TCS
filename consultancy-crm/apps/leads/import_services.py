"""Transactional lifecycle for staged lead imports."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.leads.imports import normalize_phone, staged_data_digest
from apps.leads.models import Lead, StagedLeadImport


def cleanup_expired_imports(now=None):
    """Discard expired staged PII; no filesystem paths are used by this workflow."""
    now = now or timezone.now()
    return StagedLeadImport.objects.filter(
        expires_at__lte=now, state=StagedLeadImport.State.STAGED
    ).update(staged_rows=[], preview_rows=[], state=StagedLeadImport.State.EXPIRED)


def create_staged_import(*, owner, preview, duplicate_policy):
    token = secrets.token_urlsafe(32)
    rows = preview["rows"]
    file_hash = preview["file_sha256"]
    preview_rows = preview["row_results"]
    digest = staged_data_digest(file_hash, duplicate_policy, owner.pk, rows, preview_rows)
    expires_at = timezone.now() + timedelta(seconds=settings.LEAD_IMPORT_STAGING_TTL_SECONDS)
    staged = StagedLeadImport.objects.create(
        uploaded_by=owner,
        file_sha256=file_hash,
        data_sha256=digest,
        commit_token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        duplicate_policy=duplicate_policy,
        staged_rows=rows,
        preview_summary=preview["summary"],
        preview_rows=preview_rows,
        expires_at=expires_at,
    )
    return staged, token


def _stored_token_matches(staged, token):
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(staged.commit_token_sha256, digest)


def commit_staged_import(*, staged_id, token, owner):
    """Claim durably, then insert all leads in a separate atomic transaction.

    A process crash after claiming leaves PROCESSING permanently unusable until
    the recovery command marks it FAILED. The claim and Lead inserts are
    intentionally separate transactions, not one atomic transaction.
    """
    claim_error = None
    with transaction.atomic():
        try:
            staged = StagedLeadImport.objects.select_for_update().get(pk=staged_id)
        except StagedLeadImport.DoesNotExist:
            raise ValidationError({"staged_import_id": "Staged import not found."})

        if staged.uploaded_by_id != owner.pk or not _stored_token_matches(staged, token):
            raise ValidationError({"staged_import_id": "Staged import not found."})
        if staged.state != StagedLeadImport.State.STAGED:
            raise ValidationError({"staged_import_id": "This staged import is no longer available."})
        if staged.expires_at <= timezone.now():
            staged.state = StagedLeadImport.State.EXPIRED
            staged.staged_rows = []
            staged.preview_rows = []
            staged.save(update_fields=["state", "staged_rows", "preview_rows"])
            claim_error = "The staged import has expired. Upload the file again."
        elif staged.data_sha256 != staged_data_digest(
            staged.file_sha256,
            staged.duplicate_policy,
            staged.uploaded_by_id,
            staged.staged_rows,
            staged.preview_rows,
        ):
            staged.state = StagedLeadImport.State.FAILED
            staged.staged_rows = []
            staged.preview_rows = []
            staged.save(update_fields=["state", "staged_rows", "preview_rows"])
            claim_error = "The staged import failed integrity validation. Upload the file again."
        else:
            staged.state = StagedLeadImport.State.PROCESSING
            staged.processing_started_at = timezone.now()
            staged.save(update_fields=["state", "processing_started_at"])

    # Returning from the first atomic block durably commits PROCESSING before
    # any Lead insertion transaction begins.
    if claim_error:
        raise ValidationError({"detail": claim_error})

    try:
        with transaction.atomic():
            staged = StagedLeadImport.objects.select_for_update().get(pk=staged_id)
            if (
                staged.uploaded_by_id != owner.pk
                or not _stored_token_matches(staged, token)
                or staged.state != StagedLeadImport.State.PROCESSING
            ):
                raise ValidationError({"staged_import_id": "This staged import is no longer available."})
            if staged.data_sha256 != staged_data_digest(
                staged.file_sha256,
                staged.duplicate_policy,
                staged.uploaded_by_id,
                staged.staged_rows,
                staged.preview_rows,
            ):
                staged.state = StagedLeadImport.State.FAILED
                staged.staged_rows = []
                staged.preview_rows = []
                staged.save(update_fields=["state", "staged_rows", "preview_rows"])
                response_data = None
            else:
                response_data = _insert_staged_leads(staged, owner)
                staged.state = StagedLeadImport.State.COMMITTED
                staged.committed_at = timezone.now()
                staged.commit_result = response_data
                staged.staged_rows = []
                staged.preview_rows = []
                staged.save(
                    update_fields=[
                        "state", "committed_at", "commit_result", "staged_rows", "preview_rows"
                    ]
                )
    except Exception:
        # If insertion rolls back or this update itself fails, PROCESSING still
        # prevents token reuse; recovery later consumes abandoned claims.
        _fail_processing_stage(staged_id, owner.pk)
        raise ValidationError({"detail": "Import failed; no leads were created."}) from None

    if response_data is None:
        raise ValidationError({"detail": "The staged import failed integrity validation."})
    return response_data


def _insert_staged_leads(staged, owner):
    existing = set()
    candidate_phones = {normalize_phone(row["data"]["phone"]) for row in staged.staged_rows}
    if candidate_phones and staged.duplicate_policy == StagedLeadImport.DuplicatePolicy.SKIP:
        for lead in Lead.objects.only("phone").iterator(chunk_size=1000):
            normalized = normalize_phone(lead.phone)
            if normalized in candidate_phones:
                existing.add(normalized)

    seen = set()
    to_create = []
    skipped_rows = []
    for row in staged.staged_rows:
        data = row["data"]
        normalized = normalize_phone(data["phone"])
        duplicate = normalized in existing or normalized in seen
        seen.add(normalized)
        if duplicate and staged.duplicate_policy == StagedLeadImport.DuplicatePolicy.SKIP:
            skipped_rows.append(row["row_number"])
            continue
        lead = Lead(**data, created_by=owner, assigned_to=None)
        lead.full_clean()
        to_create.append(lead)

    Lead.objects.bulk_create(to_create, batch_size=500)
    return {
        "created": len(to_create),
        "skipped": len(skipped_rows),
        "failed": sum(row["outcome"] == "invalid" for row in staged.preview_rows),
        "skipped_rows": skipped_rows,
        "failed_rows": [
            {"row_number": row["row_number"], "errors": row["errors"]}
            for row in staged.preview_rows
            if row["outcome"] == "invalid"
        ],
    }


def _fail_processing_stage(staged_id, owner_id):
    with transaction.atomic():
        try:
            staged = StagedLeadImport.objects.select_for_update().get(
                pk=staged_id, uploaded_by_id=owner_id
            )
        except StagedLeadImport.DoesNotExist:
            return
        if staged.state == StagedLeadImport.State.PROCESSING:
            staged.state = StagedLeadImport.State.FAILED
            staged.staged_rows = []
            staged.preview_rows = []
            staged.save(update_fields=["state", "staged_rows", "preview_rows"])


def recover_abandoned_imports(now=None):
    """Fail old PROCESSING claims; skip active transactions holding row locks."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.LEAD_IMPORT_PROCESSING_RECOVERY_SECONDS)
    recovered = 0
    with transaction.atomic():
        stages = list(
            StagedLeadImport.objects.select_for_update(skip_locked=True)
            .filter(
                state=StagedLeadImport.State.PROCESSING,
            )
            .filter(
                Q(processing_started_at__lte=cutoff)
                | Q(processing_started_at__isnull=True)
            )
            .order_by("pk")
        )
        for staged in stages:
            staged.state = StagedLeadImport.State.FAILED
            staged.staged_rows = []
            staged.preview_rows = []
        if stages:
            StagedLeadImport.objects.bulk_update(
                stages, ["state", "staged_rows", "preview_rows"], batch_size=500
            )
            recovered = len(stages)
    return recovered


def cancel_staged_import(*, staged_id, owner):
    with transaction.atomic():
        try:
            staged = StagedLeadImport.objects.select_for_update().get(
                pk=staged_id, uploaded_by=owner
            )
        except StagedLeadImport.DoesNotExist:
            raise ValidationError({"staged_import_id": "Staged import not found."})
        if staged.state != StagedLeadImport.State.STAGED:
            raise ValidationError({"staged_import_id": "This staged import is no longer available."})
        staged.state = StagedLeadImport.State.CANCELLED
        staged.staged_rows = []
        staged.preview_rows = []
        staged.save(update_fields=["state", "staged_rows", "preview_rows"])
