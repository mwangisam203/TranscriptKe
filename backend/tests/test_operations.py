# Imported pytest fixtures intentionally share names with test parameters.
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.core.config import settings
from app.models.access import InstitutionMembership
from app.models.fulfillment import OrderHold, RegistrarCase
from app.models.issuance import DocumentDelivery
from app.models.operations import OperationsCase, WorkerRun
from app.models.orders import Order, OrderItem
from app.models.payments import PaymentAttempt, PaymentWebhook
from app.models.user import UserRole
from app.services.order_timing import planning_target
from app.services.orders import utcnow
from app.services.readiness import database_ready, report
from app.services.worker_runs import tracked_run
from tests.conftest import migration_config
from tests.test_issuance import issued, ready, registrar  # noqa: F401
from tests.test_orders import checked, submit, workflow  # noqa: F401
from tests.test_payments import gateway, payable, start  # noqa: F401


@pytest.fixture
def operations(client, workflow, catalog, auth_headers):  # noqa: F811
    submit(client, workflow)
    return {
        **workflow,
        "ops": f"/api/v1/staff/institutions/{catalog['institution'].id}/operations",
        "manager_headers": auth_headers(catalog["manager"]),
    }


def summary(client, w):
    return checked(client.get(w["ops"] + "/summary", headers=w["manager_headers"]))[
        "counts"
    ]


def queue(client, w, kind="all", extra=""):
    return checked(
        client.get(
            w["ops"] + f"/queue?kind={kind}{extra}", headers=w["manager_headers"]
        )
    )


def review(client, w, **changes):
    current = checked(client.get(w["url"], headers=w["headers"]))
    return client.put(
        w["ops"] + f"/orders/{w['order']['id']}",
        headers=w["manager_headers"],
        json={
            "expected_version": current["version"],
            "note": "PRIVATE: confirm archive staffing tomorrow",
            "follow_up_at": (utcnow() + timedelta(days=1)).isoformat(),
            **changes,
        },
    )


def test_submission_freezes_planning_target(client, operations, db, catalog):
    w = operations
    order = db.get(Order, w["order"]["id"])
    target = order.processing_due_at
    target_utc = target if target.tzinfo else target.replace(tzinfo=timezone.utc)
    assert target_utc == planning_target(order.submitted_at, order.submitted_snapshot)
    catalog["service"].processing_days_max = 100
    db.commit()
    assert queue(client, w)["items"][0]["processing_due_at"]
    db.refresh(order)
    assert order.processing_due_at == target


@pytest.mark.parametrize(
    "submitted,days,expected",
    [
        ("2026-09-18T10:00:00+00:00", 1, "2026-09-21T20:59:59+00:00"),
        ("2026-09-18T22:00:00+00:00", 1, "2026-09-21T20:59:59+00:00"),
        ("2026-09-20T10:00:00+00:00", 0, "2026-09-20T20:59:59+00:00"),
        ("2026-09-21T10:00:00+00:00", 5, "2026-09-28T20:59:59+00:00"),
    ],
)
def test_planning_clock_uses_nairobi_weekdays(submitted, days, expected):
    assert (
        planning_target(
            datetime.fromisoformat(submitted),
            {"items": [{"processing_days_max": days}]},
        ).isoformat()
        == expected
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"items": []},
        {"items": [{"processing_days_max": -1}]},
        {"items": [{"processing_days_max": True}]},
        {"items": [{"processing_days_max": 366}]},
    ],
)
def test_invalid_legacy_timing_stays_unknown(snapshot):
    assert planning_target(utcnow(), snapshot) is None


def test_counts_are_distinct_and_queues_match(client, operations, db, catalog):
    w = operations
    order = db.get(Order, w["order"]["id"])
    order.processing_due_at = utcnow() - timedelta(days=1)
    for _ in range(2):
        db.add(
            OrderHold(
                order_id=order.id,
                category="academic",
                student_message="Hold",
                internal_note="Private evidence",
                created_by=catalog["staff"].id,
            )
        )
    db.commit()
    result = summary(client, w)
    assert (
        result["submitted_total"]
        == result["overdue"]
        == result["holds"]
        == result["assignment_required"]
        == 1
    )
    assert queue(client, w, "holds")["total"] == 1
    item = queue(client, w, "overdue")["items"][0]
    assert {"overdue", "holds", "assignment_required"}.issubset(item["flags"])
    assert not any(
        key in str(item)
        for key in ("admissions@example.com", "PRIVATE", "code_hash", "academic_record")
    )


