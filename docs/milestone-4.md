# Milestone 4 — Registrar review, holds and fulfillment preparation

Institution staff can now claim submitted orders, review each document, request
information through the existing messaging workflow, place and resolve holds, and
record preparation readiness. Students see document progress, hold explanations and
resolution messages. Private evidence remains in institutional audit history.

## Run locally

From `backend/`:

```bash
uv sync --locked --group dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Migration `0005` adds registrar cases, holds and audit events. Existing orders,
quotes, consent, accounts and record matches are preserved. Cases are created on
first assignment; older submitted orders are immediately available in the queue.
Migrations are explicit and are not automatically applied at application startup.

## Workflow

1. Use the Milestone 3 workflow to submit an order at a demo institution.
2. Sign in as an active institutional staff member and open the institution tab.
3. Filter the order queue by document status, assigned-to-me or active holds.
   API clients can also filter unassigned orders or a specific assignee. The queue
   is oldest-submission first and paginated, with one result per order.
4. Open the order. Review the submitted snapshot, attachments, consent and release
   instructions. Choose **Assign to me** to claim an unassigned order. Managers can
   reassign or unassign using the institution's registrar selector.
5. Select a document and approve it for processing or reject it during review.
   Record both a student-facing explanation and private institutional evidence.
6. Add an academic, identity, financial or administrative hold if needed. All holds
   apply to the order. Resolve each separately with an explanation and evidence.
7. Use the existing question/message form when information is missing. The student
   must reply to the specific outstanding question to clear that blocker.
8. For after-grades/after-graduation orders, confirm the requested event with private
   evidence before marking preparation ready. Withdrawing confirmation returns
   ready items to processing and records the change.
9. Mark each approved item ready once institutional preparation is complete. Review
   release blockers. Readiness is a registrar attestation, not an uploaded official
   document, payment confirmation or delivery receipt.

## States and authorization

| Action | Required item state | Result |
| --- | --- | --- |
| Approve | `awaiting_review` | `processing` |
| Reject | `awaiting_review` | `rejected` |
| Mark ready | `processing` | `ready` |
| Manager reopen | `processing`, `ready`, or `rejected` | `awaiting_review` |

These are per-document states. The overall request remains `submitted` while being
handled, with the existing `cancellation_requested` and `cancelled` states retained.
Holds are separate records: multiple holds can coexist without losing item state.
A ready item with a new hold remains visibly ready but cannot pass release checks.

Every mutation locks the institution and order, checks `expected_version`, and
increments the order version together with public and private audit entries. A
stale or repeated mutation returns `409`; reload before deciding whether to retry.

Only an active, verified member of the order's institution can access registrar
records. Platform admin status alone does not grant access. The assigned registrar
performs item decisions, holds and release confirmations. Managers may reassign an
order; reopening also requires manager permission and assignment to that manager.
Revoked members lose access immediately; a manager can reassign their outstanding
orders. Staff cannot assign an order to its owner or act on their own order.
Drafts, including cancelled drafts, never enter the institutional queue.

## Checks before progressing

Approval and readiness recheck institution approval, the academic match and exact
submitted consent, active holds, unanswered questions and cancellation state.
Readiness additionally requires any deferred release event to be confirmed.

A changed academic match invalidates further progression of the old authorization;
a new authorized order is required. Later catalog prices do not rewrite an already
submitted quote. Quote expiry applies to submission, not to registrar processing of
an order that was validly submitted earlier.

Pending cancellation blocks registrar mutations. The earlier cancellation workflow
still requires staff approval. Orders with items in processing or ready cannot use
the simple cancellation path; a manager can deliberately reopen items for review,
with history retained. Rejected items do not prevent cancellation before payment.

Release assessment also requires all items ready and verified payment for a nonzero
order total. It **always returns `can_release=false` in this milestone**, because
secure issuance and delivery are not implemented. There is no endpoint to mark an
order paid, upload an official issued transcript or bypass release checks.
Financial holds do not create charges or replace payment confirmation.

## API

All paths start with `/api/v1`; verified authentication is required.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/orders/{order_id}/fulfillment` | Owner's document states and public hold history |
| GET | `/staff/institutions/{institution_id}/registrar-queue` | Paginated submitted-order queue |
| GET | `/staff/institutions/{institution_id}/registrars` | Paginated eligible assignees, names and roles |

Queue parameters: `state`, `assigned_to`, `unassigned`, `on_hold`, `offset`, `limit`.
`state` filters by any matching item. `unassigned` and `assigned_to` are mutually
exclusive. Limit defaults to 30, maximum 100. The registrar roster defaults to 100.

The following routes use the prefix
`/staff/institutions/{institution_id}/orders/{order_id}/fulfillment`:

| Method | Suffix | Body beyond `expected_version` |
| --- | --- | --- |
| GET | (none) | Read states, holds, assignee, consent, private history and release blockers |
| PUT | `/assignment` | `user_id` (null to unassign) |
| POST | `/items/{key}/decisions` | `decision`, `student_message`, `internal_note` |
| POST | `/holds` | `category`, `student_message`, `internal_note` |
| POST | `/holds/{hold_id}/resolution` | `student_message`, `internal_note` |
| POST | `/release-confirmation` | `confirmed` boolean, `internal_note` |

Full request schemas are available in `/docs`. No private notes, registrar IDs or
consent evidence are added to the student fulfillment response. The owner retains
their existing access to their own consent through the Milestone 3 consent endpoint.

## Validation and next work

```bash
uv run pytest -q tests/test_fulfillment.py tests/test_foundation.py
uv sync --locked --group browser
uv run --group browser playwright install chromium
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser
```

Use `TEST_DATABASE_URL` pointing to a disposable PostgreSQL database named
`transcriptske_test*` for competing-review tests. Test schemas and mail are isolated
from the application database and real email providers.

Milestone 5 adds M-Pesa and Stripe or another card processor, verified webhooks,
payment reconciliation and refunds. Secure issuance, recipient delivery, automated
SIS checks, workload SLAs and operational notifications remain later work. The
current workflow uses manual institutional checks and demo institutions.
