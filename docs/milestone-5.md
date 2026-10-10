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

New orders use **checkout before institution review**. Enrollment details are
saved privately; choose documents and recipients, sign consent, then pay. A payable
order enters `awaiting_payment` with no institution submission timestamp. Only a
provider-confirmed full payment releases it to the institution and starts its
processing target. Pending, failed or uncertain attempts do not release an order.
Zero-fee orders are released immediately after consent without a gateway charge.

`PAYMENT_COLLECTION_POLICY=before_review` is the default for new orders. Each order
stores its policy. Existing orders retain `after_review` and their prior behavior;
changing configuration never silently changes an existing order. Those legacy
orders still require registrar approval before payment. Institution approval,
signed scope, whole-shilling M-Pesa totals, provider configuration and duplicate
payment protection apply to checkout. Ownership matching and unchanged enrollment
scope remain mandatory before preparation and delivery. New private enrollment
records are hidden from staff until a checkout is released.

Amounts come from the immutable submitted quote in KES minor units. The browser
cannot supply an amount or alter fees. Zero-fee orders need no payment. M-Pesa is
offered only for whole-shilling totals; fractional totals must use card payment.
The app never rounds a price or silently adds gateway fees.

TranscriptsKE collects payments and handles refunds using platform merchant
credentials. `PAYMENT_ROUTING_MODE=platform` is the default; each approved school
must also have collection enabled by an administrator. Students select a school
by name, and each order belongs to that one school. `PAYMENT_INSTITUTION_ID` is
only required for the optional legacy `pilot` routing mode. Existing attempts
retain their original merchant scope and provider binding. This does not implement
Stripe Connect, split settlement or automatic payouts to schools.

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
PAYMENT_ROUTING_MODE=platform
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

Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `STRIPE_PUBLISHABLE_KEY`
from the same sandbox (or live account). The publishable key enables secure
embedded card fields inside the application's checkout. Customers see
**Credit or debit card · Visa / Mastercard**. Card numbers, expiry dates and CVC
are collected directly by the provider; they never enter application endpoints,
drafts or logs. The provider checks the card details and the issuing bank
authorizes payment. Test mode requires provider test cards.

Without a publishable key, new payments use the existing hosted checkout.
Previously created hosted attempts retain their original checkout URL.
The owner-only `GET /orders/{id}/payments/{payment_id}/card-checkout` endpoint
returns a non-cacheable client secret after checking the original amount,
currency, owner reference, mode and open session. Client secrets are not
persisted or returned in payment history.
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
redirect returns to `/workspace`; it never changes payment state. Hosted checkout opens in a separate tab; embedded checkout remains in the order.
Neither completion UI nor returning to the workspace marks an order paid;
backend provider reconciliation confirms payment.

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
a success receipt is accepted only after the token, checkout reference, amount,
phone number and receipt uniqueness are validated. The callback settles payment
and releases the order atomically; a second STK query is not required. The callback token is our integration's authentication mechanism, **not a
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
| POST | `/orders/{id}/payments/{payment_id}/retry-prompt` | Check an M-Pesa attempt and send a fresh prompt after confirmed failure; requires Idempotency-Key |
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

For the guided Daraja setup and workspace test flow, see
[the M-Pesa sandbox guide](mpesa-sandbox.md).


## Checkout recovery

Order status buttons reopen checkout; awaiting-payment orders focus the payment
step. Confirmed failed or expired attempts offer a fresh payment attempt with the
same signed quote. The most recent attempt is prominent; older attempts remain
in expandable history. Required fields use visible asterisks, including
conditional destination and M-Pesa fields.

While an unresolved payment is open, the workspace checks its status after one
second and then every five seconds for two minutes. Checks pause in background
tabs and stop on navigation or logout. A provider outage leaves a manual status
check available. Automatic checks never start or resend charges.

