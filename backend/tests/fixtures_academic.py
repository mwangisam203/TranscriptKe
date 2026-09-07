import pytest

from app.models.academic import InstitutionService, OrderingPolicy
from app.models.access import InstitutionMembership, MembershipRole

SERVICE = {
    "code": "TRANSCRIPT",
    "name": "Official transcript",
    "document_type": "official_transcript",
    "description": "Test catalog",
    "fee_minor": 150050,
    "currency": "KES",
    "processing_days_min": 2,
    "processing_days_max": 7,
    "delivery_methods": ["secure_electronic"],
    "required_fields": ["program"],
    "is_active": True,
}
POLICY = {
    "expected_version": 0,
    "accepting_requests": True,
    "student_instructions": "Provide the name on your academic record.",
    "required_fields": ["attendance_start_year"],
}


def grant(db, user, institution, role=MembershipRole.STAFF):
    member = InstitutionMembership(
        user_id=user.id, institution_id=institution.id, role=role
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@pytest.fixture
def catalog(db, institutions, user_factory):
    manager = user_factory("manager@example.com")
    staff = user_factory("staff@example.com")
    grant(db, manager, institutions[0], MembershipRole.MANAGER)
    grant(db, staff, institutions[0])
    service = InstitutionService(institution_id=institutions[0].id, **SERVICE)
    policy = OrderingPolicy(
        institution_id=institutions[0].id,
        **{key: value for key, value in POLICY.items() if key != "expected_version"},
    )
    db.add_all([service, policy])
    db.commit()
    db.refresh(service)
    return {
        "manager": manager,
        "staff": staff,
        "institution": institutions[0],
        "service": service,
        "policy": policy,
    }
