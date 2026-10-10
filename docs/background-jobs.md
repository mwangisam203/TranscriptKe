# Redis and automatic background jobs

Celery workers consume job signals from Redis. Celery Beat schedules payment
reconciliation and payment receipt emails every 10 seconds, and pending document notifications every minute.
The work itself, receipts, payment ledger and worker run history remain in
PostgreSQL. No scheduled task creates a payment prompt, charges a card or
approves a refund. Verification/reset emails still use the existing mail transport;
this queue handles document availability notifications and payment receipts.

## Local setup

From `backend/`, install dependencies and apply any outstanding migrations:

```bash
uv sync
uv run alembic upgrade head
```

Redis must be running locally. On Ubuntu with Redis installed:

```bash
sudo systemctl enable --now redis-server
redis-cli ping
```

Add these to `backend/.env`:

```dotenv
BACKGROUND_JOBS_ENABLED=true
REDIS_URL=redis://127.0.0.1:6379/5
JOB_BATCH_SIZE=25
PAYMENT_JOB_INTERVAL_SECONDS=10
DELIVERY_JOB_INTERVAL_SECONDS=60
```

Install and start the user services:

```bash
uv run python -m app.jobs install
uv run python -m app.jobs status
```

The installer uses this checkout's `.venv`, writes only the two TranscriptsKE
units in `~/.config/systemd/user/`, and refuses to overwrite unrelated units.
It checks Redis connectivity and the database migration head before starting.
Failed processes restart automatically; database/broker startup failures retry
every five seconds. Stopping Uvicorn does **not** stop these separate workers.

Services normally start when you sign in and survive closing your terminal.
To start them at boot and keep them running after a full logout, enable lingering
for your own user (your operating system may require administrator authorization):

```bash
loginctl enable-linger "$USER"
```

The API, public callback URL/ngrok, Redis, PostgreSQL and worker services are
separate processes. This installer manages only the Celery worker and scheduler.
It does not start the website or ngrok. Restart the API after changing `.env` so
its deployment-readiness report includes Redis health.

## Everyday controls

Run these from `backend/`:

```bash
uv run python -m app.jobs check
uv run python -m app.jobs status
uv run python -m app.jobs logs
uv run python -m app.jobs restart
uv run python -m app.jobs stop
uv run python -m app.jobs start
```

`stop` stops scheduling first, then lets the worker finish in-flight tasks.
To also prevent automatic startup, disable the user units:

```bash
systemctl --user disable --now transcriptske-scheduler transcriptske-worker
```

Changing code or provider configuration requires `app.jobs restart`; workers
do not use the development server's automatic reload.

## How failure recovery works

- Redis locks prevent concurrent runs of the same job. Payment and delivery jobs
  can run alongside each other. The worker uses the existing database locks and
  idempotent ledger updates.
- Tasks accept JSON only. Messages contain task names, not ID documents, email
  codes, receipt data or provider credentials. Task results are not stored in Redis.
- Acknowledgment happens after execution. A lost worker can cause redelivery;
  job locks and database checks protect state when work is repeated.
- Old scheduled signals expire instead of accumulating a backlog during an
  outage. Fresh scheduled runs rediscover outstanding work in PostgreSQL.
- Execution has a 240-second soft limit and a 270-second hard limit. The Redis
  lease lasts 300 seconds and redelivery visibility is 360 seconds. Keep the
  default prefork worker pool; alternate pools may not enforce these limits.
- Worker health stays in the manager operations dashboard. A successful empty
  run confirms execution, not provider connectivity or delivery to an inbox.
- File mail still creates local email files. Configure SMTP for actual inbox
  delivery; Redis does not provide an email service.

## Deployment

Run exactly one scheduler for the application. Additional workers can consume
the same queue. Do not run the old payment `--loop` process or cron schedules
alongside Celery. Old one-shot worker commands remain available for diagnosis.

Use a private authenticated Redis instance, `rediss://` for remote TLS, and a
dedicated database. Use `noeviction` and configure persistence/backups according
to your hosting environment. The installer does not alter a shared Redis server's
configuration. The database remains recoverable even if queued schedule signals
are lost, because future runs query outstanding database rows again.

Jobs, Redis and PostgreSQL must share the same environment/provider account
configuration as the API. Do not use production credentials in test workers.

## Tests

The opt-in integration test starts its own Redis server on a temporary local port
and actual Celery worker/scheduler processes against an isolated test database.
It checks queue execution, automatic scheduling and lock-based overlap recovery.
It does not contact payment providers, send email or modify your running Redis.

```bash
uv run pytest -q tests/test_background_jobs.py
RUN_REDIS_TESTS=1 uv run pytest -q tests/test_background_jobs.py
```

Implementation follows Celery's official [Redis broker documentation](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html),
[periodic task guidance](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html)
and [service management guidance](https://docs.celeryq.dev/en/stable/userguide/daemonizing.html).
