# Milestone 6 — Institution-prepared PDFs and secure recipient delivery

Registrars upload institution-prepared PDFs, attest to the document and authorized
recipient, and issue documents through the existing order workflow. The app does
not invent academic results or generate transcripts from unverified information.

## Workflow

1. Submit an order with the recipient email and `secure_electronic` delivery.
2. The assigned registrar approves the items. Complete payment after approval,
   clear holds and outstanding questions, confirm any deferred release event,
   and mark every item ready.
3. Upload one institutional PDF per ordered item. Each upload is at most 2 MiB,
   has a simple PDF filename and PDF header/end marker, and must pass the configured
   ClamAV scanner. Scanner failure blocks upload. Files are stored privately in the
   database, outside the web assets directory, with an SHA-256 digest.
4. Use **Download registrar preview** to inspect the exact original bytes. This
   access is audited. The assigned registrar attests that the PDF and recipient
   match the authorized request, records private evidence, and selects **Issue document**.
5. Issuance creates an expiring delivery record for the recipient from the immutable
   consent snapshot. The recipient cannot be changed during issuance. A notification
   worker sends pending availability emails; **Send recipient notification** also
   sends or resends one immediately.
6. The recipient opens `/recipient#<delivery-id>`, supplies their email, and receives
   a random, single-use code in that mailbox. No recipient account is required.
   A matching email and delivery identifier alone cannot download the document.
7. The recipient enters the code to download. It expires after 15 minutes and is
   stored only as a hash. Each additional download needs a new code. Delivery
   access expires after seven days by default, configurable from one to thirty.
8. Students see preparation, issuance, revocation, notification and download status.
   Staff also see original filenames, document hashes and private release evidence.
   The order remains `submitted`; document items move `ready → issued → delivered`.
   `delivered` means the server accepted an authenticated download request, not
   proof that the browser saved it or that a human read it.

A quantity greater than one does not duplicate the PDF: the issued PDF represents
that item and its authorized recipient. Separate recipients require separate items.
Physical copies and postal/collection handover are not implemented by this milestone.
Orders containing those methods cannot be electronically released through a bypass.

## Release gates and revocation

Issuance and every recipient access recheck institution approval/activity, academic
record match, original consent, cancellation state, holds, outstanding student
questions, deferred release conditions and payment. A nonzero order requires one
matching successful unrefunded payment. Refund/dispute/review states stop access.
All items must be ready (or already issued), and each ready item must have an
unrevoked PDF in the current issuance mode before release begins.

An active institutional membership is required for staff access; being a platform
admin alone is insufficient. The assigned registrar uploads and issues. Staff
cannot handle their own documents. Only institutional managers can revoke, with a
reason visible to the student, including when payment or institutional approval
currently blocks normal fulfillment.

Revocation invalidates future downloads and outstanding codes, preserves the
original bytes/history, and returns the item to processing for correction. Mark it
ready again and upload a replacement to obtain a new delivery. The same process
can replace an expired delivery; links are never silently extended. Reopening an
item with an active prepared PDF requires revocation first. Deferred confirmation
cannot be withdrawn while issued items remain active.

Already downloaded files cannot be recalled. The app does not send automated
revocation notices or claim to invalidate a PDF outside this service. Notify affected
recipients through the institution's established process when correcting a release.
Once any document was issued, even if later revoked, the simple full-refund workflow
is blocked; that case needs a separate institutional investigation. Provider-reported
refunds/disputes still reconcile and block further access.

Mutations lock the institution then the order. Staff decisions require the current
`expected_version`. Concurrent issuance produces a single delivery; simultaneous
redemption of the same code permits only one download. Requesting a new code
invalidates the previous one. Email/code endpoints use database-backed rate limits
and return a generic code-request response for unknown or mismatched recipients.
No access code is placed in a URL or returned by the code-request API.

## Local setup

From `backend/`:

