"""Checkout orchestration — the control-flow-rich entry into the domain.

This is the method whose process flow should roll up into a LEVELED spec:
  validate → reserve (loop) → price → risk gate (if/else) → persist →
  charge (external) → payment gate (if/else) → persist.
"""

from .inventory_service import OutOfStock


class CheckoutError(Exception):
    pass


class CheckoutService:
    RISK_THRESHOLD = 1000.0

    def __init__(self, repo, inventory, pricing, payment, notifier):
        self.repo = repo
        self.inventory = inventory
        self.pricing = pricing
        self.payment = payment
        self.notifier = notifier

    def checkout(self, order):
        self._validate(order)

        # Reserve stock for every line item (loop + per-item state transitions).
        try:
            self.inventory.reserve_all(order)
        except OutOfStock as exc:
            order.status = "DRAFT"
            raise CheckoutError(f"out of stock: {exc}")

        # Price the order.
        order.total = self.pricing.compute_total(order)

        # Risk gate: large orders are flagged for manual review.
        if order.total > self.RISK_THRESHOLD:
            order.status = "FLAGGED"
            self.notifier.notify_risk(order)
        else:
            order.status = "APPROVED"

        self.repo.save(order)

        # Charge the customer through the external payment provider.
        result = self.payment.charge(order)
        if result.ok:
            order.status = "PAID"
        else:
            order.status = "PAYMENT_FAILED"
            self.notifier.notify_failure(order)

        self.repo.save(order)
        return order

    def _validate(self, order):
        if not order.items:
            raise CheckoutError("empty order")
        if order.customer is None:
            raise CheckoutError("missing customer")
