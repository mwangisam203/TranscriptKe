# TranscriptsKE

TranscriptsKE is a Kenyan academic transcript request and verification platform.
The current release implements **Milestones 1 and 2: accounts, institution services,
and academic record matching**. Transcript ordering, registrar fulfillment, document
delivery, and payments are planned; they are not implemented yet. Payments will include M-Pesa
and Stripe or another suitable card gateway in Milestone 5.

## Implemented

- FastAPI API, PostgreSQL/SQLAlchemy models, and versioned Alembic migrations.
- Student registration with normalized, validated email and names; bcrypt password hashing.
- Email verification, password recovery, password changes, and JWT authentication.
- Expiring, single-use action codes stored as hashes; password changes/reset invalidate old sessions.
- Logout invalidates access tokens on all devices. Access tokens expire after 60 minutes by default.
- Database-backed rate limits shared across application workers.
- Public directory of active, approved institutions.
- Configurable institution services, KES fees, delivery options, processing times and matching requirements.
- Student academic record links, manual staff decisions, private evidence and versioned review history.
- A browser workspace for students, staff, managers and institution approvals.
- Institution memberships, authorized invitations, manager/staff permissions, and revocation.
- Audit records for email verification, password changes, administrator bootstrap, and staff access changes.
- Repeatable fictional development institutions and an explicit local administrator bootstrap command.
- Local file email delivery for development and SMTP with STARTTLS for deployment.

Open the basic browser workspace at `/workspace`, or use the interactive API documentation
at `/docs`. The workspace is served by FastAPI and needs no separate frontend build.
See the [Milestone 2 guide](docs/milestone-2.md) for the demo walkthrough and new endpoints.

## Requirements and setup

Use Python 3.12+, uv, and PostgreSQL 16+.
Run commands below from `backend/` unless otherwise indicated.
If `.env` already exists, update it with the new example settings instead of copying
over it; retain your existing database credentials and secret.

