# Milestone 7 — Pilot operations and deployment diagnostics

This milestone adds an institution manager dashboard, planning targets for submitted
orders, payment/delivery exception queues, audited follow-up notes, worker run
history, and read-only deployment diagnostics. It does not enable payments,
issuance, scheduled jobs or external email automatically.

## Manager workflow

Sign in as an active, verified institutional manager and select the institution in
the staff workspace. **Pilot operations** shows counts and a paginated order queue.
Choose a category, open the existing order workflow to perform authorized actions,
or select **Review follow-up** to record a private institutional note and optional
future review time. Refresh the dashboard after changes to see current counts.

Counts represent distinct orders, even when an order has several payments,
documents, messages or holds. Drafts and cancelled drafts never appear. Submitted
orders remain in the all-orders history after cancellation or issuance. Queue rows
show order reference, workflow/payment state, planning target and matching flags;
they do not return academic details, recipients, access codes, provider payloads or
checkout links. Each request rechecks institution membership and manager permission.
Platform administrators need an actual manager membership for these endpoints.

| Queue | Included orders |
| --- | --- |
| Past planning target | Submitted orders with unfinished review/processing/ready items and an elapsed target |
| Assignment needed | Open fulfillment without a verified, active eligible registrar assignment |
| Active holds | Submitted/cancellation-requested orders with unresolved holds |
| Awaiting student response | Submitted/cancellation-requested orders with unanswered required questions |
| Payment exceptions | Unknown attempts; pending/initiating attempts older than the configured threshold; refund requests/pending refunds, disputes, partial refunds, review flags; old unprocessed webhook records |
| Delivery exceptions | Unrevoked documents with failed notification attempts, overdue pending notifications, or no download with access expiring within 24 hours/already expired |
| Cancellation requests | Submitted orders awaiting a cancellation decision |
| Ready documents | Orders with at least one ready item; this is not a guarantee that release checks pass |
| Follow-ups due | Manager-scheduled follow-ups whose date has arrived, including on closed workflows |
| Missing planning target | Open fulfillment where the submitted quote could not provide a valid target |

Fresh pending payments and newly queued email notifications have a grace period.
An unknown provider outcome is flagged immediately, including attempts with no
provider reference that the reconciliation worker cannot safely retry. Inspect the
original workflow and provider evidence; there is no operations override to mark
an order paid or to bypass recipient consent, holds, scanning or release checks.
Cancelled orders can still appear for unresolved payment/delivery exceptions.

Follow-up updates require the current order version and lock the institution/order.
A stale update returns `409`. Managers cannot review their own order. Notes and
scheduled follow-ups are recorded in the private registrar audit trail; existing
institutional staff with registrar-history access can read that history. Students
see only a generic progress-review event, never the private note or follow-up date.
The dashboard's case history shows the newest 50 operations entries. Clearing a
follow-up records the change; it does not resolve an underlying exception.

## Planning targets

At the first successful submission, `orders.processing_due_at` is calculated from
that order's immutable quoted `processing_days_max` values, using the largest value.
Count Monday–Friday dates after the submission date in `Africa/Nairobi`, then set
the target to 23:59:59 Nairobi time on the resulting date. A zero-day service targets
the end of the submission date, including if submitted on a weekend.

For example, a one-day quote submitted on Friday targets Monday evening. Later
catalog changes and submission retries do not move the target. Migration `0008`
backfills existing submitted orders from their original snapshots, in batches.
Missing or invalid historic timing stays unknown and is flagged rather than guessed.
The migration does not change quotes, consent hashes, prices, or workflow states.

These are **planning targets, not contractual SLAs or guaranteed delivery dates**.
Public holidays, payment waits, holds, staffing and deferred release events do not
pause or extend the clock. A deferred order can therefore appear overdue while
correctly waiting for graduation or grades. Managers see that release condition in
the queue. Formal institutional calendars and paused SLA clocks remain future work.
Issuing all items removes an order from fulfillment overdue counts; recipient
notification/download problems remain visible in delivery exceptions.

## Configuration and migration

From `backend/`:

