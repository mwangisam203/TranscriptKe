"""Queue safety checks plus an opt-in real Redis/Celery scheduler test."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import tasks
from app.celery_app import celery_app
from app.jobs import service_units
from app.models.operations import WorkerRun
from app.services.readiness import report


@pytest.fixture
def lock_client(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.lock.return_value.acquire.return_value = True
    monkeypatch.setattr(tasks, "job_redis", lambda: client)
    return client


def test_overlapping_job_is_skipped_without_processing(lock_client):
    lock_client.lock.return_value.acquire.return_value = False
    runner = MagicMock()
    assert tasks.run_exclusive("payments", runner) == {"skipped": "already_running"}
    runner.assert_not_called()
    lock_client.lock.return_value.release.assert_not_called()


def test_failed_job_releases_lock_and_does_not_log_private_exception(lock_client):
    runner = MagicMock(side_effect=ValueError("private provider response and receipt"))
    with pytest.raises(RuntimeError) as exc:
        tasks.run_exclusive("payments", runner)
    assert "private provider response" not in "".join(
        traceback.format_exception(exc.value)
    )
    lock_client.lock.return_value.release.assert_called_once()


@pytest.mark.parametrize("kind", ["payments", "deliveries", "receipts"])
def test_tasks_use_existing_audited_runners(lock_client, monkeypatch, kind):
    from app import delivery_worker, payment_worker, receipt_worker

    runner = MagicMock(return_value={"done": 1})
    module = {
        "payments": payment_worker,
        "deliveries": delivery_worker,
        "receipts": receipt_worker,
    }[kind]
    monkeypatch.setattr(module, "run", runner)
    task = {
        "payments": tasks.reconcile_payments,
        "deliveries": tasks.send_document_notifications,
        "receipts": tasks.send_payment_receipts,
    }[kind]
    assert task.run() == {"done": 1}
    runner.assert_called_once_with(tasks.settings.JOB_BATCH_SIZE)
    lock_client.lock.assert_called_once_with(
        f"transcriptske:job-lock:{kind}", timeout=tasks.JOB_LOCK_SECONDS
    )


def test_execution_limit_precedes_lock_expiry_and_redelivery():
    assert (
        celery_app.conf.task_time_limit
        < tasks.JOB_LOCK_SECONDS
        < celery_app.conf.broker_transport_options["visibility_timeout"]
    )
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_acks_late
    assert celery_app.conf.task_ignore_result


def test_background_readiness_reports_unreachable_broker_without_secrets(
    db, monkeypatch
):
    monkeypatch.setattr(tasks.settings, "BACKGROUND_JOBS_ENABLED", True)
    monkeypatch.setattr(tasks, "broker_ready", lambda: False)
    checks = {row["name"]: row for row in report(db)["checks"]}
    assert checks["background_queue"]["status"] == "blocked"
    assert "redis://" not in checks["background_queue"]["message"]


def test_user_services_restart_and_keep_paths_safe(tmp_path):
    units = service_units(tmp_path / "a project % folder")
    for content in units.values():
        assert "Restart=on-failure" in content
        assert "-m app.jobs check" in content
        assert "BACKGROUND_JOBS_ENABLED=true" in content
        assert "a project %% folder" in content
        assert "WantedBy=default.target" in content


def stop_process(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def eventually(check, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("Background service did not reach the expected state")


@pytest.mark.skipif(
    os.environ.get("RUN_REDIS_TESTS") != "1",
    reason="Opt-in real broker/worker integration",
)
def test_real_redis_worker_scheduler_and_overlap_recovery(
    engine, tmp_path, monkeypatch
):
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("Install redis-server for the integration check")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"redis://127.0.0.1:{port}/0"
    processes = []
    streams = []
    broker = Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
    previous_broker = celery_app.conf.broker_url
    try:
        stream = (tmp_path / "redis.log").open("w")
        streams.append(stream)
        processes.append(
            subprocess.Popen(
                [
                    executable,
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                ],
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )

        def redis_running():
            try:
                return broker.ping()
            except Exception:
                return False

        eventually(redis_running)
        monkeypatch.setattr(tasks.settings, "REDIS_URL", url)
        celery_app.conf.broker_url = url
        env = {
            **os.environ,
            "DATABASE_URL": str(engine.url),
            "REDIS_URL": url,
            "BACKGROUND_JOBS_ENABLED": "true",
            "PAYMENT_JOB_INTERVAL_SECONDS": "5",
            "DELIVERY_JOB_INTERVAL_SECONDS": "10",
            "ISSUANCE_ENABLED": "false",
            "PAYMENTS_ENABLED": "false",
            "MAIL_BACKEND": "file",
        }
        backend = Path(__file__).resolve().parents[1]
        stream = (tmp_path / "worker.log").open("w")
        streams.append(stream)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "app.celery_app:celery_app",
                    "worker",
                    "--concurrency=2",
                    "--loglevel=WARNING",
                    "--without-gossip",
                    "--without-mingle",
                ],
                cwd=backend,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )
        eventually(lambda: bool(celery_app.control.ping(timeout=0.5)))

        def runs(worker):
            with Session(engine) as db:
                return list(
                    db.scalars(select(WorkerRun).where(WorkerRun.worker == worker))
                )

        lock = broker.lock("transcriptske:job-lock:payments", timeout=30)
        assert lock.acquire(blocking=False)
        task_id = tasks.reconcile_payments.apply_async().id

        def completed_skip():
            active = celery_app.control.inspect(timeout=0.5).active() or {}
            reserved = celery_app.control.inspect(timeout=0.5).reserved() or {}
            queued = broker.llen("transcriptske:transcriptske.jobs")
            return not queued and all(
                item["id"] != task_id
                for worker in [*active.values(), *reserved.values()]
                for item in worker
            )

        eventually(completed_skip)
        assert not runs("payments")
        lock.release()
        tasks.reconcile_payments.apply_async()
        tasks.send_document_notifications.apply_async()
        tasks.send_payment_receipts.apply_async()
        eventually(
            lambda: (
                any(row.status == "succeeded" for row in runs("payments"))
                and any(row.status == "succeeded" for row in runs("deliveries"))
                and any(row.status == "succeeded" for row in runs("receipts"))
            )
        )
        initial = {
            worker: len(runs(worker))
            for worker in ("payments", "deliveries", "receipts")
        }
        stream = (tmp_path / "scheduler.log").open("w")
        streams.append(stream)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "app.celery_app:celery_app",
                    "beat",
                    "--loglevel=WARNING",
                    f"--schedule={tmp_path / 'schedule'}",
                    f"--pidfile={tmp_path / 'beat.pid'}",
                ],
                cwd=backend,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )
        eventually(
            lambda: all(
                len(runs(worker)) > initial[worker]
                and runs(worker)[-1].status == "succeeded"
                for worker in initial
            )
        )
    finally:
        for process in reversed(processes):
            stop_process(process)
        broker.close()
        for stream in streams:
            stream.close()
        celery_app.conf.broker_url = previous_broker
