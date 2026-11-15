# Lead spreadsheet import contract

## File and column contract

The administrator preview endpoint accepts UTF-8 CSV or a single-sheet XLSX
workbook. Headers are compared case-insensitively after removing spaces and
punctuation. Supported aliases are:

| Lead field | Accepted headers |
| --- | --- |
| `name` | Student name, customer name, full name, name, student |
| `phone` | Primary phone, phone number, mobile, mobile number, contact number |
| `alternate_phone` | Alternate phone, alt phone, secondary phone, alternate mobile |
| `email` | Email, email address |
| `course_interest` | Course, course interest, enquiry course, program |
| `enquiry_details` | Enquiry, enquiry details, message, requirements |
| `internal_notes` | Notes, internal notes, admin notes |

Student name and primary phone are required. Unknown, duplicate-mapped,
ownership, status, archive, and system headers are rejected. Empty spreadsheet
rows are ignored; row numbers in results remain the original row numbers.
Imported values are validated and trimmed. XLSX formula cells are rejected and
never evaluated.

Guardrails are configurable in `config/settings.py`:

- Request body: 6 MiB, rejected before multipart parsing when `Content-Length`
  exceeds the limit. Requests without `Content-Length` receive HTTP 411.
- File: 5 MiB, also checked by the importer and streamed upload handler.
- Data rows: 5,000.
- CSV/XLSX columns: 100. CSV separators are counted before `csv.reader`
  allocates cell lists.
- XLSX XML row elements: 5,001, including the header row.
- XLSX cell elements: 100,000.
- XLSX shared strings: 100,000; workbook metadata XML parts: 1 MiB each.
- Imported enquiry details and internal notes: 5,000 characters each.
- XLSX ZIP entries: 1,000; total expanded content: 25 MiB; per-entry
  compression ratio: at most 100:1.
- Stage lifetime: 30 minutes. Abandoned processing recovery threshold: 60
  minutes.

These Django limits apply after the request reaches the application. The reverse
proxy and application server must independently enforce a request-body limit
before buffering or forwarding uploads; set each upstream limit to no more than
the Django 6 MiB request limit. A proxy limit slightly above 5 MiB may be needed
to allow for multipart boundaries and headers, while still keeping the whole
request bounded. The application continues to enforce the 5 MiB file limit
inside that request allowance. Missing-length and unknown-length/chunked uploads
are rejected by the application with HTTP 411; they must not be exempted from
upstream request limits. The production proxy and application server have not
been selected or verified here, so deployment-level limit verification remains
pending.

XLSX XML must be UTF-8. Worksheet rows and cells and shared strings are parsed
incrementally, counted by actual XML elements, and rejected at the limits.
DTD/entity declarations, external relationships,
macro-enabled workbooks, external-link parts, malformed references, duplicate
cell/row references, and multiple worksheets are rejected before row data is
staged. Parser failures return fixed safe messages; row validation errors retain
only row numbers and field-level messages, not submitted values.

## Duplicate handling

Phone comparison normalization removes every character except ASCII digits. If
no digits remain, the trimmed value is compared case-insensitively. This
normalization is used only for duplicate detection; the trimmed submitted phone
is stored unchanged. For example, `+91 98765-43210` and `919876543210` compare
equal.

The default `skip_duplicates` policy checks existing CRM records during preview
and again during commit, and skips subsequent duplicate rows in the same file.
It is best-effort when separate imports run concurrently: both can check before
either inserts, so simultaneous commits may still create duplicate phone
records. The `allow_duplicates` policy explicitly allows them. No global phone
uniqueness constraint is used because multiple enquiries from one phone are
legitimate.

The selected policy, owner, original file SHA-256, staged validated rows, and
preview row outcomes are bound by the staged payload digest. Commit accepts
only the staged ID and one-time token; it does not accept replacement row data
or a replacement policy.

## Commit state and recovery

The import states are `STAGED`, `PROCESSING`, `COMMITTED`, `FAILED`, `EXPIRED`,
and `CANCELLED`.

1. Commit locks a stage and validates owner, token, expiry, state, and staged
   payload hash.
2. It changes `STAGED` to `PROCESSING` and commits that claim in its own
   transaction.
3. A separate transaction validates and inserts every importable Lead, then
   marks the stage `COMMITTED` and clears staged rows.
4. Insert or validation failures roll back all Lead creation, then mark the
   stage `FAILED` and clear staged rows. If the process or database fails before
   that final update, the durable `PROCESSING` state remains unusable.

The stage claim and Lead inserts are intentionally not one atomic transaction.
If the process crashes before or during insertion, no token retry can resume
the stage. A crash before transaction commit rolls back all Lead inserts. A
claim abandoned in `PROCESSING` is failed, never resumed, by the recovery
command. The recovery command locks eligible rows and skips rows currently
locked by active commit transactions.

Operators should schedule both commands periodically:

```sh
python manage.py cleanup_staged_lead_imports
python manage.py recover_staged_lead_imports
```

Cleanup erases expired staged row data. Recovery erases rows for `PROCESSING`
stages older than the configured threshold. Both commands are safe to run
repeatedly. Cancellation locks the stage and succeeds only from `STAGED`, so it
cannot invalidate an already claimed import. Committed, failed, expired, and
cancelled stages cannot be reused.
