from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.models.academic import AcademicRecordLink
from app.models.access import InstitutionMembership
from app.models.orders import Order, OrderConsent
from tests.fixtures_academic import grant
from tests.test_orders import checked, submit, workflow  # noqa: F401


@pytest.fixture
def registrar(client, workflow):  # noqa: F811
    w = workflow
    submit(client, w)
    w["registrar_url"] = w["staff_url"] + "/fulfillment"
    progress = checked(client.get(w["registrar_url"], headers=w["staff_headers"]))
    # Resolve staff identity through the existing auth endpoint.
    staff = checked(client.get("/api/v1/auth/me", headers=w["staff_headers"]))
    checked(
        client.put(
            w["registrar_url"] + "/assignment",
            headers=w["staff_headers"],
            json={"expected_version": progress["version"], "user_id": staff["id"]},
        )
    )
    return w


def action(client, w, path, **payload):
    progress = checked(client.get(w["registrar_url"], headers=w["staff_headers"]))
    return client.post(
        w["registrar_url"] + path,
        headers=w["staff_headers"],
        json={"expected_version": progress["version"], **payload},
    )


def decision(client, w, choice="approve"):
    return action(
        client,
        w,
        "/items/item1/decisions",
        decision=choice,
        student_message="Your document request was reviewed.",
        internal_note="PRIVATE: registrar verified the archive source.",
    )


def test_review_preparation_and_private_history(client, registrar):
    w = registrar
    checked(decision(client, w))
    progress = checked(decision(client, w, "ready"))
    assert progress["items"][0]["status"] == "ready"
    assert not progress["can_release"]
    assert any("payment" in b for b in progress["release_blockers"])
    assert any("delivery" in b for b in progress["release_blockers"])
    assert progress["preparation_blockers"] == []
    assert any("PRIVATE" in e["internal_note"] for e in progress["history"])
    for suffix in ["", "/fulfillment", "/timeline", "/messages"]:
        response = checked(client.get(w["url"] + suffix, headers=w["headers"]))
        assert "PRIVATE" not in str(response) and "internal_note" not in str(response)
    student = checked(client.get(w["url"] + "/fulfillment", headers=w["headers"]))
    assert student["items"][0]["status"] == "ready"
    assert "assigned_to" not in student and "history" not in student


@pytest.mark.parametrize("choice", ["ready", "reopen"])
def test_invalid_transitions(client, registrar, choice):
    assert decision(client, registrar, choice).status_code in (403, 409)


