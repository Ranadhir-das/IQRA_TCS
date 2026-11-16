import io
import json
import threading
import unittest
import zipfile
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.db.models.query import QuerySet
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.leads.import_services import (
    cleanup_expired_imports,
    commit_staged_import,
    create_staged_import,
    recover_abandoned_imports,
)
from apps.leads import import_services
from apps.leads.imports import ImportFileError, normalize_phone, preview_upload
from apps.leads.models import Lead, LeadAssignmentHistory, StagedLeadImport
from apps.leads.middleware import LeadImportUploadLimitMiddleware


PREVIEW_URL = "/api/v1/leads/import/preview/"
COMMIT_URL = "/api/v1/leads/import/commit/"


def make_user(username, role):
    return User.objects.create_user(
        username=username,
        password="SecureTestPass@123",
        email=f"{username}@iqracentral.com",
        role=role,
        is_staff=(role == User.Role.ADMIN),
    )


def csv_file(content, name="leads.csv"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


def xlsx_file(*, formula=False, sheet_xml=None, shared_strings_xml=None):
    from django.core.files.uploadedfile import SimpleUploadedFile

    def cell(ref, value, formula_value=False):
        f = "<f>1+1</f>" if formula_value else ""
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is>{f}</c>'

    formula_xml = "<f>1+1</f>" if formula else ""
    worksheet = sheet_xml or (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1">' + cell("A1", "Student Name") + cell("B1", "Primary Phone") + '</row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Excel Student</t></is></c>'
        f'<c r="B2" t="inlineStr"><is><t>9876501234</t></is>{formula_xml}</c></row>'
        '</sheetData></worksheet>'
    )
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Leads" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": worksheet,
    }
    if shared_strings_xml is not None:
        files["xl/sharedStrings.xml"] = shared_strings_xml
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value if isinstance(value, bytes) else value.encode("utf-8"))
    return SimpleUploadedFile("leads.xlsx", buffer.getvalue(), content_type="application/octet-stream")


class LeadImportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user("import_admin", User.Role.ADMIN)
        self.other_admin = make_user("other_import_admin", User.Role.ADMIN)
        self.employee = make_user("import_employee", User.Role.EMPLOYEE)

    def authenticate(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def preview(self, user=None, content="Student Name,Primary Phone,Email\nA Student,9876501200,a@example.com\n", policy=None):
        self.authenticate(user or self.admin)
        data = {"file": csv_file(content)}
        if policy:
            data["duplicate_policy"] = policy
        return self.client.post(PREVIEW_URL, data, format="multipart")

    def commit(self, user, preview_response, **extra):
        self.authenticate(user)
        payload = {
            "staged_import_id": preview_response.json()["staged_import_id"],
            "commit_token": preview_response.json()["commit_token"],
            **extra,
        }
        return self.client.post(COMMIT_URL, payload, format="json")

    def test_valid_csv_preview_is_staged_without_creating_leads(self):
        response = self.preview()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["summary"], {
            "total": 1, "valid": 1, "invalid": 0, "duplicate": 0,
            "importable": 1, "requires_attention": 0,
        })
        self.assertEqual(body["header_mapping"]["Student Name"], "name")
        self.assertEqual(body["rows"][0]["row_number"], 2)
        self.assertEqual(Lead.objects.count(), 0)
        staged = StagedLeadImport.objects.get(pk=body["staged_import_id"])
        self.assertEqual(staged.uploaded_by, self.admin)
        self.assertEqual(staged.duplicate_policy, "skip_duplicates")
        self.assertEqual(staged.staged_rows[0]["data"]["phone"], "9876501200")
        self.assertNotIn("file_path", body)

    def test_valid_xlsx_preview_and_formula_rejection(self):
        self.authenticate(self.admin)
        response = self.client.post(PREVIEW_URL, {"file": xlsx_file()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Lead.objects.count(), 0)
        self.authenticate(self.admin)
        formula_response = self.client.post(
            PREVIEW_URL, {"file": xlsx_file(formula=True)}, format="multipart"
        )
        self.assertEqual(formula_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_xlsx_streams_shared_strings(self):
        shared = (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Student Name</t></si><si><t>Primary Phone</t></si>'
            '<si><t>Shared Student</t></si><si><t>9876501232</t></si></sst>'
        )
        sheet = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
            '</sheetData></worksheet>'
        )
        self.authenticate(self.admin)
        response = self.client.post(
            PREVIEW_URL,
            {"file": xlsx_file(sheet_xml=sheet, shared_strings_xml=shared)},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        stage = StagedLeadImport.objects.get(pk=response.json()["staged_import_id"])
        self.assertEqual(stage.staged_rows[0]["data"]["name"], "Shared Student")

    @override_settings(LEAD_IMPORT_MAX_XLSX_ROW_ELEMENTS=2)
    def test_xlsx_actual_row_elements_are_bounded(self):
        sheet = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Student Name</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>A</t></is></c></row>'
            '<row r="3"><c r="A3" t="inlineStr"><is><t>B</t></is></c></row>'
            '</sheetData></worksheet>'
        )
        self.authenticate(self.admin)
        response = self.client.post(
            PREVIEW_URL, {"file": xlsx_file(sheet_xml=sheet)}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(LEAD_IMPORT_MAX_XLSX_CELL_ELEMENTS=3)
    def test_xlsx_actual_cell_elements_are_bounded(self):
        self.authenticate(self.admin)
        response = self.client.post(PREVIEW_URL, {"file": xlsx_file()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_xlsx_duplicate_and_malformed_cell_references_are_rejected(self):
        duplicate = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Student Name</t></is></c>'
            '<c r="A1" t="inlineStr"><is><t>Primary Phone</t></is></c></row>'
            '</sheetData></worksheet>'
        )
        malformed = duplicate.replace('r="A1"', 'r="UNTRUSTED_CELL_REFERENCE"', 1)
        self.authenticate(self.admin)
        for sheet in (duplicate, malformed):
            response = self.client.post(
                PREVIEW_URL, {"file": xlsx_file(sheet_xml=sheet)}, format="multipart"
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertNotIn("UNTRUSTED_CELL_REFERENCE", response.content.decode())

    def test_utf8_and_utf16_dtd_entity_declarations_are_rejected(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE worksheet [<!ENTITY crafted "SECRET_ENTITY_TEXT">]>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1"><v>&crafted;</v></c></row></sheetData></worksheet>'
        )
        self.authenticate(self.admin)
        for encoded_xml in (xml.encode("utf-8"), xml.encode("utf-16")):
            response = self.client.post(
                PREVIEW_URL,
                {"file": xlsx_file(sheet_xml=encoded_xml)},
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertNotIn("SECRET_ENTITY_TEXT", response.content.decode())
            self.assertNotIn("DOCTYPE", response.content.decode())

    def test_unsupported_xml_encoding_declaration_is_rejected_safely(self):
        sheet = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"/></sheetData></worksheet>'
        )
        self.authenticate(self.admin)
        response = self.client.post(
            PREVIEW_URL, {"file": xlsx_file(sheet_xml=sheet)}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("UTF-16", response.content.decode())

    def test_malformed_xlsx_and_zip_errors_are_fixed_safe_messages(self):
        crafted = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="CRAFTED_ROW_VALUE"/></sheetData></worksheet>'
        )
        self.authenticate(self.admin)
        for upload in (
            xlsx_file(sheet_xml=crafted),
            SimpleUploadedFile("broken.xlsx", b"PK\x03\x04broken archive"),
        ):
            response = self.client.post(PREVIEW_URL, {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertNotIn("CRAFTED_ROW_VALUE", response.content.decode())
            self.assertNotIn("Traceback", response.content.decode())

    def test_oversized_request_is_rejected_before_multipart_parsing(self):
        request = RequestFactory().post(
            PREVIEW_URL, data=b"not parsed", content_type="multipart/form-data"
        )
        request.META["CONTENT_LENGTH"] = str(settings.LEAD_IMPORT_MAX_REQUEST_SIZE + 1)
        downstream_called = []
        middleware = LeadImportUploadLimitMiddleware(lambda req: downstream_called.append(True))
        response = middleware(request)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(downstream_called, [])

    @override_settings(LEAD_IMPORT_MAX_CSV_COLUMNS=3)
    def test_csv_column_limit_is_checked_and_quoted_delimiters_are_allowed(self):
        excessive = self.preview(
            content="Student Name,Primary Phone,Email,Course\nA,1,a@example.com,BBA\n"
        )
        self.assertEqual(excessive.status_code, status.HTTP_400_BAD_REQUEST)
        valid = self.preview(
            content='Student Name,Primary Phone,Enquiry Details\nA,1,"Asked, then left"\n'
        )
        self.assertEqual(valid.status_code, status.HTTP_201_CREATED)

    def test_file_type_content_and_empty_malformed_rejections(self):
        self.authenticate(self.admin)
        for upload in (
            csv_file("a,b\n1,2\n", "old.xls"),
            csv_file("PK\x03\x04not a workbook", "spoof.csv"),
            csv_file("", "empty.csv"),
            csv_file('"unclosed,header\nvalue', "bad.csv"),
            csv_file("not a workbook", "spoof.xlsx"),
        ):
            response = self.client.post(PREVIEW_URL, {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(LEAD_IMPORT_MAX_FILE_SIZE=30)
    def test_file_size_limit(self):
        response = self.preview(content="Student Name,Primary Phone\n" + "A" * 100 + ",123\n")
        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    @override_settings(LEAD_IMPORT_MAX_FILE_SIZE=30)
    def test_importer_retains_file_size_defense_in_depth(self):
        with self.assertRaises(ImportFileError):
            preview_upload(
                csv_file("Student Name,Primary Phone\n" + "A" * 100 + ",123\n"),
                duplicate_policy=StagedLeadImport.DuplicatePolicy.SKIP,
            )

    @override_settings(LEAD_IMPORT_MAX_ROWS=2)
    def test_row_limit(self):
        response = self.preview(content="Student Name,Primary Phone\nA,1\nB,2\nC,3\n")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_required_columns_row_validation_and_ambiguous_headers(self):
        cases = [
            ("Student Name,Email\nA,a@example.com\n", status.HTTP_400_BAD_REQUEST),
            ("Student Name,Primary Phone,Name\nA,1,B\n", status.HTTP_400_BAD_REQUEST),
            ("Student Name,Primary Phone,Status\nA,1,CONVERTED\n", status.HTTP_400_BAD_REQUEST),
            ("Student Name,Primary Phone\n,1\nA,\n", status.HTTP_201_CREATED),
            ("Student Name,Primary Phone,Email\nA,2,not-an-email\n", status.HTTP_201_CREATED),
            ("Student Name,Primary Phone\n" + "N" * 201 + ",3\n", status.HTTP_201_CREATED),
        ]
        for content, expected_status in cases:
            response = self.preview(content=content)
            self.assertEqual(response.status_code, expected_status)
            if response.status_code == status.HTTP_201_CREATED:
                self.assertGreater(response.json()["summary"]["invalid"], 0)
                self.assertNotIn("not-an-email", json.dumps(response.json()))

    def test_duplicate_detection_default_policy_and_phone_normalization(self):
        Lead.objects.create(name="Existing", phone="+91 98765-01200")
        response = self.preview(content=(
            "Student Name,Primary Phone\nA,919876501200\nB,9876501201\nC,98765 01201\n"
        ))
        body = response.json()
        self.assertEqual(body["summary"]["duplicate"], 2)
        self.assertEqual(body["summary"]["importable"], 1)
        self.assertEqual([r["outcome"] for r in body["rows"]], ["duplicate", "importable", "duplicate"])
        self.assertEqual(normalize_phone("+91 98765-01200"), normalize_phone("919876501200"))

    def test_explicit_allow_duplicates_policy_is_bound_to_staging(self):
        content = "Student Name,Primary Phone\nA,9876501200\nB,98765 01200\n"
        response = self.preview(content=content, policy="allow_duplicates")
        self.assertEqual(response.json()["summary"]["importable"], 2)
        staged = StagedLeadImport.objects.get(pk=response.json()["staged_import_id"])
        self.assertEqual(staged.duplicate_policy, "allow_duplicates")
        commit = self.commit(self.admin, response, duplicate_policy="skip_duplicates")
        self.assertEqual(commit.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Lead.objects.count(), 0)
        commit = self.commit(self.admin, response)
        self.assertEqual(commit.status_code, status.HTTP_200_OK)
        self.assertEqual(commit.json()["created"], 2)

    def test_employee_cannot_preview_or_commit(self):
        preview = self.preview()
        self.authenticate(self.employee)
        response = self.client.post(PREVIEW_URL, {"file": csv_file("Student Name,Primary Phone\nA,1\n")}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        commit = self.commit(self.employee, preview)
        self.assertEqual(commit.status_code, status.HTTP_403_FORBIDDEN)

    def test_different_admin_cannot_commit_another_admin_stage(self):
        preview = self.preview()
        response = self.commit(self.other_admin, preview)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Lead.objects.count(), 0)

    def test_expired_and_tampered_stages_are_rejected(self):
        expired = self.preview()
        stage = StagedLeadImport.objects.get(pk=expired.json()["staged_import_id"])
        stage.expires_at = timezone.now() - timedelta(seconds=1)
        stage.save(update_fields=["expires_at"])
        response = self.commit(self.admin, expired)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        stage.refresh_from_db()
        self.assertEqual(stage.staged_rows, [])
        self.assertEqual(stage.preview_rows, [])

        tampered = self.preview(content="Student Name,Primary Phone\nB,9876501202\n")
        stage = StagedLeadImport.objects.get(pk=tampered.json()["staged_import_id"])
        stage.staged_rows[0]["data"]["name"] = "Tampered"
        stage.save(update_fields=["staged_rows"])
        response = self.commit(self.admin, tampered)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Lead.objects.count(), 0)

    def test_processing_stage_cannot_be_recommitted_or_cancelled(self):
        preview = self.preview()
        stage = StagedLeadImport.objects.get(pk=preview.json()["staged_import_id"])
        stage.state = StagedLeadImport.State.PROCESSING
        stage.processing_started_at = timezone.now()
        stage.save(update_fields=["state", "processing_started_at"])

        repeated = self.commit(self.admin, preview)
        self.assertEqual(repeated.status_code, status.HTTP_400_BAD_REQUEST)
        self.authenticate(self.admin)
        cancelled = self.client.post(
            f"/api/v1/leads/import/{stage.pk}/cancel/", {}, format="json"
        )
        self.assertEqual(cancelled.status_code, status.HTTP_400_BAD_REQUEST)
        stage.refresh_from_db()
        self.assertEqual(stage.state, StagedLeadImport.State.PROCESSING)

    def test_recovery_only_fails_abandoned_processing_stages(self):
        active_response = self.preview(content="Student Name,Primary Phone\nActive,9876501230\n")
        abandoned_response = self.preview(content="Student Name,Primary Phone\nAbandoned,9876501231\n")
        active = StagedLeadImport.objects.get(pk=active_response.json()["staged_import_id"])
        abandoned = StagedLeadImport.objects.get(pk=abandoned_response.json()["staged_import_id"])
        abandoned.state = StagedLeadImport.State.PROCESSING
        abandoned.processing_started_at = timezone.now() - timedelta(hours=2)
        abandoned.save(update_fields=["state", "processing_started_at"])

        self.assertEqual(recover_abandoned_imports(), 1)
        active.refresh_from_db()
        abandoned.refresh_from_db()
        self.assertEqual(active.state, StagedLeadImport.State.STAGED)
        self.assertTrue(active.staged_rows)
        self.assertEqual(abandoned.state, StagedLeadImport.State.FAILED)
        self.assertEqual(abandoned.staged_rows, [])
        self.assertEqual(abandoned.preview_rows, [])

    def test_changed_file_hash_is_rejected(self):
        preview = self.preview(content="Student Name,Primary Phone\nHash Student,9876501202\n")
        stage = StagedLeadImport.objects.get(pk=preview.json()["staged_import_id"])
        stage.file_sha256 = "0" * 64
        stage.save(update_fields=["file_sha256"])
        response = self.commit(self.admin, preview)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Lead.objects.count(), 0)

    def test_commit_creates_valid_unassigned_default_status_leads_once(self):
        response = self.preview(content="Student Name,Primary Phone\nNew Student,9876501200\n")
        committed = self.commit(self.admin, response)
        self.assertEqual(committed.status_code, status.HTTP_200_OK)
        self.assertEqual(committed.json(), {
            "created": 1,
            "skipped": 0,
            "failed": 0,
            "skipped_rows": [],
            "failed_rows": [],
        })
        lead = Lead.objects.get(phone="9876501200")
        self.assertEqual(lead.created_by, self.admin)
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertIsNone(lead.assigned_to)
        self.assertFalse(LeadAssignmentHistory.objects.filter(lead=lead).exists())
        repeated = self.commit(self.admin, response)
        self.assertEqual(repeated.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Lead.objects.count(), 1)

    def test_commit_rechecks_crm_duplicates_and_skips_new_duplicate(self):
        response = self.preview(content="Student Name,Primary Phone\nNew Student,9876501200\n")
        Lead.objects.create(name="Raced lead", phone="98765 01200")
        committed = self.commit(self.admin, response)
        self.assertEqual(committed.status_code, status.HTTP_200_OK)
        self.assertEqual(committed.json()["created"], 0)
        self.assertEqual(committed.json()["skipped_rows"], [2])

    def test_failed_commit_rolls_back_all_leads_and_consumes_stage(self):
        response = self.preview(content="Student Name,Primary Phone\nA,9876501200\nB,9876501201\n")
        real_bulk_create = Lead.objects.bulk_create

        def insert_then_fail(objects, **kwargs):
            real_bulk_create(objects[:1], **kwargs)
            raise RuntimeError("simulated failure after first insert")

        with patch("apps.leads.import_services.Lead.objects.bulk_create", side_effect=insert_then_fail):
            committed = self.commit(self.admin, response)
        self.assertEqual(committed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Lead.objects.count(), 0)
        stage = StagedLeadImport.objects.get(pk=response.json()["staged_import_id"])
        self.assertEqual(stage.state, StagedLeadImport.State.FAILED)
        self.assertEqual(stage.staged_rows, [])
        self.assertEqual(stage.preview_rows, [])

    def test_cancel_and_expiry_cleanup_erase_staged_rows(self):
        response = self.preview()
        stage = StagedLeadImport.objects.get(pk=response.json()["staged_import_id"])
        self.authenticate(self.admin)
        cancelled = self.client.post(f"/api/v1/leads/import/{stage.pk}/cancel/", {}, format="json")
        self.assertEqual(cancelled.status_code, status.HTTP_204_NO_CONTENT)
        stage.refresh_from_db()
        self.assertEqual(stage.state, StagedLeadImport.State.CANCELLED)
        self.assertEqual(stage.staged_rows, [])
        self.assertEqual(stage.preview_rows, [])

        expired = self.preview(content="Student Name,Primary Phone\nB,9876501202\n")
        stage = StagedLeadImport.objects.get(pk=expired.json()["staged_import_id"])
        stage.expires_at = timezone.now() - timedelta(seconds=1)
        stage.save(update_fields=["expires_at"])
        self.assertEqual(cleanup_expired_imports(), 1)
        stage.refresh_from_db()
        self.assertEqual(stage.staged_rows, [])
        self.assertEqual(stage.preview_rows, [])

    def test_unauthenticated_import_access_is_rejected(self):
        response = self.client.post(PREVIEW_URL, {"file": csv_file("Student Name,Primary Phone\nA,1\n")}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        response = self.client.post(COMMIT_URL, {"staged_import_id": 1, "commit_token": "x"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class DurableImportClaimTests(TransactionTestCase):
    def setUp(self):
        self.admin = make_user("durable_import_admin", User.Role.ADMIN)
        self.preview = {
            "file_sha256": "a" * 64,
            "rows": [{
                "row_number": 2,
                "data": {"name": "Claimed Student", "phone": "9876501999"},
            }],
            "row_results": [{"row_number": 2, "outcome": "importable", "errors": []}],
            "summary": {"total": 1, "valid": 1, "invalid": 0, "duplicate": 0, "importable": 1},
        }
        self.staged, self.token = create_staged_import(
            owner=self.admin,
            preview=self.preview,
            duplicate_policy=StagedLeadImport.DuplicatePolicy.SKIP,
        )

    def test_processing_claim_is_committed_before_lead_insertion(self):
        real_insert = import_services._insert_staged_leads

        def observe_claim(staged, owner):
            observer = connections["default"].copy(alias="import_claim_observer")
            try:
                with observer.cursor() as cursor:
                    cursor.execute(
                        f"SELECT state FROM {StagedLeadImport._meta.db_table} WHERE id = %s",
                        [staged.pk],
                    )
                    observed_state = cursor.fetchone()[0]
            finally:
                observer.close()
            self.assertEqual(observed_state, StagedLeadImport.State.PROCESSING)
            return real_insert(staged, owner)

        with patch("apps.leads.import_services._insert_staged_leads", side_effect=observe_claim):
            result = commit_staged_import(
                staged_id=self.staged.pk, token=self.token, owner=self.admin
            )
        self.assertEqual(result["created"], 1)
        self.staged.refresh_from_db()
        self.assertEqual(self.staged.state, StagedLeadImport.State.COMMITTED)

    @unittest.skipUnless(connection.vendor == "postgresql", "Concurrent row-lock test requires PostgreSQL")
    def test_concurrent_commit_attempts_only_claim_and_insert_once(self):
        start_claims = threading.Barrier(2)
        both_claim_queries_started = threading.Event()
        first_claim_locked = threading.Event()
        release_first_claim = threading.Event()
        query_lock = threading.Lock()
        query_threads = set()
        returned_queries = []
        thread_state = threading.local()
        connection_objects = {}
        results = {}
        failures = {}

        original_get = QuerySet.get

        def synchronized_get(queryset, *args, **kwargs):
            thread_name = threading.current_thread().name
            is_first_claim_query = (
                queryset.model is StagedLeadImport
                and queryset.query.select_for_update
                and not getattr(thread_state, "claim_query_seen", False)
            )
            if not is_first_claim_query:
                return original_get(queryset, *args, **kwargs)

            start_claims.wait(timeout=5)
            with query_lock:
                query_threads.add(thread_name)
                if len(query_threads) == 2:
                    both_claim_queries_started.set()

            staged = original_get(queryset, *args, **kwargs)
            with query_lock:
                returned_queries.append(thread_name)
                is_first_to_lock = not first_claim_locked.is_set()
                if is_first_to_lock:
                    first_claim_locked.set()

            thread_state.claim_query_seen = True
            if is_first_to_lock and not release_first_claim.wait(timeout=5):
                raise TimeoutError("Timed out waiting to release the first staged-import claim")
            return staged

        def commit_worker(label):
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                connection_objects[label] = connections["default"].connection
                results[label] = commit_staged_import(
                    staged_id=self.staged.pk,
                    token=self.token,
                    owner=self.admin,
                )
            except ValidationError as exc:
                results[label] = {"validation_error": str(exc.detail)}
            except BaseException as exc:
                failures[label] = exc
            finally:
                connections.close_all()

        workers = [
            threading.Thread(target=commit_worker, args=(label,), name=label, daemon=True)
            for label in ("import-commit-1", "import-commit-2")
        ]
        try:
            with patch.object(QuerySet, "get", new=synchronized_get):
                for worker in workers:
                    worker.start()
                self.assertTrue(first_claim_locked.wait(timeout=5), "No worker acquired the stage lock")
                self.assertTrue(
                    both_claim_queries_started.wait(timeout=5),
                    "Both workers did not reach the same claim query",
                )
                # Keep the first transaction holding its row lock long enough
                # for the other independent connection to block on its claim.
                self.assertFalse(release_first_claim.wait(timeout=0.2))
                self.assertEqual(len(returned_queries), 1)
        finally:
            release_first_claim.set()
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=5)

        self.assertFalse(any(worker.is_alive() for worker in workers), "Commit workers did not finish")
        self.assertFalse(failures, f"Unexpected worker failures: {failures!r}")
        self.assertEqual(len(connection_objects), 2)
        self.assertIsNot(connection_objects["import-commit-1"], connection_objects["import-commit-2"])
        self.assertEqual(len(returned_queries), 2)
        successful = [value for value in results.values() if "created" in value]
        rejected = [value for value in results.values() if "validation_error" in value]
        self.assertEqual(len(successful), 1)
        self.assertEqual(successful[0]["created"], 1)
        self.assertEqual(len(rejected), 1)
        self.assertIn("no longer available", rejected[0]["validation_error"])
        self.assertEqual(Lead.objects.filter(phone="9876501999").count(), 1)
        self.staged.refresh_from_db()
        self.assertEqual(self.staged.state, StagedLeadImport.State.COMMITTED)

    def test_interruption_leaves_unusable_claim_recovered_as_failed(self):
        class SimulatedProcessInterruption(BaseException):
            pass

        with patch(
            "apps.leads.import_services._insert_staged_leads",
            side_effect=SimulatedProcessInterruption(),
        ):
            with self.assertRaises(SimulatedProcessInterruption):
                commit_staged_import(
                    staged_id=self.staged.pk, token=self.token, owner=self.admin
                )

        self.staged.refresh_from_db()
        self.assertEqual(self.staged.state, StagedLeadImport.State.PROCESSING)
        self.assertEqual(Lead.objects.count(), 0)
        recovered = recover_abandoned_imports(
            now=self.staged.processing_started_at
            + timedelta(seconds=settings.LEAD_IMPORT_PROCESSING_RECOVERY_SECONDS + 1)
        )
        self.assertEqual(recovered, 1)
        self.staged.refresh_from_db()
        self.assertEqual(self.staged.state, StagedLeadImport.State.FAILED)
        self.assertEqual(self.staged.staged_rows, [])
        self.assertEqual(self.staged.preview_rows, [])