def test_assignment_revocation_and_completed_items(client, operations, db, catalog):
    w = operations
    db.add(RegistrarCase(order_id=w["order"]["id"], assigned_to=catalog["staff"].id))
    db.commit()
    assert summary(client, w)["assignment_required"] == 0
    member = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.user_id == catalog["staff"].id
        )
    )
    member.is_active = False
    db.commit()
    assert summary(client, w)["assignment_required"] == 1
    item = db.scalar(select(OrderItem).where(OrderItem.order_id == w["order"]["id"]))
    item.fulfillment_status = "issued"
    db.commit()
    assert summary(client, w)["open_fulfillment"] == 0
    assert summary(client, w)["assignment_required"] == 0


def test_manager_authorization_and_tenant_isolation(
    client, operations, user_factory, auth_headers, institutions, catalog, db
):
    from app.models.access import MembershipRole
    from tests.fixtures_academic import grant

    w = operations
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    foreign = user_factory("foreign@example.com")
    grant(db, foreign, institutions[1], MembershipRole.MANAGER)
    for headers in (
        w["headers"],
        w["staff_headers"],
        auth_headers(admin),
        auth_headers(foreign),
    ):
        assert client.get(w["ops"] + "/summary", headers=headers).status_code == 403
    other = f"/api/v1/staff/institutions/{institutions[1].id}/operations"
    assert (
        checked(client.get(other + "/summary", headers=auth_headers(foreign)))[
            "counts"
        ]["submitted_total"]
        == 0
    )
    assert (
        client.get(
            other + f"/orders/{w['order']['id']}", headers=auth_headers(foreign)
        ).status_code
        == 404
    )
    member = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.user_id == catalog["manager"].id
        )
    )
    member.is_active = False
    db.commit()
    assert (
        client.get(w["ops"] + "/queue", headers=w["manager_headers"]).status_code == 403
    )


def test_drafts_and_cancelled_drafts_are_excluded(
    client,
    workflow,
    catalog,
    auth_headers,
    db,  # noqa: F811
):
    w = {
        **workflow,
        "ops": f"/api/v1/staff/institutions/{catalog['institution'].id}/operations",
        "manager_headers": auth_headers(catalog["manager"]),
    }
    assert summary(client, w)["submitted_total"] == 0
    assert (
        client.get(
            w["ops"] + f"/orders/{w['order']['id']}", headers=w["manager_headers"]
        ).status_code
        == 404
    )
    db.get(Order, w["order"]["id"]).status = "cancelled"
    db.commit()
    assert queue(client, w)["total"] == 0


def test_private_follow_up_and_version_conflicts(client, operations, db):
    w = operations
    result = checked(review(client, w))
    assert result["note"].startswith("PRIVATE") and len(result["history"]) == 1
    assert review(client, w, expected_version=1).status_code == 409
    for suffix in ("", "/timeline", "/fulfillment"):
        assert "PRIVATE" not in str(
            checked(client.get(w["url"] + suffix, headers=w["headers"]))
        )
    case = db.get(OperationsCase, w["order"]["id"])
    case.follow_up_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert queue(client, w, "follow_up")["total"] == 1
    checked(
        review(
            client,
            w,
            follow_up_at=None,
            note="Follow-up completed; no workflow override.",
        )
    )
    assert queue(client, w, "follow_up")["total"] == 0
    assert summary(client, w)["open_fulfillment"] == 1


@pytest.mark.parametrize(
    "follow_up", ["2026-01-01T10:00:00", "2020-01-01T10:00:00Z", "2099-01-01T10:00:00Z"]
)
def test_follow_up_requires_bounded_future_timezone(client, operations, follow_up):
    assert review(client, operations, follow_up_at=follow_up).status_code == 422


def test_manager_cannot_review_own_order(client, operations, db, catalog):
    w = operations
    order = db.get(Order, w["order"]["id"])
    order.user_id = catalog["manager"].id
    db.commit()
    assert (
        client.put(
            w["ops"] + f"/orders/{order.id}",
            headers=w["manager_headers"],
            json={"expected_version": order.version, "note": "Own review"},
        ).status_code
        == 403
    )


def test_payment_queue_ages_pending_and_shows_unprocessed_callbacks(
    client,
    payable,
    db,
    gateway,
    catalog,
    auth_headers,  # noqa: F811
):
    w = {
        **payable,
        "ops": f"/api/v1/staff/institutions/{catalog['institution'].id}/operations",
        "manager_headers": auth_headers(catalog["manager"]),
    }
    payment = checked(start(client, w), 201)
    assert queue(client, w, "payments")["total"] == 0
    row = db.get(PaymentAttempt, payment["id"])
    row.created_at = utcnow() - timedelta(hours=1)
    db.commit()
    assert queue(client, w, "payments")["total"] == 1
    row.status = "failed"
    db.commit()
    assert queue(client, w, "payments")["total"] == 0
    webhook = PaymentWebhook(
        provider="stripe",
        event_key="event-waiting",
        payment_id=row.id,
        payload={},
        created_at=utcnow() - timedelta(hours=1),
    )
    db.add(webhook)
    db.commit()
    assert queue(client, w, "payments")["total"] == 1
    webhook.processed_at = utcnow()
    db.commit()
    assert queue(client, w, "payments")["total"] == 0


