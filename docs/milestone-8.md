# Milestone 8 — Pilot evaluation and controlled institution onboarding

This phase provides evidence and review tools for deciding whether to expand after
a pilot. It does not claim a real pilot has passed. Demo institutions and fake
provider tests remain demonstrations. No real acceptance results are prefilled.

## Onboard an institution

1. A verified platform administrator opens **Institution approvals → Add an
   institution**. Name and code must be unique. Creation always produces an active,
   **unapproved** institution with draft onboarding; it is hidden from the public
   directory and cannot accept student submissions.
2. The administrator appoints its manager through the existing invitation workflow.
   The manager configures document services and the ordering policy in the institution
   workspace. Preparing a policy does not bypass institution approval.
3. Under **Onboarding and pilot evaluation**, the manager saves private evidence for
   authority, privacy/consent, staff training, support ownership, payment acceptance,
   secure delivery acceptance, and recovery/monitoring. Record the owner, date,
   outcome and an internal evidence reference; exclude secrets and student records.
   Draft entries may be incomplete; submission needs at least 20 characters per
   requirement, an active verified manager, an active service, and policy instructions.
4. Submission freezes editing. A **different** platform administrator reviews it,
   either requesting changes or accepting the evidence. Returned evidence can be
   corrected and resubmitted. Versions and institution locks prevent stale or
   simultaneous decisions. Accepted records remain immutable.
5. After acceptance, the administrator separately enables the existing institution
   approval control. Direct approval requests cannot bypass onboarding for institutions
   with an onboarding record. Manager/service/policy setup is rechecked at this
   final step. Approval withdrawal is always available.

Evidence notes are human attestations, not machine verification of contracts,
provider behavior or legal compliance. Test-environment payment/delivery acceptance
can support preparation for a pilot; it is not proof of successful live operation.
Acceptance and public approval do not enable payment providers or document issuance.
The ordering policy must also accept requests before students can submit.

Existing institutions retain their approval flags during migration and display
**existing approval; pilot acceptance not established** until evidence is supplied.
They may keep using their existing workflow. Their managers can start onboarding;
expansion decisions require completed onboarding even for these existing institutions.
The development seed is unchanged. Migration invents no approvals or evidence.

## Evaluate the pilot

Managers choose a completed submission window (maximum 366 days), **demo/test** or
**live**, and preview aggregate evidence. The interval is `[start, end)`: start is
included, end excluded. API dates require explicit timezones; browser inputs use the
operator's local timezone. Outcomes are observed when the report is captured, not
reconstructed at the end of the submission window.

The report counts distinct orders:

- Submitted cohort orders, including cancelled submissions and orders without a
  payment or document. This denominator includes all modes because orders have no mode.
- Currently paid orders with a successful, unrefunded payment in the selected mode.
- Orders with **every item** delivered and an unrevoked issued PDF in the selected
  mode with a recorded recipient download. Partially delivered or rejected-item
  orders do not count as fully downloaded.
- Orders satisfying both paid and fully downloaded conditions.

Demo uses test payments and demo documents; live uses live payments and live
documents. Draft orders never enter the cohort. Current operations counts cover
**all submitted orders at that institution**, including those outside the chosen
window. An institution cannot hide an unresolved exception by selecting another window.

The manager supplies fresh acceptance evidence and findings, then submits an
immutable snapshot for review. Managers and platform administrators can read the
institution's paginated evaluations. Ordinary staff and students cannot. Reports
contain aggregate measurements and submitted notes, not student identifiers,
recipient addresses, academic records, provider payloads or access codes. The
administrator's new access is limited to onboarding/evaluation records; it does not
grant academic-record access through staff endpoints.

A different administrator records one decision with a reason:

- **Continue pilot**: collect more evidence or more representative traffic.
- **Rework**: correct the workflow and submit a fresh evaluation afterward.
- **Approve expansion planning**: allowed only when both the original snapshot and
  the freshly recalculated review report meet the expansion gates.