For M-Pesa, correlated query results 1037 (unanswered/unreachable prompt), 1025,
9999, 1032, 1 and 2001 indicate an unsuccessful attempt; 1019 indicates expiry.
Unknown result codes and network timeouts remain unresolved in live payments. This distinguishes
final provider results from transport failures and allows confirmed unsuccessful
requests to be retried. The result meanings are described in
[Safaricom's Online Checkout API reference](https://addiscommunication.gov.et/uploads/Publication/smart-city-2023-08-28-64ec81afaa0d8.pdf).


## Payment actions and unresolved requests

Payment responses include `can_check_status` and `can_resume`. The workspace
shows actions supported by those flags; it does not offer provider status queries
for an attempt that has no provider reference. A never-dispatched reservation can
resume through the existing owner-only `/orders/{id}/payments/{payment_id}/retry`
route. Dispatched card recovery retains the existing provider idempotency key and
recovery window. Confirmed failures use a fresh attempt on the same quoted order.

Status-check buttons show loading and errors inside the payment block. The
registrar and platform finance views use the same payment capabilities. Unresolved
requests without a reference are refreshed for callback recovery. Unfunded,
unacknowledged sandbox M-Pesa requests can be retired after two minutes; live
requests remain blocked for finance review.

A different verified platform administrator can investigate an unknown attempt
with no provider reference and record a no-payment decision through
`POST /admin/finance/orders/{id}/payments/{payment_id}/no-payment-review`.
This requires the current order version, a provider case reference, written
investigation evidence and explicit confirmation that no payment was received.
It refuses accepted, paid, refunded or ledger-backed attempts. The review keeps
the original attempt, records private audit evidence, marks the reviewed attempt
failed and permits a new payment; it never marks the order paid or sends it to the
school. Use this only after checking the actual outcome with the provider.


The order list labels an unpaid request as **Checkout incomplete**. A payment
attempt shows **Awaiting provider confirmation** or **Outcome not confirmed**
until its result is known. A status check that confirms failure opens retry;
a check that still reports pending explicitly explains why retry is not available.
An administrator's documented no-payment review can unblock an older unresolved
attempt; the original financial record remains intact.

The student M-Pesa retry action uses `retry-prompt`: it queries the previous
request, returns an already-confirmed success without charging again, and creates
a new attempt on confirmed failure or expiry. An identical idempotency key
returns the same new attempt. Pending accepted requests, unavailable provider queries and missing references
in live payments never trigger a second STK request. A stale sandbox attempt with
no reference can be retired with an explicit audit event before retry. OAuth failures during
initiation are definitive failures because no STK request has been sent. Hosted
card success and cancellation URLs retain the order route.


## Timely M-Pesa outcomes and exception recovery

The workspace checks payment progress every five seconds for two minutes and
retries temporary status-check connection failures. A token-authenticated callback
with the matching CheckoutRequestID and a recognized final failure code marks the
attempt unsuccessful immediately. It does not depend on a second query succeeding.
A success callback validates the token, checkout reference, amount, phone number
and unique receipt, then settles payment and releases the order atomically. Delayed
failure callbacks cannot overwrite a confirmed success.

The two-minute local expiry rule applies only when both the current environment
and attempt are sandbox/test, no provider reference or transaction receipt exists,
and there is no charge ledger, paid timestamp, refund or refunded amount. It logs
`payment_test_expired`, retains the attempt and allows a new checkout attempt. It
is not evidence of a provider failure and never applies to real funds or an
acknowledged STK request.

In production, unresolved requests appear in the platform finance exceptions
filter (`GET /admin/finance/payments?attention_only=true`). A missing-reference
request receives one `payment_recovery_required` event after the wait window. The
existing no-payment review workflow requires provider investigation before a new
charge. The customer sees one payment state and only actions supported by it.

For automatic background recovery, use the Redis/Celery worker and scheduler in
[background job setup](background-jobs.md). The standalone loop below remains an
alternative for development; do not run it alongside Celery:

```bash
cd backend
uv run python -m app.payment_worker --loop
```

The loop checks eligible pending requests every ten seconds, refreshes settled
requests less frequently, and retires stale unacknowledged sandbox attempts. It
never initiates a new charge. See [Safaricom's integration reference](https://www.safaricom.co.ke/images/Downloads/Tender_Documents/EOI_Safaricom_M-PESA_Integration_V1_002.pdf)
for the separation between STK initiation, final callbacks and status queries.


A recorded, validated M-Pesa success callback can be replayed from the private
webhook inbox if an older handler left it pending after a status-query error.
Receipt conflict checks and the unique charge ledger prevent double settlement.
The browser reads stored status before asking for provider reconciliation, and
reopens the order when confirmation advances it to submitted. Owner status checks
return an already-confirmed M-Pesa success without a redundant provider query.
Queries remain the fallback for absent callbacks and for back-office verification.
