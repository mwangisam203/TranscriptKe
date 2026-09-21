from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.fulfillment import OrderHold
from app.models.orders import OrderItem
from app.models.user import User
from app.schemas.academic import Id
from app.schemas.fulfillment import (
    AssignmentInput,
    HoldInput,
    ReleaseConfirmation,
    ResolutionInput,
    ReviewInput,
)
from app.services.fulfillment import (
    actionable,
    assign,
    assigned_case,
    preparation_blockers,
    record,
    summary,
)
from app.services.orders import get_order, rows, utcnow

router = APIRouter(tags=["registrar"])
PREFIX = "/staff/institutions/{institution_id}/orders/{order_id}/fulfillment"


@router.get("/orders/{order_id}/fulfillment")
def student_progress(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return summary(db, get_order(db, user, order_id))


@router.get(PREFIX)
def registrar_progress(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return summary(
        db, get_order(db, user, order_id, institution_id=institution_id), staff=True
    )


@router.put(PREFIX + "/assignment")
def assignment(
    institution_id: Id,
    order_id: Id,
    payload: AssignmentInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = actionable(db, user, institution_id, order_id, payload.expected_version)
    assign(db, user, order, payload.user_id)
    db.commit()
    return summary(db, order, staff=True)


@router.post(PREFIX + "/holds", status_code=201)
def create_hold(
    institution_id: Id,
    order_id: Id,
    payload: HoldInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = actionable(db, user, institution_id, order_id, payload.expected_version)
    assigned_case(db, user, order)
    db.add(
        OrderHold(
            order_id=order.id,
            category=payload.category,
            student_message=payload.student_message,
            internal_note=payload.internal_note,
            created_by=user.id,
        )
    )
    record(
        db, order, user, "hold_added", payload.student_message, payload.internal_note
    )
    db.commit()
    return summary(db, order, staff=True)


@router.post(PREFIX + "/holds/{hold_id}/resolution")
def resolve_hold(
    institution_id: Id,
    order_id: Id,
    hold_id: Id,
    payload: ResolutionInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = actionable(db, user, institution_id, order_id, payload.expected_version)
    assigned_case(db, user, order)
    hold = next((h for h in rows(db, OrderHold, order.id) if h.id == hold_id), None)
    if hold is None:
        raise HTTPException(404, "Hold not found")
    if hold.resolved_at:
        raise HTTPException(409, "Hold already resolved")
    hold.resolved_at = utcnow()
    hold.resolved_by = user.id
    hold.resolution = payload.student_message
    record(
        db, order, user, "hold_resolved", payload.student_message, payload.internal_note
    )
    db.commit()
    return summary(db, order, staff=True)


@router.post(PREFIX + "/release-confirmation")
def release_confirmation(
    institution_id: Id,
    order_id: Id,
    payload: ReleaseConfirmation,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = actionable(db, user, institution_id, order_id, payload.expected_version)
    case = assigned_case(db, user, order)
    if order.release_when == "now":
        raise HTTPException(409, "This order has no deferred release event")
    case.release_confirmed_at = utcnow() if payload.confirmed else None
    case.release_confirmed_by = user.id if payload.confirmed else None
    case.release_evidence = payload.internal_note
    if not payload.confirmed:
        for item in rows(db, OrderItem, order.id):
            if item.fulfillment_status == "ready":
                item.fulfillment_status = "processing"
    record(
        db,
        order,
        user,
        "release_condition_updated",
        "The requested release event has been confirmed."
        if payload.confirmed
        else "The requested release event requires confirmation again.",
        payload.internal_note,
    )
    db.commit()
    return summary(db, order, staff=True)


@router.post(PREFIX + "/items/{key}/decisions")
def item_decision(
    institution_id: Id,
    order_id: Id,
    key: str,
    payload: ReviewInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = actionable(db, user, institution_id, order_id, payload.expected_version)
    assigned_case(db, user, order)
    item = next((i for i in rows(db, OrderItem, order.id) if i.key == key), None)
    if item is None:
        raise HTTPException(404, "Document item not found")
    transitions = {
        "approve": ("awaiting_review", "processing"),
        "reject": ("awaiting_review", "rejected"),
        "ready": ("processing", "ready"),
    }
    if payload.decision == "reopen":
        require_academic_staff(db, user, institution_id, manage=True)
        if item.fulfillment_status not in ("processing", "ready", "rejected"):
            raise HTTPException(409, "This item cannot be reopened")
        next_state = "awaiting_review"
    else:
        previous, next_state = transitions[payload.decision]
        if item.fulfillment_status != previous:
            raise HTTPException(409, "Invalid document state transition")
        if payload.decision in ("approve", "ready"):
            blockers = preparation_blockers(
                db, order, include_deferred=payload.decision == "ready"
            )
            if blockers:
                raise HTTPException(
                    409, {"message": "Resolve registrar blockers", "fields": blockers}
                )
    item.fulfillment_status = next_state
    record(
        db,
        order,
        user,
        "document_" + next_state,
        f"Document {key}: {payload.student_message}",
        payload.internal_note,
    )
    db.commit()
    return summary(db, order, staff=True)


@router.get("/staff/institutions/{institution_id}/registrar-queue")
def registrar_queue(
    institution_id: Id,
    state: str | None = None,
    assigned_to: Id | None = None,
    unassigned: bool = False,
    on_hold: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    from sqlalchemy import select

    from app.models.fulfillment import RegistrarCase
    from app.models.orders import Order
    from app.services.orders import read_order

    require_academic_staff(db, user, institution_id)
    if state not in (
        None,
        "awaiting_review",
        "processing",
        "ready",
        "rejected",
        "cancelled",
    ):
        raise HTTPException(422, "Unknown document state")
    if unassigned and assigned_to is not None:
        raise HTTPException(422, "Choose an assignee or unassigned, not both")
    statement = (
        select(Order)
        .outerjoin(RegistrarCase, RegistrarCase.order_id == Order.id)
        .where(Order.institution_id == institution_id, Order.submitted_at.is_not(None))
    )
    if assigned_to is not None:
        statement = statement.where(RegistrarCase.assigned_to == assigned_to)
    if unassigned:
        statement = statement.where(RegistrarCase.assigned_to.is_(None))
    if state:
        statement = statement.where(
            select(OrderItem.id)
            .where(
                OrderItem.order_id == Order.id, OrderItem.fulfillment_status == state
            )
            .exists()
        )
    if on_hold:
        statement = statement.where(
            select(OrderHold.id)
            .where(OrderHold.order_id == Order.id, OrderHold.resolved_at.is_(None))
            .exists()
        )
    return [
        read_order(db, order)
        for order in db.scalars(
            statement.order_by(Order.submitted_at, Order.id).offset(offset).limit(limit)
        )
    ]


@router.get("/staff/institutions/{institution_id}/registrars")
def registrars(
    institution_id: Id,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    from sqlalchemy import select

    from app.models.access import InstitutionMembership

    require_academic_staff(db, user, institution_id)
    statement = (
        select(User, InstitutionMembership.role)
        .join(InstitutionMembership, InstitutionMembership.user_id == User.id)
        .where(
            InstitutionMembership.institution_id == institution_id,
            InstitutionMembership.is_active.is_(True),
            User.is_email_verified.is_(True),
        )
        .order_by(User.full_name, User.id)
        .offset(offset)
        .limit(limit)
    )
    return [
        {"id": person.id, "name": person.full_name, "role": role}
        for person, role in db.execute(statement)
    ]
