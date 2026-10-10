"""Manage this checkout's Celery worker and scheduler as Linux user services."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import BASE_DIR

UNITS = ("transcriptske-worker.service", "transcriptske-scheduler.service")
MARKER = "# Managed by TranscriptsKE background jobs"


def quote(value):
    # systemd quoting/specifiers, not shell syntax. subprocess never invokes a shell.
    return (
        '"'
        + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        + '"'
    )


def service_units(backend=BASE_DIR):
    python = backend / ".venv/bin/python"
    common = (
        f"WorkingDirectory={str(backend).replace('%', '%%')}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Environment=BACKGROUND_JOBS_ENABLED=true\n"
        f"ExecStartPre={quote(python)} -m app.jobs check\n"
        "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=300\n"
        "KillMode=mixed\nUMask=0077\nNoNewPrivileges=true\n"
    )
    worker = (
        f"{MARKER}\n[Unit]\nDescription=TranscriptsKE background job worker\n"
        "StartLimitIntervalSec=0\n\n[Service]\nType=simple\n"
        + common
        + f"ExecStart={quote(python)} -m celery -A app.celery_app:celery_app worker "
        "--loglevel=WARNING --concurrency=2 --queues=transcriptske.jobs "
        "--hostname=transcriptske@%H --without-gossip --without-mingle\n"
        "\n[Install]\nWantedBy=default.target\n"
    )
    scheduler = (
        f"{MARKER}\n[Unit]\nDescription=TranscriptsKE background job scheduler\n"
        "Wants=transcriptske-worker.service\nAfter=transcriptske-worker.service\n"
        "StartLimitIntervalSec=0\n\n[Service]\nType=simple\n"
        + common
        + f"ExecStart={quote(python)} -m celery -A app.celery_app:celery_app beat "
        f"--loglevel=WARNING --schedule={quote(backend / '.jobs/schedule')} "
        f"--pidfile={quote(backend / '.jobs/beat.pid')}\n"
        "\n[Install]\nWantedBy=default.target\n"
    )
    return dict(zip(UNITS, (worker, scheduler), strict=True))


def check():
    from app.db.session import SessionLocal
    from app.services.readiness import database_ready
    from app.tasks import broker_ready

    if not broker_ready():
        print("Redis is unavailable. Check REDIS_URL and the Redis service.")
        return 1
    try:
        with SessionLocal() as db:
            ready = database_ready(db)
    except Exception:
        ready = False
    if not ready:
        print(
            "Database unavailable or migrations are behind. Run alembic upgrade head."
        )
        return 1
    print("Redis and database are ready for background jobs.")
    return 0


def install():
    if not shutil.which("systemctl"):
        raise RuntimeError("User services require Linux with systemd.")
    if not (BASE_DIR / ".venv/bin/python").exists():
        raise RuntimeError("Run uv sync in backend before installing services.")
    if check():
        return 1
    unit_dir = Path.home() / ".config/systemd/user"
    units = service_units()
    if shutil.which("systemd-analyze"):
        with tempfile.TemporaryDirectory(prefix="transcriptske-units-") as temporary:
            candidates = []
            for name, content in units.items():
                candidate = Path(temporary) / name
                candidate.write_text(content)
                candidates.append(str(candidate))
            subprocess.run(
                ["systemd-analyze", "--user", "verify", *candidates], check=True
            )
    for name in units:
        path = unit_dir / name
        if path.exists() and not path.read_text().startswith(MARKER):
            raise RuntimeError(f"Refusing to replace an unrelated service: {name}")
    unit_dir.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / ".jobs").mkdir(mode=0o700, exist_ok=True)
    for name, content in units.items():
        path = unit_dir / name
        path.write_text(content)
        path.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", *UNITS], check=True)
    subprocess.run(["systemctl", "--user", "is-active", "--quiet", *UNITS], check=True)
    print("Worker and scheduler installed. They continue running when terminals close.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("check", "install", "status", "start", "stop", "restart", "logs"),
    )
    args = parser.parse_args()
    if args.command == "check":
        return check()
    if args.command == "install":
        return install()
    if args.command == "logs":
        return subprocess.call(
            [
                "journalctl",
                "--user",
                "-u",
                UNITS[0],
                "-u",
                UNITS[1],
                "-n",
                "40",
                "--no-pager",
            ]
        )
    units = list(reversed(UNITS)) if args.command == "stop" else list(UNITS)
    command = ["systemctl", "--user", args.command, *units]
    if args.command == "status":
        command.append("--no-pager")
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
