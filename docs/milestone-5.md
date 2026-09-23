# Milestone 5 — Payments, receipts, reconciliation and refunds

Students can pay an approved document order through Stripe-hosted card checkout or
M-Pesa Express STK Push. Managers can review full-refund requests. Provider
confirmation updates the order and an append-only charge/refund ledger. The
workspace includes payment status, receipts and institutional reconciliation controls.

**Payments are disabled by default.** No real payment or refund was made while
implementing this milestone. Tests use simulated providers. Actual Stripe test-mode
and Daraja sandbox acceptance must be completed with your merchant credentials and
public webhook configuration before live use.

## Collection policy

The agreed policy is **pay after registrar approval**. Every document must be in
`processing` or `ready`; the institution, record match and consent must still be
valid. Active holds, unanswered questions, cancellation or an existing unresolved
payment prevent a new request. Deferred graduation/grades instructions do not stop
payment after approval, but still block completion of preparation until confirmed.

Amounts come from the immutable submitted quote in KES minor units. The browser
cannot supply an amount or alter fees. Zero-fee orders need no payment. M-Pesa is
offered only for whole-shilling totals; fractional totals must use card payment.
The app never rounds a price or silently adds gateway fees.

This is a **single authorized pilot merchant configuration**, explicitly restricted
to `PAYMENT_INSTITUTION_ID`. It is not institution-by-institution merchant routing,
Stripe Connect, split settlement or a platform payout system. Do not enable one
institution's merchant credentials for another institution's orders.

## Local setup

From `backend/`:

```bash
uv sync --locked --group dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Migration `0006` adds payment attempts, refunds, webhook inbox records, financial
events and ledger entries. It allows a null actor for provider-generated order events
rather than falsely attributing those events to a student. Existing orders and
consent are preserved. Downgrade is blocked once system order events exist, to avoid
misrepresenting financial audit history; back up before migrations.

Edit the local `.env` using the entries in `.env.example`. Keep secrets out of Git.
Enable only the provider(s) you configure:

```dotenv
PAYMENTS_ENABLED=true
PAYMENT_MODE=test
PAYMENT_INSTITUTION_ID=YOUR_APPROVED_INSTITUTION_ID
PAYMENT_PUBLIC_URL=https://your-public-test-origin.example
```

Use a random application `SECRET_KEY` of at least 32 characters. Payment callbacks
use purpose-specific HMAC tokens derived from it, so do not rotate this key with
outstanding M-Pesa callbacks without a recovery plan. Payment API keys and mode are
bound to each attempt; retain the original configuration while reconciling it.

`PAYMENTS_ENABLED=false` stops new collection but still permits existing payments
to be verified with their original configured credentials. Live collection requires
HTTPS and an appropriate live Stripe key; production cannot enable test payments.
Use separate test and live data/configuration. Test receipts are explicitly labeled
and test payments do not authorize production issuance.

## Stripe cards

Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`. Card numbers and security codes
are entered on Stripe's hosted page; the application has no card-input endpoint.
Requests pin Stripe API version `2025-02-24.acacia`. Configure the webhook endpoint
with the same API version and your merchant's required account/currency settings.

Webhook URL:

```text
https://YOUR_ORIGIN/api/v1/payments/webhooks/stripe
```