```bash
cd backend
uv sync --locked --group dev
cp .env.example .env
uv run python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Copy the generated secret into `SECRET_KEY` in `.env`. Set `DATABASE_URL` to a
PostgreSQL database owned by a dedicated application role. Create the role/database
using your local PostgreSQL administrator; database credentials are not created by
this application. For example, in an administrator `psql` session:

```sql
CREATE ROLE transcriptske LOGIN;
\password transcriptske
CREATE DATABASE transcriptske OWNER transcriptske;
```

Use the password you entered in `DATABASE_URL`; URL-encode special characters.
For a **new, empty database**:

```bash
uv run alembic upgrade head
uv run python -m app.cli seed-academic-demo
uv run uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`. `/health` is a process-health check; it does
not assert database or email-provider readiness. Migrations and seeds are explicit
commands, not startup side effects.

The seed creates `DEMO-UNI` and `DEMO-TVET`, clearly labeled fictional institutions.
It can be rerun without duplicates and does not overwrite existing institution data.
It refuses to run outside `APP_ENV=development` and creates no user accounts.

## Upgrading an existing development database

Revision `0001` describes the original `users` and `institutions` schema. Revision
`0002` adds authentication/access tables and `users.token_version`.

If the database has no existing tables, use `alembic upgrade head` as above.
If tables were previously created manually or through `Base.metadata.create_all`,
back up the database and compare its tables, types, constraints, and indexes with
`alembic/versions/0001_initial_schema.py`. **Only when that baseline matches**, mark
that existing schema as revision `0001`, then upgrade:

```bash
uv run alembic stamp 0001
uv run alembic upgrade head
```

Do not stamp `head` or stamp a schema that does not match. If there is schema drift,
reconcile it before upgrading. `stamp` records a revision; it does not create or repair tables.

The upgrade preserves users and passwords but resets existing `is_email_verified`
flags to false: the old registration code set them without mailbox verification.
Existing users must request a verification email and confirm its code before signing
in. Existing staff roles do not create memberships or grant institution access;
authorized invitations are required. Previously issued JWTs are no longer accepted.
Review legacy invalid email addresses with their owners before migration if any exist.
Downgrading does not restore the old verification flags.

For an existing Milestone 1 database already at `0002`, run `uv run alembic upgrade head`
to apply `0003`. It preserves verified accounts, sessions and memberships. Existing
institutions start **unapproved** and must be approved by a platform administrator
before becoming publicly available or accepting matching submissions.

## Try registration and verification

In `/docs`, submit `POST /api/v1/auth/register`:

```json
{
  "email": "student@example.com",
  "full_name": "Jane Doe",
  "password": "a-long-unique-password"
}
```

Registration creates an **unverified student** and sends a verification code.
Unknown fields such as `role`, `institution_id`, or `is_email_verified` are rejected.
Passwords must be at least 8 characters and no more than 72 UTF-8 bytes because of
bcrypt's input limit. Whitespace-only names and invalid email addresses are rejected.

With `MAIL_BACKEND=file`, emails are saved under `backend/.mailbox/` as private
`.eml` files. Open the newest file for the expected recipient and copy the code from
its body. These files contain secrets, are ignored by Git, and must never be served
by a web server. They are local development artifacts, not an application inbox API.

Submit `POST /api/v1/auth/email-verifications/confirm`:

```json
{"token": "paste-the-code-from-the-email"}
```

Then call `POST /api/v1/auth/login` with JSON `email` and `password`. Paste the returned
`access_token` into the **Authorize** dialog in `/docs`. Requests use
`Authorization: Bearer <access_token>`; login is JSON, not an OAuth2 form endpoint.

Verification codes expire after 60 minutes by default. A resend creates a new code;
confirming one invalidates the remaining verification codes for that account.
**Email verification proves mailbox control, not ownership of academic records.**
Academic record matching belongs to Milestone 2.

## Authentication API

All paths below begin with `/api/v1`.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/auth/register` | Create an unverified student and deliver a verification code. |
| POST | `/auth/login` | Return an access token after password and email-verification checks. |
| GET | `/auth/me` | Read the authenticated profile. |
| POST | `/auth/email-verifications` | Request a verification code using `email`. |
| POST | `/auth/email-verifications/confirm` | Consume a verification `token`. |
| POST | `/auth/password-reset-requests` | Request a password-reset code using `email`. |
| POST | `/auth/password-resets` | Submit `token` and new `password`. |
| PUT | `/auth/password` | Submit `current_password` and new `password`; verified authentication required. |
| POST | `/auth/logout` | Invalidate existing access tokens on **all devices**. |
| GET | `/institutions` | List active institutions. |
| GET | `/institutions/{institution_id}` | Read an active institution. |

Password reset codes expire after 30 minutes. They cannot be used for verification
or invitations. A successful reset/change invalidates every pending reset code and
all previously issued access tokens. Resetting a password does not mark an email
verified. Recovery and verification requests return the same accepted response for
eligible and unknown accounts. Registration reports duplicate addresses with `409`.

There are no refresh tokens or individual device-session controls in this milestone;
users sign in again after token expiry or global logout.

Rate limits use fixed 15-minute windows and hashed email/IP keys in PostgreSQL:

- Login/password changes: 10 attempts per email, 60 per source IP per operation.
- Registration, verification email requests, reset email requests, and invitations:
  5 per email and 30 per IP per operation.
- Confirmation endpoints: 60 per IP; invitation acceptance also allows 10 per account.

Limits return `429` with `Retry-After`. Expired counter entries are cleaned up as
requests arrive. A reverse proxy must pass client addresses only through explicitly
trusted proxy configuration; do not trust arbitrary forwarded headers. Fixed-window
limits are a baseline and do not replace deployment-level abuse protection.

## Administrator and institutional access

Register and verify the operator's account through the normal flow. Then, on the
server with authorized database access, run:

```bash
uv run python -m app.cli bootstrap-admin --email operator@example.com
```

This explicit command grants platform administrator access and invalidates the
account's previous access tokens. Sign in again afterward. It does not create an
account or bypass email verification. Do not expose this command as a public API.

Permission model:

| Actor | Membership administration |
| --- | --- |
| Student / legacy `institution_staff` role without membership | No institution access. |
| Active institution staff member | View their institution's active membership roster. |
| Active institution manager | View roster and pending invitations; invite/revoke ordinary staff at their own institution. |
| Platform administrator | Invite managers/staff and revoke memberships across active institutions. |

