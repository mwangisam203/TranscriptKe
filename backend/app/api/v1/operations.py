from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.fulfillment import RegistrarEvent
from app.models.operations import OperationsCase
from app.models.user import User
from app.schemas.academic import Id
from app.schemas.operations import FollowUpInput, QueueKind
from app.services.academic import check_version
from app.services.fulfillment import record
from app.services.operations import queue, summary
from app.services.order_timing import as_utc
from app.services.orders import get_order, utcnow

router = APIRouter(
    prefix="/staff/institutions/{institution_id}/operations", tags=["pilot operations"]
)


@router.get("/summary")
def operations_summary(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id, manage=True)
    return summary(db, institution_id)


@router.get("/queue")
def operations_queue(
    institution_id: Id,
    kind: QueueKind = "overdue",
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id, manage=True)
    return queue(db, institution_id, kind, offset, limit)


def case_view(db, order):
    case = db.get(OperationsCase, order.id)
    return {
        "order_id": order.id,
        "reference": order.reference,
        "version": order.version,
        "note": case.note if case else "",
        "follow_up_at": as_utc(case.follow_up_at) if case else None,
        "updated_at": as_utc(case.updated_at) if case else None,
        "history": [
            {
                "actor_id": row.actor_id,
                "note": row.internal_note,
                "created_at": as_utc(row.created_at),
            }
            for row in db.scalars(
                select(RegistrarEvent)
                .where(
                    RegistrarEvent.order_id == order.id,
                    RegistrarEvent.action == "operations_reviewed",
                )
                .order_by(RegistrarEvent.id.desc())
                .limit(50)
            )
        ],
    }


@router.get("/orders/{order_id}")
def operations_case(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id, manage=True)
    return case_view(db, get_order(db, user, order_id, institution_id=institution_id))


@router.put("/orders/{order_id}")
def update_case(
    institution_id: Id,
    order_id: Id,
    payload: FollowUpInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    require_academic_staff(db, user, institution_id, manage=True)
    check_version(order.version, payload.expected_version)
    if order.user_id == user.id:
        raise HTTPException(403, "Another manager must review your order")
    case = db.get(OperationsCase, order.id)
    if case is None:
        case = OperationsCase(order_id=order.id)
        db.add(case)
    case.note, case.follow_up_at = payload.note, as_utc(payload.follow_up_at)
    case.updated_by, case.updated_at = user.id, utcnow()
    follow_up = payload.follow_up_at.isoformat() if payload.follow_up_at else "none"
    record(
        db,
        order,
        user,
        "operations_reviewed",
        "The institution reviewed your order's progress.",
        f"{payload.note}\nFollow-up: {follow_up}",
    )
    db.commit()
    return case_view(db, order)
