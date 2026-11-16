"""Safe CSV/XLSX parsing and row validation for administrator lead imports."""

import csv
import hashlib
import io
import json
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from django.conf import settings
from rest_framework import serializers

from apps.leads.models import Lead


HEADER_ALIASES = {
    "name": {"name", "studentname", "customername", "fullname", "student"},
    "phone": {"phone", "primaryphone", "phonenumber", "mobileno", "mobile", "contactnumber"},
    "alternate_phone": {"alternatephone", "altphone", "secondaryphone", "alternatemobile"},
    "email": {"email", "emailaddress"},
    "course_interest": {"course", "courseinterest", "enquirycourse", "program"},
    "enquiry_details": {"enquiry", "enquirydetails", "message", "requirements"},
    "internal_notes": {"notes", "internalnotes", "adminnotes"},
}
FORBIDDEN_HEADERS = {
    "id", "leadid", "assignedto", "employee", "assignee", "createdby",
    "status", "isarchived", "archived", "createdat", "updatedat",
}
HEADER_FIELD = {alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases}
MAX_XLSX_MEMBERS = 1000
MAX_XLSX_UNCOMPRESSED = 25 * 1024 * 1024
MAX_XLSX_RATIO = 100


class ImportFileError(Exception):
    """Expected, fixed-message validation error for an uploaded file."""


class LeadImportRowSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, trim_whitespace=True)
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    alternate_phone = serializers.CharField(max_length=32, allow_blank=True, required=False, trim_whitespace=True)
    email = serializers.EmailField(max_length=254, allow_blank=True, required=False, trim_whitespace=True)
    course_interest = serializers.CharField(max_length=200, allow_blank=True, required=False, trim_whitespace=True)
    enquiry_details = serializers.CharField(
        max_length=settings.LEAD_IMPORT_MAX_TEXT_LENGTH,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )
    internal_notes = serializers.CharField(
        max_length=settings.LEAD_IMPORT_MAX_TEXT_LENGTH,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )

    def validate_phone(self, value):
        if not value:
            raise serializers.ValidationError("This field is required.")
        return value


def normalize_phone(value):
    """Compare phones by their ASCII digits; stored values remain trimmed input."""
    digits = re.sub(r"[^0-9]", "", value)
    return digits or value.strip().casefold()


def _clean_header(value):
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _map_headers(headers):
    if len(headers) > settings.LEAD_IMPORT_MAX_CSV_COLUMNS:
        raise ImportFileError("The file exceeds the supported column limit.")
    mapping = {}
    seen_fields = {}
    for column, header in enumerate(headers):
        normalized = _clean_header(header)
        if not normalized:
            continue
        if normalized in FORBIDDEN_HEADERS:
            raise ImportFileError("Import files cannot contain ownership, status, archive, or system columns.")
        field = HEADER_FIELD.get(normalized)
        if field is None:
            raise ImportFileError("The file contains an unsupported column header.")
        if field in seen_fields:
            raise ImportFileError("Multiple columns map to the same lead field.")
        mapping[column] = field
        seen_fields[field] = header
    if "name" not in seen_fields or "phone" not in seen_fields:
        raise ImportFileError("Required columns are Student name and Primary phone.")
    return mapping, seen_fields


