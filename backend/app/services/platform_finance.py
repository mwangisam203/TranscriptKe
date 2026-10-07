from fastapi import HTTPException
from sqlalchemy import select

from app.models.institution import Institution
from app.models.orders import Order
from app.models.user import UserRole


def require_finance_admin(user):
    if not user.is_email_verified or user.role != UserRole.ADMIN:
        raise HTTPException(
            403, "Only TranscriptsKE administrators can manage platform payments"
        )


def finance_order(db, user, order_id, *, lock=False):
    require_finance_admin(user)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    if lock:
        db.scalar(
            select(Institution)
            .where(Institution.id == order.institution_id)
            .with_for_update()
        )
        order = db.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return order
