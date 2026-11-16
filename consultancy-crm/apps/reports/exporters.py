"""Safe CSV and standard-library OpenXML (.xlsx) report exporters."""

import csv
from datetime import date, datetime
from decimal import Decimal
import io
import re
from xml.sax.saxutils import escape as xml_escape
import zipfile
from zoneinfo import ZoneInfo

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")

FORMULA_TRIGGER_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# XML 1.0 disallows control characters except \t (0x09), \n (0x0A), and \r (0x0D).
INVALID_XML_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]"
)

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def clean_xml_text(value: str) -> str:
    """Remove characters illegal in XML 1.0 documents."""
    return INVALID_XML_CHARS_RE.sub("", value)


def is_numeric_cell(value) -> bool:
    """Return True for int, float, or Decimal values (excluding booleans)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float, Decimal))


def format_temporal_or_scalar(value):
    """Normalize datetimes, dates, booleans, and None for export cells."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(KOLKATA_TZ).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if is_numeric_cell(value):
        return value
    return str(value)


def neutralize_formula_string(text: str) -> str:
    """Prefix untrusted strings that could be interpreted as spreadsheet formulas."""
    if not text:
        return text
    stripped = text.lstrip(" \n")
    if text.startswith(FORMULA_TRIGGER_PREFIXES) or stripped.startswith(
        FORMULA_TRIGGER_PREFIXES
    ):
        return f"'{text}"
    return text


def sanitize_cell_value(value):
    """Prepare a cell value for CSV/XLSX while preserving genuine numeric types."""
    normalized = format_temporal_or_scalar(value)
    if is_numeric_cell(normalized):
        return normalized
    text = clean_xml_text(str(normalized))
    return neutralize_formula_string(text)


def sanitize_filename(name: str) -> str:
    """Ensure Content-Disposition filenames contain only safe characters."""
    cleaned = SAFE_FILENAME_RE.sub("_", name).strip("._-")
    return cleaned or "report"


def _excel_column_name(index_zero_based: int) -> str:
    """Convert 0-based column index to Excel column letters (A, B, ..., Z, AA...)."""
    num = index_zero_based + 1
    letters = []
    while num > 0:
        num, remainder = divmod(num - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def render_csv_bytes(headers, rows) -> bytes:
    """Render headers and rows into UTF-8 CSV bytes with formula injection protection."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([sanitize_cell_value(h) for h in headers])
    for row in rows:
        writer.writerow([sanitize_cell_value(cell) for cell in row])
    return buffer.getvalue().encode("utf-8")


def _build_worksheet_xml(headers, rows) -> bytes:
    """Build xl/worksheets/sheet1.xml using explicit inlineStr and numeric cells."""
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    out.write(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    )
    out.write("<sheetData>")

    def write_row(row_number: int, values):
        out.write(f'<row r="{row_number}">')
        for col_idx, raw_val in enumerate(values):
            col_ref = f"{_excel_column_name(col_idx)}{row_number}"
            cell_val = sanitize_cell_value(raw_val)
            if is_numeric_cell(cell_val):
                out.write(f'<c r="{col_ref}" t="n"><v>{cell_val}</v></c>')
            else:
                escaped = xml_escape(
                    str(cell_val),
                    entities={'"': "&quot;", "'": "&apos;"},
                )
                out.write(
                    f'<c r="{col_ref}" t="inlineStr">'
                    f'<is><t xml:space="preserve">{escaped}</t></is>'
                    f"</c>"
                )
        out.write("</row>")

    write_row(1, headers)
    for idx, row in enumerate(rows, start=2):
        write_row(idx, row)

    out.write("</sheetData></worksheet>")
    return out.getvalue().encode("utf-8")


_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
""".strip().encode("utf-8")

_ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
""".strip().encode("utf-8")

_WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Report" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
""".strip().encode("utf-8")

_WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""".strip().encode("utf-8")

_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>
""".strip().encode("utf-8")


def render_xlsx_bytes(headers, rows) -> bytes:
    """Render headers and rows into a valid OpenXML .xlsx archive in memory."""
    worksheet_bytes = _build_worksheet_xml(headers, rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _ROOT_RELS_XML)
        zf.writestr("xl/workbook.xml", _WORKBOOK_XML)
        zf.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS_XML)
        zf.writestr("xl/styles.xml", _STYLES_XML)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_bytes)
    return buffer.getvalue()
