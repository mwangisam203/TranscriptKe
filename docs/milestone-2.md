# Milestone 2: institution services and academic record matching

This milestone lets a verified student select an institution and document service,
provide academic information, and have authorized institutional staff establish the
correct record and its ownership. All supplied institutions and catalog fixtures are
fictional demos. No real university requirements or student records are assumed.

## Try the demo

From `backend/`, with a configured development database:

```bash
uv sync --locked
uv run alembic upgrade head
uv run python -m app.cli seed-academic-demo
uv run uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/workspace`.

1. Create a student account and verify it with the code in `backend/.mailbox/`.
2. Select a demo institution and its demo transcript service. The example price is
   KES 1,500 and processing estimate is 3–7 business days. No money is collected.
3. Supply the admission number, name on record, program and first attendance year.
   Submit and see the link in **My record links** with status **pending**.
4. Separately register and verify an operator account. Grant it platform administrator
   access using `uv run python -m app.cli bootstrap-admin --email operator@example.com`.
   Sign in again after granting the role.
5. In **Institution approvals**, approve the institution if necessary and invite a
   different account as its manager. The invited person registers, verifies, signs in,
   and accepts the code using **Accept a staff invitation**.
6. In **Institution workspace**, the manager can configure services and the policy,
   invite ordinary staff, and review the pending matching request.
7. To confirm ownership, enter the institution's authoritative record reference,
   explain the independent ownership checks in a private evidence note, and confirm
   the ownership checkbox. For this demo, use fictional evidence only. Alternatively,
   request more information or reject the match with a useful student-facing reason.
8. Sign back in as the student to see the decision and history. If more information
   is needed, update the details and resubmit for another staff review.

The demo seed creates no users and does not overwrite existing policies, services,
fees, activity flags or approval decisions. Newly created demo institutions are
approved for development. Demo institutions created before migration `0003` still
need explicit platform approval; rerunning the seed does not bypass that decision.

The workspace keeps its access token in memory, never browser local storage.
Reloading requires sign-in again. Private API responses use `Cache-Control: no-store`.
The page has a restrictive Content Security Policy and renders user content as text.
The API remains authoritative for permissions and validation; hiding a button is
not an access control.

## Business rules

### Approval and configuration

- New and previously existing institutions default to unapproved at migration `0003`.
  Only a verified platform administrator can approve them; a reason is audited.
- Public institution details, services and policies require an active, approved institution.
- No ordering policy means submissions are closed. A policy must explicitly allow them.
- Only enabled services belonging to the selected institution can be chosen.
- An active **manager membership** is required to write services and policies.
  Ordinary staff can read configurations and perform record matching.
- Platform administrator status alone grants no academic-record access or catalog
  editing privileges. Those require actual membership at the institution.
- Fees are integer minor units: `150050` means KES 1,500.50. This avoids floating-point
  payment calculations. KES is the only supported catalog currency in this milestone.
- Services configure document type, description, delivery methods, and minimum/maximum
  processing business days. These are service offerings, not implemented delivery features.
- Updates replace the service/policy configuration and require its current version.
  Stale saves receive `409`. Service and policy changes are audited with configuration details.
- Disable a service with `is_active=false`. It disappears from the public list, but
  existing links and their captured requirements remain available. There is no destructive
  service deletion endpoint.

### Academic matching

- Email verification establishes mailbox control only. Every academic link begins pending.
- Name on record and admission number are always required. Policy and service requirements
  are combined to determine whether program, attendance years, or previous names must be supplied.
- Required previous names may explicitly be `[]` when there are none. Omitting the field
  is distinct from stating that the student has no previous names.
- Admission numbers and institutional record references are trimmed, whitespace-normalized
  and uppercased. Punctuation is preserved. Institutions should use consistent authoritative
  record references; different aliases must not represent the same record.
- One user may have multiple links at multiple institutions or for different admission numbers.
  A user cannot create duplicate links for the same institution and normalized admission number.
- Another person's claim with the same admission number is still held for review. The API
  does not reveal an existing claimant to the student or treat the matching number as proof.
- Matching requires an institution-held record reference, explicit ownership attestation,
  and an evidence note of at least 20 characters. These requirements record a staff decision;
  they do not automatically prove that staff performed the checks. No SIS is connected yet.
- The same institutional record reference cannot be confirmed for two links at once.
  Ownership conflicts return `409` to authorized staff for resolution.
- Staff cannot review their own academic-record links.
- Students can read only their own links and public review messages. Evidence notes,
  staff identifiers and internal record references are excluded from student responses.
- Each submission, resubmission and decision adds a versioned event. Snapshots preserve
  the supplied details and the service/policy requirements in force at submission time.
- New submissions/resubmissions require current service availability, institution approval,
  and an open policy. Existing pending work may still be reviewed after a service or policy
  is closed, but confirming ownership requires the institution to remain approved.
