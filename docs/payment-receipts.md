# Payment receipts and order confirmations

A trusted successful provider callback or reconciliation records payment, releases
pay-first orders to the school queue, and stores one receipt outbox row per payment
in the same database transaction. Pending, declined and rolled-back transactions
produce no email. Historical payments are not automatically emailed by migration.

The receipt freezes the accepted institution, academic-record name, document/item
IDs, services, quantities, prices and destinations. It excludes admission numbers,
national IDs and dates of birth. The paying student's verified account email gets
the order confirmation and attached PDF; transcript recipients do not receive the
financial receipt. Each requested item lists its own destination and method.

The Redis/Celery receipt task polls committed work every ten seconds. Failed email
attempts retry after one minute. PostgreSQL row locks and a unique payment key
prevent concurrent duplicate dispatch. An SMTP acceptance followed by a database
crash can still cause redelivery; a stable Message-ID helps identify duplicates.
SMTP acceptance is not proof of inbox delivery. Email failure never reverses a
confirmed payment. Worker health includes `receipts`.

Students can download their own receipt using the **Download PDF receipt** action:
`GET /api/v1/orders/{order_id}/payments/{payment_id}/receipt.pdf`.
Verified owner authentication is required, unpaid attempts return 409, and responses
are marked `Cache-Control: no-store`. The existing JSON receipt endpoint remains.
Receipt document IDs refer to ordered items, not to subsequently issued PDFs.
Receipts acknowledge the original charge; refunds remain separately audited in
payment history. These documents are not tax invoices or academic credentials.

With `MAIL_BACKEND=file`, multipart emails and PDF attachments are written privately
to `backend/.mailbox/`. Set `MAIL_BACKEND=smtp` and configure the existing SMTP
settings for real email delivery. Apply migration 0018 and restart workers after
updating code: `uv run alembic upgrade head`, then `uv run python -m app.jobs restart`.

After payment, institution staff review the academic record, ask questions when
needed, approve requested items, prepare and upload institutional PDFs, attest to
and issue them. Demo institutions require a human using their demo staff account;
they have no automatic connection to a university database. Students follow the
order timeline, messages, holds and notifications in the workspace. Issued electronic
documents send a separate secure-link email to the chosen destination recipient.