def test_holds_block_progress_and_resolution_preserves_history(client, registrar):
    w = registrar
    progress = checked(
        action(
            client,
            w,
            "/holds",
            category="academic",
            student_message="Please clear the academic hold.",
            internal_note="PRIVATE: archive discrepancy.",
        ),
        201,
    )
    hold = progress["holds"][0]
    assert decision(client, w).status_code == 409
    progress = checked(
        action(
            client,
            w,
            f"/holds/{hold['id']}/resolution",
            student_message="Academic hold cleared.",
            internal_note="PRIVATE: checked ledger.",
        )
    )
    assert (
        progress["holds"][0]["resolved_at"]
        and progress["holds"][0]["resolution"] == "Academic hold cleared."
    )
    assert (
        action(
            client,
            w,
            f"/holds/{hold['id']}/resolution",
            student_message="Again",
            internal_note="Again",
        ).status_code
        == 409
    )
    checked(decision(client, w))
    assert (
        action(
            client,
            w,
            "/holds/999999/resolution",
            student_message="Other hold",
            internal_note="Other hold",
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "gate", ["record", "consent", "question", "approval", "cancellation", "revocation"]
)
def test_processing_rechecks_current_gates(client, registrar, db, catalog, gate):
    w = registrar
    if gate == "record":
        db.get(AcademicRecordLink, w["link"]["id"]).version += 1
    elif gate == "consent":
        order = db.get(Order, w["order"]["id"])
        db.get(OrderConsent, order.submission_consent_id).scope_hash = "invalid"
    elif gate == "approval":
        catalog["institution"].is_approved = False
    elif gate == "question":
        checked(
            client.post(
                w["staff_url"] + "/messages",
                headers=w["staff_headers"],
                json={"body": "Please confirm attendance.", "requires_response": True},
            ),
            201,
        )
    elif gate == "cancellation":
        order = checked(client.get(w["url"], headers=w["headers"]))
        checked(
            client.post(
                w["url"] + "/cancellation-requests",
                headers=w["headers"],
                json={
                    "expected_version": order["version"],
                    "reason": "No longer needed",
                },
            )
        )
    else:
        db.scalar(
            select(InstitutionMembership).where(
                InstitutionMembership.user_id == catalog["staff"].id
            )
        ).is_active = False
    db.commit()
    if gate == "revocation":
        assert (
            client.get(w["registrar_url"], headers=w["staff_headers"]).status_code
            == 403
        )
        response = client.post(
            w["registrar_url"] + "/items/item1/decisions",
            headers=w["staff_headers"],
            json={
                "expected_version": 4,
                "decision": "approve",
                "student_message": "Reviewed",
                "internal_note": "Reviewed",
            },
        )
        assert response.status_code == 403
    else:
        assert decision(client, w).status_code == 409


def test_deferred_release_confirmation_and_withdrawal(client, workflow):  # noqa: F811
    w = workflow
    checked(
        client.put(
            w["url"],
            headers=w["headers"],
            json={
                **w["body"],
                "expected_version": w["order"]["version"],
                "release_when": "after_graduation",
                "release_instruction": "December graduation",
            },
        )
    )
    submit(client, w)
    w["registrar_url"] = w["staff_url"] + "/fulfillment"
    progress = checked(client.get(w["registrar_url"], headers=w["staff_headers"]))
    staff = checked(client.get("/api/v1/auth/me", headers=w["staff_headers"]))
    checked(
        client.put(
            w["registrar_url"] + "/assignment",
            headers=w["staff_headers"],
            json={"expected_version": progress["version"], "user_id": staff["id"]},
        )
    )
    checked(decision(client, w))
    assert decision(client, w, "ready").status_code == 409
    checked(
        action(
            client,
            w,
            "/release-confirmation",
            confirmed=True,
            internal_note="Graduation register independently checked.",
        )
    )
    checked(decision(client, w, "ready"))
    progress = checked(
        action(
            client,
            w,
            "/release-confirmation",
            confirmed=False,
            internal_note="Graduation event requires further review.",
        )
    )
    assert (
        progress["items"][0]["status"] == "processing"
        and progress["release_confirmed_at"] is None
    )
    assert decision(client, w, "ready").status_code == 409


def test_assignment_requires_membership_and_manager_for_reassignment(
    client, registrar, catalog, user_factory, auth_headers, db, institutions
):
    w = registrar
    other = user_factory("other-staff@example.com")
    grant(db, other, catalog["institution"])
    foreign = user_factory("foreign-staff@example.com")
    grant(db, foreign, institutions[1])
    progress = checked(client.get(w["registrar_url"], headers=w["staff_headers"]))
    for target, headers, expected in [
        (other.id, auth_headers(other), 403),
        (foreign.id, auth_headers(catalog["manager"]), 403),
        (w["user"].id, auth_headers(catalog["manager"]), 403),
    ]:
        assert (
            client.put(
                w["registrar_url"] + "/assignment",
                headers=headers,
                json={"expected_version": progress["version"], "user_id": target},
            ).status_code
            == expected
        )
    checked(
        client.put(
            w["registrar_url"] + "/assignment",
            headers=auth_headers(catalog["manager"]),
            json={"expected_version": progress["version"], "user_id": other.id},
        )
    )
    assert decision(client, w).status_code == 409
    assert (
        client.get(w["registrar_url"], headers=auth_headers(foreign)).status_code == 403
    )
    assert (
        client.get(w["url"] + "/fulfillment", headers=auth_headers(other)).status_code
        == 404
    )


def test_rejection_cancellation_and_manager_reopening(
    client, registrar, catalog, auth_headers
):
    w = registrar
    rejected = checked(decision(client, w, "reject"))
    assert rejected["items"][0]["status"] == "rejected"
    assert decision(client, w, "approve").status_code == 409
    assert decision(client, w, "reopen").status_code == 403
    checked(
        client.put(
            w["registrar_url"] + "/assignment",
            headers=auth_headers(catalog["manager"]),
            json={
                "expected_version": rejected["version"],
                "user_id": catalog["manager"].id,
            },
        )
    )
    manager = {**w, "staff_headers": auth_headers(catalog["manager"])}
    checked(decision(client, manager, "reopen"))
    order = checked(client.get(w["url"], headers=w["headers"]))
    checked(
        client.post(
            w["url"] + "/cancellation-requests",
            headers=w["headers"],
            json={
                "expected_version": order["version"],
                "reason": "Withdraw after review",
            },
        )
    )
    assert decision(client, manager).status_code == 409


def test_processing_prevents_simple_cancellation(client, registrar):
    w = registrar
    progress = checked(decision(client, w))
    assert (
        client.post(
            w["url"] + "/cancellation-requests",
            headers=w["headers"],
            json={"expected_version": progress["version"], "reason": "Changed plans"},
        ).status_code
        == 409
    )


def test_queue_filters_and_version_conflicts(client, registrar, catalog):
    w = registrar
    queue = f"/api/v1/staff/institutions/{catalog['institution'].id}/registrar-queue"
    assert (
        len(
            checked(
                client.get(queue + "?state=awaiting_review", headers=w["staff_headers"])
            )
        )
        == 1
    )
    assert (
        checked(client.get(queue + "?unassigned=true", headers=w["staff_headers"]))
        == []
    )
    assert (
        checked(client.get(queue + "?on_hold=true", headers=w["staff_headers"])) == []
    )
    checked(
        action(
            client,
            w,
            "/holds",
            category="administrative",
            student_message="Please contact the registrar.",
            internal_note="Private review",
        ),
        201,
    )
    assert (
        len(checked(client.get(queue + "?on_hold=true", headers=w["staff_headers"])))
        == 1
    )
    assert (
        client.get(queue + "?state=invalid", headers=w["staff_headers"]).status_code
        == 422
    )
    assert (
        client.get(queue + "?limit=101", headers=w["staff_headers"]).status_code == 422
    )
    assert (
        client.post(
            w["registrar_url"] + "/holds",
            headers=w["staff_headers"],
            json={
                "expected_version": 1,
                "category": "academic",
                "student_message": "Late update",
                "internal_note": "Late update",
            },
        ).status_code
        == 409
    )


def test_concurrent_review_only_one_transition(client, registrar, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL locking check")
    w = registrar
    progress = checked(client.get(w["registrar_url"], headers=w["staff_headers"]))
    payload = {
        "expected_version": progress["version"],
        "student_message": "Reviewed",
        "internal_note": "Source checked",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda choice: client.post(
                    w["registrar_url"] + "/items/item1/decisions",
                    headers=w["staff_headers"],
                    json={**payload, "decision": choice},
                ),
                ["approve", "reject"],
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_own_order_and_drafts_cannot_be_processed(
    client,
    workflow,  # noqa: F811 -- imported pytest fixture
    catalog,
    db,
    auth_headers,
):
    w = workflow
    url = w["staff_url"] + "/fulfillment"
    assert client.get(url, headers=w["staff_headers"]).status_code == 404
    grant(db, w["user"], catalog["institution"])
    order, _, _ = submit(client, w)
    assert (
        client.put(
            url + "/assignment",
            headers=auth_headers(w["user"]),
            json={"expected_version": order["version"], "user_id": w["user"].id},
        ).status_code
        == 403
    )


def test_registrar_roster_is_scoped_and_omits_ineligible_accounts(
    client, registrar, catalog, user_factory, db, auth_headers, institutions
):
    from app.models.user import UserRole

    url = f"/api/v1/staff/institutions/{catalog['institution'].id}/registrars"
    unverified = user_factory("unverified@example.com", verified=False)
    grant(db, unverified, catalog["institution"])
    foreign = user_factory("foreign@example.com")
    grant(db, foreign, institutions[1])
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    roster = checked(client.get(url, headers=registrar["staff_headers"]))
    assert {entry["id"] for entry in roster} == {
        catalog["staff"].id,
        catalog["manager"].id,
    }
    assert all("email" not in entry for entry in roster)
    assert client.get(url, headers=auth_headers(admin)).status_code == 403
    assert client.get(url, headers=auth_headers(foreign)).status_code == 403
    assert (
        len(checked(client.get(url + "?limit=1", headers=registrar["staff_headers"])))
        == 1
    )