- Membership revocation applies to the next access check. Students retain their own
  history when an institution becomes unavailable.

Supported transitions:

```text
new -> pending
pending -> needs_information | matched | rejected
needs_information | rejected -> pending              (student resubmission)
matched | rejected -> needs_information              (manager reopens with reason)
```

A matched link cannot be edited or directly rematched. Reopening clears its confirmed
record reference and requires the student to resubmit before another decision. The
original evidence stays in the history. Future ordering logic must check current
approval, membership where applicable, and current match state rather than relying
on a historical matched event.

## API reference

All paths below begin with `/api/v1`. POST bodies reject unknown fields.
Positive identifiers are validated, and record-link lists support `offset` and `limit`
(default 50, maximum 100). Review queues also support `status`.

| Method | Path | Access |
| --- | --- | --- |
| GET | `/institutions/{institution_id}/services` | Public, active/approved institution; enabled services only |
| GET | `/institutions/{institution_id}/ordering-policy` | Public, active/approved institution |
| GET | `/staff/institutions/{institution_id}` | Active institutional member |
| GET | `/staff/institutions/{institution_id}/services` | Member; includes disabled services |
| POST | `/staff/institutions/{institution_id}/services` | Institution manager |
| PUT | `/staff/institutions/{institution_id}/services/{service_id}` | Manager; full configuration plus `expected_version` |
| GET | `/staff/institutions/{institution_id}/ordering-policy` | Member |
| PUT | `/staff/institutions/{institution_id}/ordering-policy` | Manager; `expected_version=0` to create, current version to replace |
| GET | `/admin/institutions` | Platform administrator; includes unapproved institutions, paginated by `offset` |
| PUT | `/admin/institutions/{institution_id}/approval` | Administrator; `approved`, `expected_approved`, `reason` |
| POST | `/me/academic-record-links` | Verified account; institution, service and academic matching details |
| GET | `/me/academic-record-links` | Verified account; own records only |
| GET | `/me/academic-record-links/{link_id}` | Owner |
| GET | `/me/academic-record-links/{link_id}/events` | Owner; no private staff evidence |
| POST | `/me/academic-record-links/{link_id}/resubmissions` | Owner; full revised details and `expected_version` |
| GET | `/staff/institutions/{institution_id}/record-matches` | Member; institution-scoped queue |
| GET | `/staff/institutions/{institution_id}/record-matches/{link_id}` | Member |
| GET | `/staff/institutions/{institution_id}/record-matches/{link_id}/events` | Member; includes staff evidence |
| POST | `/staff/institutions/{institution_id}/record-matches/{link_id}/decisions` | Member; manager required to reopen a completed decision |

A matching decision body:

```json
{
  "expected_version": 1,
  "decision": "matched",
  "student_message": "Your academic record ownership has been confirmed.",
  "record_reference": "DEMO-ARCHIVE-001",
  "internal_note": "Demo only: independent enrollment records checked and ownership confirmed by the registrar.",
  "ownership_confirmed": true
}
```

For `needs_information` or `rejected`, give an actionable `student_message` and omit
`record_reference` and `ownership_confirmed` (or send null/false). All decision writes
check the current version and run under database locks. A concurrent or repeated
stale decision returns `409` without creating a second history entry.

## Migration

`0003` adds institution approval, catalog/policy tables, record-link/history tables,
and structured audit details. It preserves existing verified accounts, sessions,
passwords, institutions and memberships. It does **not** repeat `0002`'s email-verification reset.
Existing institutions require approval because active status alone never established
that approval. Downgrading removes Milestone 2 catalog and matching data; do not
use a downgrade on valuable data without a recovery plan.

## Validation

The default API suite runs with `uv run pytest -q`. PostgreSQL integration tests use
the dedicated test-database configuration documented in the main README.

Browser tests are optional and live in `backend/tests/browser/`:

```bash
uv sync --locked --group browser
uv run --group browser playwright install chromium
RUN_BROWSER_TESTS=1 uv run --group browser pytest -q tests/browser
```

If Chromium reports missing system libraries, install the browser's documented system
dependencies in your development environment. `PLAYWRIGHT_BROWSERS_PATH` can point to
a separate browser directory; use the same value for installation and test execution.

The browser tests use temporary databases and fake email delivery. They cover student
submission, staff confirmation, student-facing status/privacy, manager fee/policy changes,
and a mobile layout check. PostgreSQL tests cover migration upgrades/downgrades, schema
consistency, and competing staff decisions, in addition to the API authorization suite.

## Deferred

Real institution onboarding, SIS matching, identity document uploads, transcript ordering,
consent to release documents, payments, official issuance and delivery remain later work.
Do not treat a confirmed academic link as consent to disclose a transcript. Milestone 3
must capture the records, recipients, consent and authoritative order pricing separately.