def test_delivery_queue_includes_failures_and_expiry(
    client,
    issued,
    db,
    catalog,
    auth_headers,  # noqa: F811
):
    w = {
        **issued,
        "ops": f"/api/v1/staff/institutions/{catalog['institution'].id}/operations",
        "manager_headers": auth_headers(catalog["manager"]),
    }
    assert queue(client, w, "deliveries")["total"] == 0
    delivery = db.get(DocumentDelivery, w["delivery_id"])
    delivery.notification_attempts = 1
    db.commit()
    assert queue(client, w, "deliveries")["total"] == 1
    delivery.notified_at = utcnow()
    delivery.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert queue(client, w, "deliveries")["total"] == 1
    delivery.download_count = 1
    db.commit()
    assert queue(client, w, "deliveries")["total"] == 0


def test_queue_validation(client, operations):
    w = operations
    for query in ("kind=wrong", "limit=101", "offset=-1"):
        assert (
            client.get(
                w["ops"] + "/queue?" + query, headers=w["manager_headers"]
            ).status_code
            == 422
        )
    assert queue(client, w, extra="&offset=100")["items"] == []


def test_competing_operations_reviews_only_one_wins(client, operations, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL locking")
    w = operations
    current = checked(client.get(w["url"], headers=w["headers"]))["version"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: review(client, w, expected_version=current), range(2))
        )
    assert sorted(response.status_code for response in responses) == [200, 409]


def test_worker_health_records_success_failure_and_exception(engine, db):
    factory = sessionmaker(bind=engine)
    with tracked_run(factory, "deliveries") as counts:
        counts.update(processed=3, failed=0)
    with tracked_run(factory, "payments") as counts:
        counts.update(processed=2, failed=1)
    with pytest.raises(RuntimeError):
        with tracked_run(factory, "payments"):
            raise RuntimeError("private provider detail")
    rows = list(db.scalars(select(WorkerRun).order_by(WorkerRun.started_at)))
    assert [r.status for r in rows] == ["succeeded", "failed", "failed"]
    assert all(r.finished_at for r in rows)
    assert "private" not in str([r.__dict__ for r in rows])


def test_readiness_masks_secrets_and_checks_worker_freshness(
    db,
    monkeypatch,
    catalog,
    gateway,  # noqa: F811
):
    monkeypatch.setattr(settings, "ISSUANCE_ENABLED", False)
    result = report(db)
    assert not result["configuration_ready"]
    assert not any(
        secret in str(result)
        for secret in (
            settings.SECRET_KEY,
            settings.STRIPE_SECRET_KEY,
            settings.DATABASE_URL,
        )
    )
    db.add(WorkerRun(worker="payments", status="succeeded", finished_at=utcnow()))
    db.commit()
    assert report(db)["configuration_ready"]
    run = db.scalar(select(WorkerRun))
    run.finished_at = utcnow() - timedelta(hours=1)
    db.commit()
    assert not report(db)["configuration_ready"]
    assert not report(db, live=True)["configuration_ready"]


def test_readiness_probe_rejects_old_schema_without_disclosing_details(client, db):
    assert client.get("/health/ready").json() == {"status": "ready"}
    db.execute(text("UPDATE alembic_version SET version_num='0007'"))
    db.commit()
    response = client.get("/health/ready")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert not database_ready(db)


def test_migration_backfills_existing_quotes(client, operations, engine, db):
    w = operations
    identifier = w["order"]["id"]
    db.rollback()
    with engine.begin() as connection:
        config = migration_config(connection)
        command.downgrade(config, "0007")
        original = connection.execute(
            text("SELECT submitted_at, submitted_snapshot FROM orders WHERE id=:id"),
            {"id": identifier},
        ).one()
        command.upgrade(config, "head")
        restored = connection.execute(
            text(
                "SELECT processing_due_at, submitted_at, submitted_snapshot FROM orders WHERE id=:id"
            ),
            {"id": identifier},
        ).one()
        assert restored.processing_due_at is not None
        assert restored.submitted_at == original.submitted_at
        assert restored.submitted_snapshot == original.submitted_snapshot


def test_configuration_validation_does_not_echo_secrets():
    from pydantic import ValidationError

    from app.core.config import Settings

    private = "private-key-do-not-print-12345678901234567890"
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            DATABASE_URL="sqlite://",
            SECRET_KEY=private,
            ISSUANCE_ENABLED=True,
            ISSUANCE_MODE="live",
        )
    assert private not in str(error.value)


