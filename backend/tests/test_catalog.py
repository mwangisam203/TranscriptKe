import pytest
from sqlalchemy import select

from app.cli import seed_academic_demo
from app.core.config import settings
from app.models.academic import InstitutionService, OrderingPolicy
from app.models.access import AccessEvent, MembershipRole
from app.models.user import UserRole
from tests.fixtures_academic import POLICY, SERVICE, grant


def test_manager_configures_public_catalog(
    client, db, user_factory, auth_headers, institutions
):
    manager = user_factory()
    grant(db, manager, institutions[0], MembershipRole.MANAGER)
    base = f"/api/v1/staff/institutions/{institutions[0].id}"
    headers = auth_headers(manager)
    assert client.get(base + "/ordering-policy", headers=headers).json()["version"] == 0
    response = client.post(
        base + "/services", json={**SERVICE, "code": " transcript "}, headers=headers
    )
    assert response.status_code == 201
    service = response.json()
    assert service["code"] == "TRANSCRIPT" and service["version"] == 1
    assert service["fee_minor"] == 150050
    assert (
        client.put(base + "/ordering-policy", headers=headers, json=POLICY).status_code
        == 200
    )
    public = f"/api/v1/institutions/{institutions[0].id}"
    assert client.get(public + "/services").json() == [service]
    assert client.get(public + "/ordering-policy").json()["required_fields"] == [
        "attendance_start_year"
    ]
    events = db.scalars(select(AccessEvent)).all()
    assert {event.action for event in events} == {
        "service_created",
        "ordering_policy_updated",
    }
    assert events[0].details["configuration"]["fee_minor"] == 150050


@pytest.mark.parametrize(
    "changes",
    [
        {"fee_minor": -1},
        {"fee_minor": 1500.5},
        {"fee_minor": True},
        {"fee_minor": 1000000001},
        {"currency": "USD"},
        {"name": "   "},
        {"code": "bad code"},
        {"processing_days_min": 8, "processing_days_max": 2},
        {"processing_days_min": -1},
        {"delivery_methods": []},
        {"delivery_methods": ["unknown"]},
        {"delivery_methods": ["post", "post"]},
        {"required_fields": ["national_id"]},
        {"required_fields": ["program", "program"]},
        {"institution_id": 999},
    ],
)
def test_invalid_service_configuration_rejected(client, catalog, auth_headers, changes):
    response = client.post(
        f"/api/v1/staff/institutions/{catalog['institution'].id}/services",
        headers=auth_headers(catalog["manager"]),
        json={**SERVICE, "code": "NEW", **changes},
    )
    assert response.status_code == 422