A user's global `role` is not proof of institutional membership. Every institution
route checks active database membership and the institution's active status. The
administrator override is restricted to these membership-management tools; it is not
a blanket policy for future access to academic documents. Revoking the last membership
may leave the descriptive global `institution_staff` role, which grants no access on its own.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/staff/memberships` | Current user's active memberships at active institutions. |
| GET | `/staff/institutions/{institution_id}/members` | Institution's active roster; 100 entries per page, `offset` supported. |
| POST | `/staff/institutions/{institution_id}/invitations` | Invite with `email` and `role` (`staff` or `manager`). |
| GET | `/staff/institutions/{institution_id}/invitations` | Pending unexpired invitations; 100 entries per page, `offset` supported. |
| POST | `/staff/invitations/accept` | Accept using `token`; authenticated, verified matching email required. |
| DELETE | `/staff/institutions/{institution_id}/invitations/{invitation_id}` | Revoke a pending invitation. |
| DELETE | `/staff/institutions/{institution_id}/members/{membership_id}` | Revoke membership immediately. |

An administrator invites the first manager. That manager can invite ordinary staff.
The invitee registers/verifies their own account, signs in, and accepts the emailed
code. Invitations expire after 24 hours and are single-use. Forwarding a code does
not allow another account to accept it. The issuer's current authority is checked
again at acceptance. Managers cannot appoint or revoke managers.

A membership revocation also cancels outstanding invitations to/from that member at
that institution. An old invitation cannot restore revoked access. Reinstatement
requires a new authorized invitation. Deactivation and revocation take effect on the
next authorization check even with an otherwise unexpired JWT.

## Email deployment

Development defaults to local files and sends no messages over the network.
To enable real delivery, configure `MAIL_BACKEND=smtp`, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, and an authorized `MAIL_FROM` address.
`SMTP_STARTTLS=true` upgrades the SMTP connection using certificate validation.
Implicit TLS on port 465 is not implemented; use the provider's STARTTLS endpoint.

`APP_ENV=production` requires SMTP with STARTTLS and a secret at least 32 characters
long. Email submission is synchronous with a 10-second connection timeout. Provider
errors return `503`, and the pending database transaction rolls back. If a mail provider
accepts a message but a database commit subsequently fails, request a new code. A durable
mail queue and provider bounce/retry handling belong to a later operational milestone.

No real SMTP delivery is exercised by the automated suite. Production also needs
HTTPS and deployment configuration; completion of this milestone is not a public-launch gate.

## Tests and checks

```bash
uv run pytest -q
uv run ruff check app alembic tests
uv run ruff format --check app alembic tests
```

Default tests migrate fresh temporary SQLite databases and replace email delivery
with a fake. They do not use `.env` database credentials, send real email, or create
production users. PostgreSQL is the application database; use a dedicated disposable
PostgreSQL database for full integration and concurrent-token tests:

```bash
TEST_DATABASE_URL='postgresql://test_user:test_password@localhost:5432/transcriptske_test' uv run pytest -q
```

The test database name must start with `transcriptske_test`. The database role must
be allowed to create schemas. Each test creates a uniquely named schema and drops
that schema afterward. Concurrency tests run only against PostgreSQL. Real-browser
checks are opt-in; installation and commands are in the [Milestone 2 guide](docs/milestone-2.md#validation).

Coverage includes fresh migration/rollback and schema consistency, legacy-account
upgrades, registration validation, code expiry/replay/purpose checks, login failures,
session invalidation, rate limits, invitation authorization, tenant isolation,
revocation, seed idempotency, administrator bootstrap, and simultaneous code redemption.
Milestone 2 adds service/policy validation, approval gates, ownership checks,
private evidence isolation, stale-decision protection, and simultaneous review decisions.

For model changes, generate and review a migration before applying it:

```bash
uv run alembic revision --autogenerate -m "describe the schema change"
uv run alembic upgrade head
uv run alembic check
```

References: [Alembic migration guidance](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
and [FastAPI testing documentation](https://fastapi.tiangolo.com/tutorial/testing/).

## What follows

Milestone 3 adds transcript ordering, consent and student tracking on top of the
confirmed record links and institution catalog. Later milestones add registrar
fulfillment, M-Pesa **and card payments**, trusted issuance/delivery, and pilot operations.
The basic workspace can evolve into the planned Next.js frontend. MFA, SIS integrations,
third-party ordering, and credential verification remain future work.
