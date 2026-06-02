"""HTTP entry points (FastAPI-style) — the scenario roots for PF generation."""

from fastapi import APIRouter, HTTPException

from .domain.order import Customer, Order, OrderItem
from .services.checkout_service import CheckoutError, CheckoutService

router = APIRouter()


def _build_service():
    # Wiring omitted for brevity; a DI container would supply these.
    raise NotImplementedError


@router.post("/orders/{order_id}/checkout")
def checkout_order(order_id: str, payload: dict):
    """Validate, price, reserve, charge, and persist an order."""
    customer = Customer(payload["customer_id"], payload.get("tier", "standard"))
    items = [
        OrderItem(line["sku"], line["qty"], line["price"])
        for line in payload["items"]
    ]
    order = Order(order_id, customer, items)

    service = _build_service()
    try:
        result = service.checkout(order)
    except CheckoutError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "order_id": result.order_id,
        "status": result.status,
        "total": result.total,
    }


@router.get("/orders/{order_id}")
def get_order(order_id: str):
    """Fetch a single order's current state."""
    service = _build_service()
    order = service.repo.load(order_id) if hasattr(service.repo, "load") else None
    if order is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"order_id": order.order_id, "status": order.status}