Expansion gates require live mode, at least one paid and fully downloaded order,
accepted onboarding, current institution approval, manager/service/policy setup,
and no current payment, delivery or cancellation exceptions. Revocations, refunds,
new delivery failures or withdrawn approval can block a pending decision. A blocked
original report needs a new evaluation after corrections; its historical evidence
is never rewritten. The decision stores a second report observed at review time.

These are minimum decision gates, not a statistically sufficient pilot or automatic
launch approval. Review overdue work, holds, support feedback, representative volume
and incidents explicitly in the findings. A report is an observation; underlying
orders may change after review. Decisions are historical records, not ongoing health
certificates. Submit a new evaluation for reassessment. No automatic rollout,
merchant migration, service-level guarantee or disabling of existing orders occurs.

## Deployment and limits

From `backend/`, after backing up an existing database:

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Migration `0009` adds `institution_onboarding` and `pilot_evaluations`, without
altering existing accounts, payments, approvals or issued documents. Downgrade
removes these records; generic audit events remain. Test backup restoration before
production changes. No new environment variables or dependencies are required.

Payment collection remains restricted by `PAYMENT_INSTITUTION_ID` and the existing
provider account/mode binding. **Do not switch that setting to onboard another
institution while outstanding payments depend on the current account.** Multiple
merchant accounts, Stripe Connect, institution-specific M-Pesa credentials and
settlements need a separately designed rollout. An expansion decision does not
change these settings or bypass recipient consent, scanning or registrar release.

Still deferred: real institution/provider acceptance, MFA, SIS integrations,
third-party ordering, public credential verification, formal SLA calendars,
statistical acceptance thresholds, automatic rollout and a new frontend framework.
The existing readiness CLI and operational workers remain part of deployment.

## API

All paths start with `/api/v1`. `S` below means
`/staff/institutions/{institution_id}` (verified active manager membership);
`A` means `/admin/institutions/{institution_id}` (verified platform administrator).
Responses are private and use `Cache-Control: no-store`.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/admin/institutions` | Create an unapproved institution and draft onboarding |
| GET | `S/onboarding`, `A/onboarding` | Evidence, version, requirements, blockers and latest 50 audit entries |
| PUT | `S/onboarding` | Save draft evidence with `expected_version` (0 when no record exists) |
| POST | `S/onboarding/submit` | Freeze evidence for independent review; current version required |
| POST | `A/onboarding/review` | `approved` or `changes_requested`, reason and current version |
| PUT | `A/approval` | Existing public approval control, now enforcing recorded onboarding acceptance |
| POST | `S/pilot/preview`, `A/pilot/preview` | Read-only aggregate report for `mode`, `window_start`, `window_end` |
| POST | `S/pilot/evaluations` | Capture report with evidence and findings |
| GET | `S/pilot/evaluations`, `A/pilot/evaluations` | Newest first; `offset`, `limit` (default 20, maximum 100) |
| POST | `A/pilot/evaluations/{id}/review` | One decision, reason, `expected_version`; captures current evidence |

Evidence keys: `authority`, `privacy`, `staff_training`, `support_ownership`,
`payment_acceptance`, `delivery_acceptance`, `recovery_and_monitoring`. Each accepts
at most 2,000 characters. Findings and review reasons require 20–4,000 characters.
Unknown request fields are rejected. No endpoint sends mail or charges a card;
existing invitation and order workflows retain those responsibilities.

## Validation

```bash
uv run pytest -q tests/test_pilot.py tests/test_foundation.py tests/test_catalog.py
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser/test_pilot.py
```

Use a disposable PostgreSQL `TEST_DATABASE_URL` for row-lock concurrency coverage.
Tests cover approval bypass prevention, returned evidence, independent decisions,
manager revocation, tenant isolation, window boundaries, demo/live separation,
partial delivery and changed evidence, immutable reports, pagination, migration
round trips, and desktop/mobile manager-to-administrator workflows. Live-mode rows
in automated tests are synthetic; no live provider calls are made.