```bash
uv sync --locked --group dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Migration `0008` adds the planning target column, `operations_cases`, and
`worker_runs`. Apply it before starting the updated application and workers. It
preserves existing accounts, payments and issued documents. Startup does not apply
migrations. Back up before migration; downgrading removes operations case state,
planning targets and worker history, while previously written registrar audit events
remain. Test rollback/restoration on a copy before operating on production data.

Optional `.env` settings (defaults shown):

```dotenv
OPERATIONS_PAYMENT_PENDING_MINUTES=30
OPERATIONS_NOTIFICATION_PENDING_MINUTES=15
OPERATIONS_WORKER_STALE_MINUTES=15
```

All are bounded from 5 to 1,440 minutes. Threshold changes affect queue/readiness
classification, not provider verification, document expiry or existing follow-ups.

## Worker health and scheduling

Existing worker commands now write a `running` record before processing and finish
with `succeeded` or `failed`, counts, and timestamps. Unexpected exceptions are
recorded as failed and re-raised. A hard process kill can leave a running record;
if it remains the latest run and becomes stale, readiness flags it. A later
successful run restores the worker health indication. Worker history contains no email addresses, tokens,
provider responses or exception text. It is operator-level data, not an
institution dashboard endpoint that could expose another institution's workload.

```bash
uv run python -m app.payment_worker --limit 100
uv run python -m app.delivery_worker --limit 100
```

Configure an external scheduler to run the required workers regularly, typically
every minute, and alert on nonzero exits and stale/failed readiness checks. These
commands perform their existing provider reconciliation and email work; the
readiness command below does not execute either worker. Payment workers never
initiate new charges; delivery retries preserve the existing delivery record.

A successful empty run proves the worker executed, not that a provider is reachable.
Per-item failures require investigation through the relevant order workflows. New
heartbeat records do not alter the existing retry policies, idempotency rules,
refund restrictions or notification delivery guarantees. Set an operator-approved
retention/archival policy for worker history; no automatic deletion is implemented.

## Read-only deployment diagnostics

```bash
uv run python -m app.readiness
uv run python -m app.readiness --live
```

The command emits JSON and exits zero only when its configuration checks pass.
It checks database connectivity and the Alembic head, application secret settings,
mail configuration, scanner executable availability when issuance is enabled,
provider credential completeness and outstanding-payment account/mode bindings,
the active approved pilot institution, and fresh
successful runs for workers required by enabled features or outstanding work.
Worker checks include the last run status and start/finish timestamps.
The `--live` target additionally requires production mode, enabled live payments
and issuance, SMTP/STARTTLS, a non-placeholder sender, and HTTPS public origins.

Configuration validation and connection errors do not print secret values or raw
connection strings. This tool performs database reads and local executable lookup;
it does not call providers, send mail, scan documents, apply migrations, or change
configuration. Missing database access or migrations fail closed.

**A passing report is not launch approval.** The report always includes manual
acceptance work: actual provider checkout/callback/refund behavior, scanner tests
and current signatures, SMTP receipt and recipient access/revocation, HTTPS/proxy
behavior, sensitive-log handling, backups/restoration, staff access review and the
institution's operating procedures. Credential presence and executable presence
alone cannot establish those outcomes. Email bounce tracking, a queue for account
verification email, and external monitoring integrations remain outside this scope.

`GET /health` remains the liveness endpoint. `GET /health/ready` checks only database
connectivity and the migration head; it returns `200 {"status":"ready"}` or
`503 {"status":"unavailable"}` with `Cache-Control: no-store`. It exposes no
configuration, credentials, institution counts or worker history. Worker failures
do not remove the management UI from service; inspect them with the private CLI.

## Manager API

Prefix: `/api/v1/staff/institutions/{institution_id}/operations`.

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET | `/summary` | Distinct order counts, timestamp and timing policy |
| GET | `/queue` | `kind`, `offset`, `limit` (default 30, maximum 100) |
| GET | `/orders/{order_id}` | Private follow-up state and recent audit history |
| PUT | `/orders/{order_id}` | `expected_version`, `note`, optional timezone-aware `follow_up_at` |

Queue kinds: `all`, `overdue`, `assignment_required`, `holds`, `awaiting_student`,
`payments`, `deliveries`, `cancellations`, `ready`, `follow_up`, `missing_target`.
Results sort by planning target, then submission time and ID; unknown targets sort
last. Pagination is an offset view of current data, not a frozen export. A follow-up
must be in the future and within one year, or null to clear it.

## Validation and next work

```bash
uv run pytest -q tests/test_operations.py tests/test_foundation.py
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser
```

PostgreSQL locking tests require a disposable `TEST_DATABASE_URL` whose database
name begins `transcriptske_test`. Tests use fake mail and payment providers.

Next: real institution pilot acceptance, operational ownership and monitoring,
backup/restore exercises, institution-specific SLA calendars, production provider
acceptance, and independently scoped MFA/SIS/credential-verification work. No real
provider acceptance or production deployment is claimed by this milestone.
