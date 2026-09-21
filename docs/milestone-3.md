# Milestone 3 — Ordering, consent and tracking

Students can save an order, select documents and destinations, attach supporting
information, review a quote, authorize release, submit, and communicate with their
institution. The workspace at `/workspace` exposes these actions in **My orders**.
Institution staff see submitted orders in their institution workspace.

## Try the workflow

From `backend/`, install dependencies and migrate your development database:

```bash
uv sync --locked --group dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Use the demo institutions from Milestone 2. A student needs a verified account and
an academic record confirmed by a different, authorized institutional staff member.
Sign in, open **My orders**, select that record, and create a draft. Add recipients,
document items and a purpose; save, optionally attach supporting information, and
request a quote. Review every destination, document, fee and release instruction,
then tick the release authorization and submit.

Sign in as institution staff to review the submitted details, ask for information,
and decide cancellation requests. The student can reply to a specific question;
that question then stops appearing as unanswered. Tracking is refreshed on demand.
Draft cancellation is immediate. A submitted order requires a staff decision.

## Rules and boundaries

- One order belongs to one account, institution and academic record. Each of up to
  20 document items references one of up to 10 recipients. Copies range from 1–10;
  repeated service/destination pairs use quantity rather than duplicate items.
- Electronic delivery requires email; postal delivery requires a full address.
  The service must offer the selected method. Collection is also supported.
- Release instructions support now, after grades, or after graduation. Deferred
  release requires an explanation. These are recorded instructions; registrar
  enforcement and actual release belong to Milestone 4.
- Fees come exclusively from the institution catalog in integer KES minor units.
  Total = sum of unit fee × copies for each item. There are no additional delivery
  fees or taxes in this milestone; do not advertise unconfigured charges.
- Quotes expire after 15 minutes. Consent records the exact wording/version,
  authenticated account, timestamp, quote and SHA-256 of its complete scope.
  Draft edits, attachment edits, changed fees/policy or a changed academic match
  require a fresh quote and consent. Availability and requirements are checked again
  at submission. A submitted snapshot remains unchanged by later catalog edits.
- `expected_version` prevents stale writes. Mandatory `Idempotency-Key` headers on
  creation, submission and repeat-order requests prevent duplicate effects on retry.
  Keys use 8–100 letters, digits, hyphens or underscores. A different creation
  payload cannot reuse an account's key. Repeated submission must use the same key,
  quote and consent. Keep keys until the request succeeds.
- PostgreSQL locks the institution before the order, consistently with matching and
  catalog writes. It serializes competing edits, consent and submission. This
  intentionally favors correctness over high-volume write throughput.
- A repeat order creates a fresh draft with copied selections. It does not copy
  attachments, quotes or consent, and must pass current service/price checks.
- Only the owner accesses drafts, including cancelled drafts. Active institution
  members can access submitted orders for their institution. Platform-admin status
  alone grants no access. Staff cannot message or adjudicate their own orders.
- Cancellation is limited to uncharged orders whose items have not progressed past
  awaiting review. Approval rechecks these gates. Rejection preserves the submitted
  order and its history. Refunds and paid-order cancellation need payment logic later.
- Messages are in-app, with actor, time and an optional reply relationship. Messages
  and decisions append timeline events. No recipient emails or order emails are sent.

## Private attachments

Up to five files of 2 MiB each are stored transactionally in PostgreSQL, with size,
content hash, scan method and filename. Blob columns are deferred so listing orders
does not load attachment contents. Downloads require owner/institution authorization
and force download with `no-store`, `nosniff` and sandbox headers. No public file URL
is created. Attachments are immutable after submission and included in consent.

UTF-8 `.txt` files are available by default; binary/control-character content is
rejected. PDF, PNG and JPEG also require a matching signature and a successful
ClamAV scan. To enable these, install `clamscan`, maintain its malware signatures,
and set:

```dotenv
ATTACHMENT_SCANNER=clamav
CLAMAV_COMMAND=clamscan
```

Scanner absence, failure or a 20-second timeout rejects the upload. Infected files
are rejected. The suite tests scanner failure and clean/infected results using a
fake; it does not certify a deployed ClamAV installation. The application limits
multipart streams as well as individual file sizes. Large-scale storage, retention
policies, quotas and asynchronous scanning remain deployment work. Supporting
attachments are user-provided material, not officially issued academic documents.

## API

All routes start with `/api/v1`. Except the consent wording and upload-policy routes,
verified authentication is required. Lists of orders support `offset` and `limit`
(default 30, maximum 100). Component keys are stable client-generated strings.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/orders/consent-text` | Current authorization wording/version |
| GET | `/orders/attachment-policy` | Enabled file formats and upload limits |
| POST / GET | `/orders` | Create draft / list own orders |
| GET / PUT | `/orders/{id}` | Read / atomically replace draft details |
| POST | `/orders/{id}/recipients` | Add recipient |
| PUT / DELETE | `/orders/{id}/recipients/{key}` | Edit / remove recipient |
| POST | `/orders/{id}/items` | Add document item |
| PUT / DELETE | `/orders/{id}/items/{key}` | Edit / remove item |
| POST | `/orders/{id}/validation` | Check current submission requirements |
| POST | `/orders/{id}/quotes` | Calculate and persist a quote |
| POST / GET | `/orders/{id}/consents` | Accept exact quote / read authorization history |
| POST | `/orders/{id}/submit` | Submit with quote, consent and expected version |
| POST | `/orders/{id}/reorder` | Create a repeat draft |
| POST | `/orders/{id}/attachments` | Multipart `file` and `expected_version` |
| DELETE | `/orders/{id}/attachments/{attachment_id}` | Remove a draft attachment |
| GET | `/orders/{id}/attachments/{attachment_id}/download` | Private download |
| GET | `/orders/{id}/timeline` | Versioned event history |
| GET / POST | `/orders/{id}/messages` | Read messages / send or reply |
| POST | `/orders/{id}/cancellation-requests` | Cancel draft / request cancellation |

Institution routes use the prefix `/staff/institutions/{institution_id}`:

| Method | Path after prefix | Purpose |
| --- | --- | --- |
| GET | `/orders` | Submitted-order queue |
| GET | `/orders/{id}` | Read authorized order details |
| GET | `/orders/{id}/timeline` | Read event history |
| GET / POST | `/orders/{id}/messages` | Read / send information requests or messages |
| GET | `/orders/{id}/attachments/{attachment_id}/download` | Private attachment download |
| POST | `/orders/{id}/cancellation-decisions` | Approve/reject with version and reason |

Full schemas and required fields are available in `/docs`. Draft `PUT` replaces
all items and recipients; omitted collections become empty. DELETE operations take
`expected_version` in the query string. Submitted orders have no edit/delete route.

## Validation and next milestones

```bash
uv run pytest -q tests/test_orders.py tests/test_foundation.py
uv sync --locked --group browser
uv run --group browser playwright install chromium
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser
```

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database named
`transcriptske_test*` for locking/concurrency checks. Tests migrate isolated schemas;
they do not use the configured application database or mail provider.

Registrar review, holds and fulfillment states are now covered by the
[Milestone 4 guide](milestone-4.md). Next come M-Pesa and
Stripe or another card gateway with verified payment webhooks, reconciliation and
refunds (Milestone 5). Orders currently remain `payment_status=not_started`; submission
is a request, not proof of payment, issuance or delivery. Official PDFs, secure
recipient delivery, SIS integrations and third-party ordering remain later work.