def test_service_updates_require_current_version_and_support_deactivation(
    client, catalog, auth_headers, db
):
    base = f"/api/v1/staff/institutions/{catalog['institution'].id}/services"
    headers = auth_headers(catalog["manager"])
    payload = {
        **SERVICE,
        "expected_version": 1,
        "fee_minor": 220000,
        "is_active": False,
    }
    response = client.put(
        f"{base}/{catalog['service'].id}", headers=headers, json=payload
    )
    assert response.status_code == 200 and response.json()["version"] == 2
    assert (
        client.put(
            f"{base}/{catalog['service'].id}", headers=headers, json=payload
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/institutions/{catalog['institution'].id}/services").json()
        == []
    )
    assert len(client.get(base, headers=headers).json()) == 1
    event = db.scalar(
        select(AccessEvent).where(AccessEvent.action == "service_updated")
    )
    assert (
        event.details["before"]["fee_minor"] == 150050
        and event.details["after"]["fee_minor"] == 220000
    )


def test_policy_conflicts_and_required_field_validation(client, catalog, auth_headers):
    base = f"/api/v1/staff/institutions/{catalog['institution'].id}/ordering-policy"
    headers = auth_headers(catalog["manager"])
    assert client.put(base, headers=headers, json=POLICY).status_code == 409
    assert (
        client.put(
            base, headers=headers, json={**POLICY, "expected_version": 1}
        ).status_code
        == 200
    )
    assert (
        client.put(
            base,
            headers=headers,
            json={**POLICY, "expected_version": 2, "required_fields": ["national_id"]},
        ).status_code
        == 422
    )


def test_duplicate_service_code_scoped_to_institution(
    client, catalog, auth_headers, institutions, db
):
    headers = auth_headers(catalog["manager"])
    own = f"/api/v1/staff/institutions/{institutions[0].id}/services"
    assert client.post(own, headers=headers, json=SERVICE).status_code == 409
    grant(db, catalog["manager"], institutions[1], MembershipRole.MANAGER)
    assert (
        client.post(
            f"/api/v1/staff/institutions/{institutions[1].id}/services",
            headers=headers,
            json=SERVICE,
        ).status_code
        == 201
    )


def test_staff_and_admin_without_membership_cannot_configure(
    client, catalog, user_factory, auth_headers, institutions
):
    base = f"/api/v1/staff/institutions/{institutions[0].id}"
    assert (
        client.get(
            base + "/services", headers=auth_headers(catalog["staff"])
        ).status_code
        == 200
    )
    for user in [
        catalog["staff"],
        user_factory(),
        user_factory("admin@example.com", role=UserRole.ADMIN),
    ]:
        headers = auth_headers(user)
        assert (
            client.post(
                base + "/services", headers=headers, json={**SERVICE, "code": "NEW"}
            ).status_code
            == 403
        )
        assert (
            client.put(
                base + "/ordering-policy",
                headers=headers,
                json={**POLICY, "expected_version": 1},
            ).status_code
            == 403
        )
    assert (
        client.get(
            f"/api/v1/staff/institutions/{institutions[1].id}/services",
            headers=auth_headers(catalog["manager"]),
        ).status_code
        == 403
    )


def test_foreign_service_id_cannot_be_updated(
    client, catalog, institutions, db, auth_headers
):
    foreign = InstitutionService(institution_id=institutions[1].id, **SERVICE)
    db.add(foreign)
    db.commit()
    response = client.put(
        f"/api/v1/staff/institutions/{institutions[0].id}/services/{foreign.id}",
        headers=auth_headers(catalog["manager"]),
        json={**SERVICE, "expected_version": 1},
    )
    assert response.status_code == 404


def test_approval_is_platform_only_and_unapproved_catalog_is_hidden(
    client, catalog, user_factory, auth_headers, db
):
    institution = catalog["institution"]
    institution.is_approved = False
    db.commit()
    public = f"/api/v1/institutions/{institution.id}"
    assert institution.id not in [
        item["id"] for item in client.get("/api/v1/institutions").json()
    ]
    for suffix in ["", "/services", "/ordering-policy"]:
        assert client.get(public + suffix).status_code == 404
    endpoint = f"/api/v1/admin/institutions/{institution.id}/approval"
    payload = {
        "approved": True,
        "expected_approved": False,
        "reason": "Fictional institution approved for local demonstration.",
    }
    assert (
        client.put(
            endpoint, headers=auth_headers(catalog["manager"]), json=payload
        ).status_code
        == 403
    )
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)
    assert client.put(endpoint, headers=headers, json=payload).status_code == 200
    assert client.put(endpoint, headers=headers, json=payload).status_code == 409
    assert client.get(public + "/services").status_code == 200
    event = db.scalar(
        select(AccessEvent).where(AccessEvent.action == "institution_approval_changed")
    )
    assert event.details["reason"] == payload["reason"]


def test_missing_policy_is_closed_by_default(client, institutions):
    data = client.get(
        f"/api/v1/institutions/{institutions[0].id}/ordering-policy"
    ).json()
    assert data["version"] == 0 and data["accepting_requests"] is False


def test_demo_catalog_seed_is_repeatable(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    seed_academic_demo(db)
    services = db.scalars(select(InstitutionService)).all()
    policies = db.scalars(select(OrderingPolicy)).all()
    assert len(services) == len(policies) == 2
    services[0].fee_minor = 777
    policies[0].accepting_requests = False
    db.commit()
    seed_academic_demo(db)
    assert len(db.scalars(select(InstitutionService)).all()) == 2
    db.refresh(services[0])
    db.refresh(policies[0])
    assert services[0].fee_minor == 777 and not policies[0].accepting_requests
