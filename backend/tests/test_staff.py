from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.access import (
    AccessEvent,
    ActionToken,
    InstitutionMembership,
    MembershipRole,
)
from app.models.user import UserRole
from app.services.tokens import utcnow

STAFF = "/api/v1/staff"


def grant(db, user, institution, role=MembershipRole.STAFF):
    membership = InstitutionMembership(
        user_id=user.id, institution_id=institution.id, role=role
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def test_student_and_legacy_staff_role_do_not_grant_institution_access(
    client, user_factory, auth_headers, institutions
):
    for role in [UserRole.STUDENT, UserRole.INSTITUTION_STAFF]:
        user = user_factory(email=f"{role}@example.com", role=role)
        headers = auth_headers(user)
        assert (
            client.get(
                f"{STAFF}/institutions/{institutions[0].id}/members", headers=headers
            ).status_code
            == 403
        )
        assert client.get(f"{STAFF}/memberships", headers=headers).json() == []


def test_membership_is_scoped_and_staff_cannot_invite(
    client, db, user_factory, auth_headers, institutions
):
    user = user_factory(role=UserRole.INSTITUTION_STAFF)
    grant(db, user, institutions[0])
    headers = auth_headers(user)
    assert (
        client.get(
            f"{STAFF}/institutions/{institutions[0].id}/members", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"{STAFF}/institutions/{institutions[1].id}/members", headers=headers
        ).status_code
        == 403
    )
    response = client.post(
        f"{STAFF}/institutions/{institutions[0].id}/invitations",
        headers=headers,
        json={"email": "other@example.com"},
    )
    assert response.status_code == 403


def test_admin_invitation_acceptance_and_replay(
    client, db, user_factory, auth_headers, institutions, mailer
):
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    student = user_factory()
    institution = institutions[0]
    response = client.post(
        f"{STAFF}/institutions/{institution.id}/invitations",
        headers=auth_headers(admin),
        json={"email": student.email, "role": "manager"},
    )
    assert response.status_code == 201
    assert "token_hash" not in response.json() and "token" not in response.json()
    raw = mailer.token("staff_invitation")
    headers = auth_headers(student)
    accepted = client.post(
        f"{STAFF}/invitations/accept", headers=headers, json={"token": raw}
    )
    assert accepted.status_code == 200
    assert (
        accepted.json()["role"] == "manager"
        and accepted.json()["institution_id"] == institution.id
    )
    assert (
        client.post(
            f"{STAFF}/invitations/accept", headers=headers, json={"token": raw}
        ).status_code
        == 400
    )
    db.refresh(student)
    assert student.role == UserRole.INSTITUTION_STAFF
    assert (
        client.get(
            f"{STAFF}/institutions/{institution.id}/members", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"{STAFF}/institutions/{institutions[1].id}/members", headers=headers
        ).status_code
        == 403
    )
    actions = db.scalars(select(AccessEvent.action)).all()
    assert "staff_invited" in actions and "invitation_accepted" in actions


def test_manager_may_invite_staff_only_at_own_institution(
    client, db, user_factory, auth_headers, institutions
):
    manager = user_factory()
    grant(db, manager, institutions[0], MembershipRole.MANAGER)
    headers = auth_headers(manager)
    own_url = f"{STAFF}/institutions/{institutions[0].id}/invitations"
    payload = {"email": "staff@example.com", "role": "staff"}
    assert client.post(own_url, headers=headers, json=payload).status_code == 201
    for role in ["manager", "admin"]:
        response = client.post(own_url, headers=headers, json={**payload, "role": role})
        assert response.status_code == (403 if role == "manager" else 422)
    assert (
        client.post(
            f"{STAFF}/institutions/{institutions[1].id}/invitations",
            headers=headers,
            json=payload,
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "case", ["wrong_email", "unverified", "expired", "revoked", "inactive_institution"]
)
def test_invitation_rejects_ineligible_redemption(
    client, db, user_factory, auth_headers, institutions, mailer, case
):
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    student = user_factory(verified=case != "unverified")
    institution = institutions[0]
    response = client.post(
        f"{STAFF}/institutions/{institution.id}/invitations",
        headers=auth_headers(admin),
        json={"email": student.email},
    )
    assert response.status_code == 201
    record = db.get(ActionToken, response.json()["id"])
    expected = 400
    if case == "wrong_email":
        student = user_factory("other@example.com")
        expected = 403
    elif case == "unverified":
        expected = 403
    elif case == "expired":
        record.expires_at = utcnow() - timedelta(seconds=1)
    elif case == "revoked":
        assert (
            client.delete(
                f"{STAFF}/institutions/{institution.id}/invitations/{record.id}",
                headers=auth_headers(admin),
            ).status_code
            == 204
        )
    else:
        institution.is_active = False
        expected = 404
    db.commit()
    result = client.post(
        f"{STAFF}/invitations/accept",
        headers=auth_headers(student),
        json={"token": mailer.token("staff_invitation")},
    )
    assert result.status_code == expected
    assert db.scalar(select(InstitutionMembership)) is None


def test_membership_revocation_takes_effect_with_existing_token(
    client, db, user_factory, auth_headers, institutions
):
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    student = user_factory()
    member = grant(db, student, institutions[0])
    headers = auth_headers(student)
    url = f"{STAFF}/institutions/{institutions[0].id}/members"
    assert client.get(url, headers=headers).status_code == 200
    assert (
        client.delete(f"{url}/{member.id}", headers=auth_headers(admin)).status_code
        == 204
    )
    assert client.get(url, headers=headers).status_code == 403
    assert client.get(f"{STAFF}/memberships", headers=headers).json() == []


def test_manager_cannot_revoke_other_institution_member_or_manager(
    client, db, user_factory, auth_headers, institutions
):
    manager = user_factory("manager@example.com")
    own_member = grant(db, manager, institutions[0], MembershipRole.MANAGER)
    target = user_factory()
    foreign_member = grant(db, target, institutions[1])
    headers = auth_headers(manager)
    own_url = f"{STAFF}/institutions/{institutions[0].id}/members"
    assert (
        client.delete(f"{own_url}/{foreign_member.id}", headers=headers).status_code
        == 404
    )
    assert (
        client.delete(f"{own_url}/{own_member.id}", headers=headers).status_code == 403
    )
    assert (
        client.delete(
            f"{STAFF}/institutions/{institutions[1].id}/members/{foreign_member.id}",
            headers=headers,
        ).status_code
        == 403
    )


def test_revoked_inviter_cannot_grant_access(
    client, db, user_factory, auth_headers, institutions, mailer
):
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    manager = user_factory("manager@example.com")
    student = user_factory()
    member = grant(db, manager, institutions[0], MembershipRole.MANAGER)
    url = f"{STAFF}/institutions/{institutions[0].id}"
    assert (
        client.post(
            f"{url}/invitations",
            headers=auth_headers(manager),
            json={"email": student.email},
        ).status_code
        == 201
    )
    raw = mailer.token("staff_invitation")
    assert (
        client.delete(
            f"{url}/members/{member.id}", headers=auth_headers(admin)
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"{STAFF}/invitations/accept",
            headers=auth_headers(student),
            json={"token": raw},
        ).status_code
        == 400
    )


def test_inviter_authority_is_rechecked_at_acceptance(
    client, db, user_factory, auth_headers, institutions, mailer
):
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    student = user_factory()
    assert (
        client.post(
            f"{STAFF}/institutions/{institutions[0].id}/invitations",
            headers=auth_headers(admin),
            json={"email": student.email},
        ).status_code
        == 201
    )
    admin.role = UserRole.STUDENT
    db.commit()
    response = client.post(
        f"{STAFF}/invitations/accept",
        headers=auth_headers(student),
        json={"token": mailer.token("staff_invitation")},
    )
    assert response.status_code == 403


def test_old_invitation_cannot_restore_revoked_membership(
    client, db, user_factory, auth_headers, institutions, mailer
):
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    student = user_factory()
    url = f"{STAFF}/institutions/{institutions[0].id}"
    for _ in range(2):
        assert (
            client.post(
                f"{url}/invitations",
                headers=auth_headers(admin),
                json={"email": student.email},
            ).status_code
            == 201
        )
    first, second = [message["token"] for message in mailer.messages]
    response = client.post(
        f"{STAFF}/invitations/accept",
        headers=auth_headers(student),
        json={"token": first},
    )
    assert response.status_code == 200
    assert (
        client.delete(
            f"{url}/members/{response.json()['id']}", headers=auth_headers(admin)
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"{STAFF}/invitations/accept",
            headers=auth_headers(student),
            json={"token": second},
        ).status_code
        == 400
    )
    # A new authorized invitation can explicitly restore access.
    assert (
        client.post(
            f"{url}/invitations",
            headers=auth_headers(admin),
            json={"email": student.email},
        ).status_code
        == 201
    )
    response = client.post(
        f"{STAFF}/invitations/accept",
        headers=auth_headers(student),
        json={"token": mailer.token("staff_invitation")},
    )
    assert response.status_code == 200 and response.json()["is_active"] is True
