"""Checkout view — calls into billing, never into shipping."""

from app.billing.invoice import Invoice


class CheckoutView:
    def __init__(self, strategy):
        # Deliberately untyped: receiver-type resolution (Stage 3) cannot pin
        # self.strategy.calculate() — this is the dynamic-dispatch case the
        # community-narrowing fixture gauges.
        self.strategy = strategy

    def total(self):
        Invoice.build()
        return self.strategy.calculate()