def test_database_readiness_failure_is_generic():
    from sqlalchemy.exc import OperationalError

    class Unavailable:
        def execute(self, query):
            raise OperationalError(
                "SELECT 1", {}, Exception("private database details")
            )

        def rollback(self):
            self.rolled_back = True

    db = Unavailable()
    assert database_ready(db) is False and db.rolled_back


def test_queue_pagination_is_stable_and_submitted_cancellations_remain_visible(
    client, operations, db, catalog
):
    from app.models.orders import OrderRecipient

    w = operations
    original = db.get(Order, w["order"]["id"])
    for index in range(2):
        clone = Order(
            user_id=original.user_id,
            institution_id=original.institution_id,
            academic_record_link_id=original.academic_record_link_id,
            creation_key=f"operations-clone-{index}",
            creation_hash="x" * 64,
            status="cancellation_requested" if index else "submitted",
            submitted_at=original.submitted_at,
            submitted_snapshot=original.submitted_snapshot,
            processing_due_at=original.processing_due_at,
        )
        db.add(clone)
        db.flush()
        db.add(
            OrderRecipient(
                order_id=clone.id,
                key="recipient1",
                name="Admissions",
                delivery_method="secure_electronic",
                email="admissions@example.com",
            )
        )
        db.flush()
        db.add(
            OrderItem(
                order_id=clone.id,
                key="item1",
                service_id=catalog["service"].id,
                recipient_key="recipient1",
                quantity=1,
                fulfillment_status="awaiting_review",
            )
        )
    db.commit()
    result = queue(client, w, extra="&limit=1")
    second = queue(client, w, extra="&limit=1&offset=1")
    assert result["total"] == second["total"] == 3
    assert result["items"][0]["id"] < second["items"][0]["id"]
    assert queue(client, w, "cancellations")["total"] == 1


def test_follow_up_keeps_blockers_and_no_financial_mutation(
    client, operations, db, catalog
):
    w = operations
    db.add(
        OrderHold(
            order_id=w["order"]["id"],
            category="academic",
            student_message="Hold",
            internal_note="Review",
            created_by=catalog["staff"].id,
        )
    )
    db.commit()
    checked(review(client, w, note="Reviewed but the underlying hold remains."))
    assert summary(client, w)["holds"] == 1
    assert db.get(Order, w["order"]["id"]).payment_status == "not_started"


def test_readiness_rejects_missing_scanner_and_failed_worker(
    db, monkeypatch, catalog, gateway
):  # noqa: F811
    import app.services.readiness as readiness

    monkeypatch.setattr(settings, "ISSUANCE_ENABLED", True)
    monkeypatch.setattr(settings, "ATTACHMENT_SCANNER", "clamav")
    monkeypatch.setattr(readiness.shutil, "which", lambda _: None)
    db.add(
        WorkerRun(worker="payments", status="failed", finished_at=utcnow(), failed=1)
    )
    db.commit()
    checks = {check["name"]: check["status"] for check in report(db)["checks"]}
    assert (
        checks["scanner"]
        == checks["worker_payments"]
        == checks["worker_deliveries"]
        == "blocked"
    )


def test_readiness_cli_reports_bad_configuration_without_secret_values():
    import json
    import os
    import subprocess
    import sys

    private = "test-only-private-value-not-for-output-12345"
    environment = {
        **os.environ,
        "APP_ENV": "production",
        "MAIL_BACKEND": "file",
        "DATABASE_URL": "sqlite://",
        "SECRET_KEY": private,
        "ISSUANCE_ENABLED": "false",
        "PAYMENTS_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.readiness", "--live"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (
        result.returncode == 1
        and json.loads(result.stdout)["configuration_ready"] is False
    )
    assert private not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_readiness_detects_changed_provider_account(client, payable, db, monkeypatch):
    checked(start(client, payable), 201)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_different_account")
    checks = {check["name"]: check for check in report(db)["checks"]}
    assert checks["payment_binding"]["status"] == "blocked"
    assert "sk_test_different_account" not in str(checks)
    assert checks["worker_payments"]["last_status"] is None


def test_follow_up_preserves_offset_as_an_instant(client, operations, monkeypatch):
    import app.services.operations as service

    instant = (utcnow() + timedelta(hours=1)).replace(microsecond=0)
    requested = instant.astimezone(timezone(timedelta(hours=3)))
    result = checked(review(client, operations, follow_up_at=requested.isoformat()))
    assert datetime.fromisoformat(result["follow_up_at"]) == instant
    monkeypatch.setattr(service, "utcnow", lambda: instant + timedelta(seconds=1))
    assert queue(client, operations, "follow_up")["total"] == 1