def _read_csv(data):
    if data.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        raise ImportFileError("File contents do not match a CSV file.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError("CSV files must use UTF-8 encoding.") from None
    if not text.strip():
        raise ImportFileError("The uploaded file is empty.")
    _precheck_csv_columns(text)
    try:
        rows = []
        for row in csv.reader(io.StringIO(text, newline=""), strict=True):
            rows.append(row)
            if len(rows) > settings.LEAD_IMPORT_MAX_ROWS + 1:
                raise ImportFileError(f"The file exceeds the {settings.LEAD_IMPORT_MAX_ROWS} data-row limit.")
    except csv.Error:
        raise ImportFileError("The CSV structure is malformed.") from None
    if any(len(row) > settings.LEAD_IMPORT_MAX_CSV_COLUMNS for row in rows):
        raise ImportFileError("The file exceeds the supported column limit.")
    return rows


def _precheck_csv_columns(text):
    """Count unquoted separators before csv.reader allocates per-row lists."""
    columns = 1
    in_quotes = False
    at_field_start = True
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            if in_quotes:
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 1
                else:
                    in_quotes = False
                    at_field_start = False
            elif at_field_start:
                in_quotes = True
            else:
                at_field_start = False
        elif char == "," and not in_quotes:
            columns += 1
            if columns > settings.LEAD_IMPORT_MAX_CSV_COLUMNS:
                raise ImportFileError("The file exceeds the supported column limit.")
            at_field_start = True
        elif char in "\r\n" and not in_quotes:
            columns = 1
            at_field_start = True
        elif not in_quotes:
            at_field_start = False
        index += 1


def _read_xml_part(archive, name):
    raw = archive.read(name)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError("XLSX XML parts must use UTF-8 encoding.") from None
    declaration = re.match(r"\s*<\?xml\b[^?]*\?>", text, re.IGNORECASE)
    if declaration:
        encoding = re.search(
            r"\bencoding\s*=\s*(['\"])(.*?)\1",
            declaration.group(0),
            re.IGNORECASE,
        )
        if encoding and encoding.group(2).casefold().replace("_", "-") not in {"utf-8", "utf8"}:
            raise ImportFileError("XLSX XML parts must use UTF-8 encoding.")
    if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ImportFileError("DTD and entity declarations are not supported.")
    return text


def _read_shared_strings(xml_text):
    shared = []
    stack = []
    try:
        for event, element in ET.iterparse(io.StringIO(xml_text), events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag.endswith("}si") or element.tag == "si":
                shared.append("".join(node.text or "" for node in element.iter() if node.tag.endswith("}t") or node.tag == "t"))
                if len(shared) > settings.LEAD_IMPORT_MAX_XLSX_SHARED_STRINGS:
                    raise ImportFileError("The workbook exceeds the supported shared-string limit.")
                if len(stack) > 1:
                    stack[-2].remove(element)
                element.clear()
            elif len(stack) > 1 and not any(node.tag.endswith("}si") or node.tag == "si" for node in stack):
                stack[-2].remove(element)
                element.clear()
            stack.pop()
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("The XLSX shared-string table is malformed.") from None
    return shared


def _read_worksheet(xml_text, shared_strings):
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    row_tag = f"{{{ns_main}}}row"
    cell_tag = f"{{{ns_main}}}c"
    formula_tag = f"{{{ns_main}}}f"
    value_tag = f"{{{ns_main}}}v"
    text_tag = f"{{{ns_main}}}t"
    inline_tag = f"{{{ns_main}}}is"
    stack = []
    rows_by_number = {}
    seen_rows = set()
    seen_cell_refs = set()
    actual_row_count = 0
    actual_cell_count = 0
    current_row_number = None
    current_values = None
    max_row = max_column = 0

    try:
        for event, element in ET.iterparse(io.StringIO(xml_text), events=("start", "end")):
            if event == "start":
                stack.append(element)
                if element.tag == row_tag:
                    actual_row_count += 1
                    if actual_row_count > settings.LEAD_IMPORT_MAX_XLSX_ROW_ELEMENTS:
                        raise ImportFileError("The workbook exceeds the supported worksheet row-element limit.")
                    current_row_number = int(element.attrib.get("r", "0"))
                    if current_row_number < 1 or current_row_number > settings.LEAD_IMPORT_MAX_ROWS + 1:
                        raise ImportFileError("The workbook exceeds the supported row limit.")
                    if current_row_number in seen_rows:
                        raise ImportFileError("The workbook contains duplicate row references.")
                    seen_rows.add(current_row_number)
                    current_values = {}
                continue

            if element.tag == cell_tag:
                if current_row_number is None or current_values is None:
                    raise ImportFileError("The worksheet contains a cell outside a row.")
                actual_cell_count += 1
                if actual_cell_count > settings.LEAD_IMPORT_MAX_XLSX_CELL_ELEMENTS:
                    raise ImportFileError("The workbook exceeds the supported cell-element limit.")
                ref = element.attrib.get("r", "")
                match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", ref)
                if not match:
                    raise ImportFileError("The workbook contains an invalid cell reference.")
                if int(match.group(2)) != current_row_number or ref in seen_cell_refs:
                    raise ImportFileError("The workbook contains duplicate or mismatched cell references.")
                seen_cell_refs.add(ref)
                column_number = 0
                for char in match.group(1):
                    column_number = column_number * 26 + ord(char) - 64
                if column_number > 100:
                    raise ImportFileError("The workbook exceeds the supported column limit.")
                if element.find(formula_tag) is not None:
                    raise ImportFileError("Formula cells are not supported; provide calculated values.")
                value_node = element.find(value_tag)
                cell_type = element.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in element.iter(text_tag))
                elif value_node is None:
                    value = ""
                elif cell_type == "s":
                    value = shared_strings[int(value_node.text)]
                elif cell_type == "b":
                    value = value_node.text == "1"
                else:
                    value = value_node.text or ""
                current_values[column_number - 1] = value
                max_column = max(max_column, column_number)
                stack[-2].remove(element)
                element.clear()
            elif element.tag == row_tag:
                rows_by_number[current_row_number] = current_values
                max_row = max(max_row, current_row_number)
                stack[-2].remove(element)
                element.clear()
                current_row_number = None
                current_values = None
            elif len(stack) > 1 and current_row_number is None:
                stack[-2].remove(element)
                element.clear()
            stack.pop()
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("The XLSX worksheet is malformed or unsupported.") from None

    if max_row == 0 or max_column == 0:
        return []
    rows = []
    for row_number in range(1, max_row + 1):
        values = rows_by_number.get(row_number, {})
        rows.append([values.get(column, "") for column in range(max_column)])
    return rows


def _read_xlsx(data):
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ImportFileError("File contents do not match an XLSX workbook.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_XLSX_MEMBERS or sum(item.file_size for item in infos) > MAX_XLSX_UNCOMPRESSED:
                raise ImportFileError("The workbook expands beyond the supported size.")
            if any(item.file_size > max(item.compress_size, 1) * MAX_XLSX_RATIO for item in infos):
                raise ImportFileError("The workbook compression ratio is not supported.")
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise ImportFileError("The file is not a valid XLSX workbook.")
            if any("vbaproject" in name.lower() or "xl/externallinks/" in name.lower() for name in names):
                raise ImportFileError("Macro-enabled or externally linked workbooks are not supported.")
            xml_parts = [name for name in names if name.endswith((".xml", ".rels"))]
            metadata_parts = {"[Content_Types].xml", "xl/workbook.xml"}
            metadata_parts.update(name for name in xml_parts if name.endswith(".rels"))
            if any(archive.getinfo(name).file_size > 1024 * 1024 for name in metadata_parts):
                raise ImportFileError("The workbook metadata exceeds the supported size.")
            xml_text = {name: _read_xml_part(archive, name) for name in xml_parts}
            content_types = xml_text["[Content_Types].xml"].lower()
            if "macroenabled" in content_types:
                raise ImportFileError("Macro-enabled workbooks are not supported.")

            for name, text in xml_text.items():
                if name.endswith(".rels"):
                    rel_root = ET.fromstring(text)
                    if any(rel.attrib.get("TargetMode", "").lower() == "external" for rel in rel_root):
                        raise ImportFileError("External workbook relationships are not supported.")

            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            shared_strings = (
                _read_shared_strings(xml_text["xl/sharedStrings.xml"])
                if "xl/sharedStrings.xml" in names
                else []
            )
            workbook = ET.fromstring(xml_text["xl/workbook.xml"])
            sheets = workbook.findall("m:sheets/m:sheet", ns)
            if len(sheets) != 1:
                raise ImportFileError("XLSX imports must contain exactly one worksheet.")
            relationship_id = sheets[0].attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            rel_root = ET.fromstring(xml_text["xl/_rels/workbook.xml.rels"])
            relationship = next((rel for rel in rel_root if rel.attrib.get("Id") == relationship_id), None)
            if relationship is None or relationship.attrib.get("TargetMode") == "External":
                raise ImportFileError("The XLSX worksheet relationship is invalid.")
            target = relationship.attrib.get("Target", "")
            if ".." in target.replace("\\", "/").split("/"):
                raise ImportFileError("The XLSX worksheet path is invalid.")
            worksheet_path = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
            )
            if (
                not worksheet_path.startswith("xl/worksheets/")
                or worksheet_path.startswith("../")
                or worksheet_path not in names
            ):
                raise ImportFileError("The XLSX worksheet path is invalid.")
            return _read_worksheet(xml_text[worksheet_path], shared_strings)
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("The XLSX workbook is malformed or unsupported.") from None


def _read_rows(upload):
    size_limit = settings.LEAD_IMPORT_MAX_FILE_SIZE
    if upload.size > size_limit:
        raise ImportFileError("File size exceeds the configured limit.")
    extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if extension not in {"csv", "xlsx"}:
        raise ImportFileError("Only .csv and .xlsx files are supported.")
    try:
        data = upload.read(size_limit + 1)
    except Exception:
        raise ImportFileError("The uploaded file could not be read.") from None
    if len(data) > size_limit:
        raise ImportFileError("File size exceeds the configured limit.")
    if not data:
        raise ImportFileError("The uploaded file is empty.")
    if extension == "csv":
        if data.startswith(b"PK"):
            raise ImportFileError("File extension does not match its contents.")
        rows = _read_csv(data)
    else:
        if not data.startswith(b"PK"):
            raise ImportFileError("File extension does not match its contents.")
        rows = _read_xlsx(data)
    return data, rows


def preview_upload(upload, *, duplicate_policy):
    data, rows = _read_rows(upload)
    if not rows:
        raise ImportFileError("The uploaded file is empty.")
    if len(rows) < 2 or not any(
        any(value not in (None, "") for value in row) for row in rows[1:]
    ):
        raise ImportFileError("The file contains no data rows.")
    if len(rows) - 1 > settings.LEAD_IMPORT_MAX_ROWS:
        raise ImportFileError(f"The file exceeds the {settings.LEAD_IMPORT_MAX_ROWS} data-row limit.")

    mapping, visible_mapping = _map_headers(rows[0])
    mapped_rows = []
    row_results = []
    serializer = LeadImportRowSerializer
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(value not in (None, "") for value in row):
            continue
        values = {field: row[index] if index < len(row) and row[index] is not None else "" for index, field in mapping.items()}
        values = {key: str(value) if not isinstance(value, str) else value for key, value in values.items()}
        row_serializer = serializer(data=values)
        if not row_serializer.is_valid():
            row_results.append({"row_number": row_number, "outcome": "invalid", "errors": row_serializer.errors})
            continue
        mapped_rows.append({"row_number": row_number, "data": row_serializer.validated_data})

    if not mapped_rows and not row_results:
        raise ImportFileError("The file contains no data rows.")

    existing = set()
    phone_candidates = {normalize_phone(item["data"]["phone"]) for item in mapped_rows}
    if phone_candidates:
        for phone in Lead.objects.only("phone").iterator(chunk_size=1000):
            normalized = normalize_phone(phone.phone)
            if normalized in phone_candidates:
                existing.add(normalized)

    seen = set()
    duplicate_count = 0
    importable_rows = []
    for item in mapped_rows:
        normalized = normalize_phone(item["data"]["phone"])
        is_duplicate = normalized in existing or normalized in seen
        seen.add(normalized)
        if is_duplicate:
            duplicate_count += 1
        if is_duplicate and duplicate_policy == "skip_duplicates":
            row_results.append({"row_number": item["row_number"], "outcome": "duplicate", "errors": []})
        else:
            importable_rows.append(item)
            outcome = "duplicate_allowed" if is_duplicate else "importable"
            row_results.append({"row_number": item["row_number"], "outcome": outcome, "errors": []})

    row_results.sort(key=lambda result: result["row_number"])
    summary = {
        "total": len(row_results),
        "valid": len(mapped_rows),
        "invalid": sum(result["outcome"] == "invalid" for result in row_results),
        "duplicate": duplicate_count,
        "importable": len(importable_rows),
        "requires_attention": sum(result["outcome"] in {"invalid", "duplicate"} for result in row_results),
    }
    payload = {
        "rows": importable_rows if duplicate_policy == "skip_duplicates" else mapped_rows,
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "mapping": {str(header): field for field, header in visible_mapping.items()},
        "row_results": row_results,
        "summary": summary,
    }
    return payload


def staged_data_digest(file_sha256, duplicate_policy, owner_id, rows, preview_rows):
    canonical = json.dumps(
        {
            "file_sha256": file_sha256,
            "duplicate_policy": duplicate_policy,
            "owner_id": owner_id,
            "rows": rows,
            "preview_rows": preview_rows,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