Subscribe to `checkout.session.completed`, `checkout.session.expired`,
`checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
`charge.refunded`, `refund.updated`, `charge.dispute.created`,
`charge.dispute.updated`, and `charge.dispute.closed`.

For local forwarding, use Stripe CLI with your test account:

```bash
stripe listen --forward-to localhost:8000/api/v1/payments/webhooks/stripe
```

Put the listener's signing secret in your local `.env` and restart the app. The
redirect returns to `/workspace`; it never changes payment state. Checkout opens in
a separate tab so the student's existing workspace remains available.

Raw webhook bodies are authenticated with the Stripe signature and a five-minute
time tolerance. The handler then retrieves the current Checkout Session and
PaymentIntent/charge, checking local metadata, reference, amount, currency and mode.
A signed event name alone is not sufficient. API retrieval also detects external
partial/full refunds and disputes; these block fulfillment as appropriate.

References: [Checkout creation](https://docs.stripe.com/api/checkout/sessions/create),
[webhook signatures](https://docs.stripe.com/webhooks/signature),
[idempotency](https://docs.stripe.com/api/idempotent_requests),
[refund creation](https://docs.stripe.com/api/refunds/create), and
[API version pinning](https://docs.stripe.com/changelog/acacia/2025-02-24/afterpay-shipping-details-optional).

## M-Pesa

Set `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE` and
`MPESA_PASSKEY`. This adapter uses **CustomerPayBillOnline**, not Buy Goods/Till
integration. Test mode uses Daraja sandbox; live mode uses the live API. The
configured public origin must be HTTPS and reachable by Safaricom.

The student enters their own Kenyan mobile number, which is normalized before the
STK request. The app never asks for an M-Pesa PIN. Browser responses show only the
last four digits. Amounts and destination shortcode are server-controlled.

Each outbound request includes a callback URL bound to that payment with an
unguessable HMAC token. Callbacks must match the known request, phone and amount;
a successful receipt is stored only after a server-to-server STK query confirms
success. The callback token is our integration's authentication mechanism, **not a
Safaricom signature header**. Uvicorn access logs redact these callback query
strings. Configure reverse proxies, monitoring and tracing to redact them too.

STK timeouts remain uncertain until authoritative verification. An accepted STK
request is not proof of payment. If the request response is lost before a checkout
reference is recorded, an authenticated callback can recover the reference. Without
that reference or callback, do not resend: investigate through the provider. The
app intentionally offers no manual “mark paid” bypass.

For reversals, also configure `MPESA_INITIATOR` and
`MPESA_SECURITY_CREDENTIAL` with the provider-issued/encrypted initiator credential.
The reversal uses the verified original receipt and full order amount. Successful
submission only means pending. Completion requires the refund-specific callback
token and matching conversation IDs from the provider acknowledgment. Early
callbacks are saved until that acknowledgment is available. A timeout or lost
acknowledgment stays unresolved and requires investigation; no automatic reversal
resubmission is attempted.

The adapter's request fields and endpoints follow Safaricom's
[official SDK reference](https://github.com/safaricom/mpesa-php-sdk/blob/master/src/Mpesa.php).
This project uses certificate-verified HTTP requests and bounded timeouts.

## Reliability and financial state

- Creation requires an `Idempotency-Key` and current order version. One unresolved
  attempt reserves the order. A different key cannot start another collection while
  the first might still charge. The database also enforces that reservation.
- The attempt and request parameters are persisted **before** contacting a provider.
  A crash cannot silently discard the local record of a possibly accepted request.
- Stripe retries use the same persisted request and provider key, within a
  conservative 23-hour window. Never substitute a fresh key for an uncertain charge.
- A confirmed expiry or definitive failure permits a fresh attempt. A late second
  success flags the order `review_required`; it does not silently double-credit it.
- Webhook inbox entries deduplicate delivery. Mutations use the same institution/order
  locking order as registrar work. Reconciliation always reads current provider
  state, so an old failure cannot overwrite a confirmed successful payment.
- Ledger charges are positive, refunds negative, in integer minor units. Repeated
  callbacks or the same cumulative refund total cannot book the same movement twice.
- Receipts are payment acknowledgments, not tax invoices or proof of transcript
  issuance. Full refund completes the cancellation and marks the document items
  cancelled when there is no other unresolved collection.

The application never accepts a client-supplied `paid` status. Institutions can
inspect transaction history and reconcile against providers, but cannot override
provider verification. Milestone 6 adds institution-prepared PDF issuance and recipient delivery; see [the Milestone 6 guide](milestone-6.md).

## Refund workflow

A student can request a full refund of an unrefunded successful payment. This
blocks fulfillment while the manager decides. A manager can also initiate an
eligible full refund directly, with a reason. Another manager must handle the
manager's own order. Ordinary staff cannot approve or reject refunds.

A rejection restores the applicable payment state and retains history. Approval
persists a refund reservation before contacting the provider. Pending/unknown
refunds keep fulfillment blocked. Stripe refunds can be reconciled and safely
retried with the same request within the retention window. M-Pesa reversals wait
for their authenticated, correlated callback. Failed refunds require institutional
provider investigation; this milestone does not create successive replacement
refund requests automatically.

Partial refunds initiated elsewhere are detected for Stripe and recorded, but the
app only initiates full refunds. Merchant payouts, fees, bank settlement matching,
tax invoices, automated dispute resolution and external M-Pesa reversal detection
are not implemented. These need additional accounting/provider workflows before a
broader production rollout.

## Reconciliation worker

Run periodically (for example, through your scheduler):

```bash
uv run python -m app.payment_worker --limit 100
```

The worker checks existing referenced payments not verified in the last five
minutes, oldest checks first. It never initiates charges or resends STK requests.
Provider failures are reported with a nonzero exit code without printing secrets.
Monitor these failures and the institution's reconciliation queue. There is no
built-in scheduler: installing one is part of deployment.

Unknown attempts without a provider reference and unresolved M-Pesa reversals need
provider investigation. Keep their collection/refund reservation in place until
an authoritative outcome is available; do not edit database statuses to clear them.

## API

All routes start with `/api/v1`. Student routes require verified ownership; staff
routes require active membership at the order's institution. Webhooks authenticate
provider messages separately and accept bodies up to 64 KiB.

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/orders/{id}/payments` | Options/status / start approved-order payment |
| POST | `/orders/{id}/payments/{payment_id}/retry` | Recover uncertain Stripe checkout safely |
| POST | `/orders/{id}/payments/{payment_id}/reconcile` | Verify current provider status |
| GET | `/orders/{id}/payments/{payment_id}/receipt` | Confirmed payment acknowledgment |
| POST | `/orders/{id}/payments/{payment_id}/refund-requests` | Student full-refund request |
| GET | `/staff/institutions/{institution_id}/orders/{id}/payments` | Payment history and ledger |
| POST | Same staff prefix + `/{payment_id}/reconcile` | Institutional provider verification |
| POST | Same staff prefix + `/{payment_id}/refunds` | Manager approval/direct full refund |
| POST | Same staff prefix + `/{payment_id}/refund-rejections` | Manager rejection with reason |
| GET | `/staff/institutions/{institution_id}/payment-reconciliation` | Paginated unresolved payment queue |
| POST | `/payments/webhooks/stripe` | Signature-authenticated Stripe events |
| POST | `/payments/webhooks/mpesa/payments/{payment_id}` | Token-authenticated STK callback |
| POST | `/payments/webhooks/mpesa/reversals/{refund_id}` | Token-authenticated reversal result |
| POST | `/payments/webhooks/mpesa/reversal-timeouts/{refund_id}` | Token-authenticated timeout notice |

Payment creation accepts `provider`, `expected_version`, and `phone` for M-Pesa.
Refund actions accept `expected_version` and `reason`. Queue pagination defaults
to 30 and allows at most 100. The `/docs` interface contains input schemas.

## Tests

```bash
uv run pytest -q tests/test_payments.py tests/test_foundation.py
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser
```

Set `TEST_DATABASE_URL` to a disposable `transcriptske_test*` PostgreSQL database for
concurrency checks. Tests cover creation races, signed/replayed callbacks, amount
validation, tenant isolation, early M-Pesa reversal results, receipts, ledger
balances, worker reconciliation, and desktop/mobile refund flows. Tests do not use
real cards, send real STK prompts, or certify your provider account setup.