```bash
uv sync --locked --group dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Migration `0007` adds `issued_documents` and `document_deliveries`. Startup does not
apply migrations. Downgrade refuses to delete existing document history.

Keep issuance disabled until the scanner is installed with current malware
signatures. For a local demonstration, set these in your untracked `.env`:

```dotenv
ISSUANCE_ENABLED=true
ISSUANCE_MODE=demo
ISSUANCE_PUBLIC_URL=http://127.0.0.1:8000
DELIVERY_EXPIRE_DAYS=7
ATTACHMENT_SCANNER=clamav
CLAMAV_COMMAND=clamscan
MAIL_BACKEND=file
```

Use sample PDFs only. Demo notifications, workspace views and download filenames
are labeled DEMO. The PDF bytes themselves are preserved, not watermarked or
rewritten. Demo issuance accepts verified test payments; it cannot use live payments.
For development, notification emails and codes are written to the private `.mailbox`
directory using the existing file mail backend. No actual recipient is emailed by it.

Live mode requires `ISSUANCE_MODE=live`, an HTTPS public origin, ClamAV and SMTP with
STARTTLS. Production rejects demo mode. Paid live issuance requires a matching live
payment; test transactions never authorize live documents. Changing modes blocks
access to existing documents from the other mode. An emergency switch to
`ISSUANCE_ENABLED=false` stops new uploads, releases and recipient access.

Do not serve the database or mail directory publicly. Deployment must protect
backups and database storage, configure retention, and avoid logging request bodies
containing access codes. Header/footer checks and antivirus scanning are not full
PDF structural validation or proof of academic authenticity. The issuing institution
remains responsible for reviewing the content and any original digital signature.

## Notification worker

```bash
uv run python -m app.delivery_worker --limit 100
```

Schedule this command periodically (for example every minute) in the deployment.
It sends pending notifications and waits at least five minutes between failed
attempts. Failed sends remain retryable; expired and revoked documents are skipped.
Workers recheck delivery state under the order lock. SMTP acceptance is recorded
separately from authenticated downloads. A crash after email acceptance but before
commit may cause a duplicate notification; notifications contain no PDF or access
code and do not grant access by themselves. This is an at-least-once notification
mechanism, not an email bounce or delivery-confirmation integration.

## API

All paths start with `/api/v1`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/orders/{order_id}/documents` | Owner's document/delivery history |
| GET | `/staff/institutions/{institution_id}/orders/{order_id}/documents` | Staff history and release blockers |
| POST | Same staff path | Multipart `file`, `item_key`, `expected_version` upload |
| GET | Same staff path + `/{document_id}/preview` | Audited registrar PDF download |
| POST | Same staff path + `/{document_id}/issue` | Version, `attested`, `internal_note` |
| POST | Same staff path + `/{document_id}/revoke` | Manager version and public `reason` |
| POST | Same staff path + `/{document_id}/notify` | Version; send/resend recipient notification |
| POST | `/deliveries/{delivery_id}/access-codes` | Recipient `email`; generic 202 response |
| POST | `/deliveries/{delivery_id}/download` | Single-use `code`; PDF attachment |

Recipient endpoints use email-code authorization instead of account JWTs. They do
not expose document metadata publicly. PDF responses force attachment download,
use generated filenames, `no-store`, `nosniff`, a sandbox content policy, and a
SHA-256 response header. Private PDFs are not exposed through static URLs.

## Validation and boundaries

```bash
uv run pytest -q tests/test_issuance.py tests/test_foundation.py tests/test_fulfillment.py
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser
```

Use a disposable PostgreSQL `TEST_DATABASE_URL` named `transcriptske_test*` for
concurrency tests. Tests use fake malware scanners, email and payment providers;
actual ClamAV, SMTP and provider acceptance must be checked during deployment.

Deferred: PDF generation from SIS data, institution signing keys and signature
validation, public credential verification, postal dispatch and collection receipts,
carrier integrations, document retention automation, object storage, and external
email delivery/bounce callbacks. Milestone 7 implements pilot operations queues and deployment diagnostics; see the
[Milestone 7 guide](milestone-7.md). End-to-end acceptance with real institution
processes and configured providers remains required.

Security reference: [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).
