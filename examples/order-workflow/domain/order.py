"""Domain entities for the order-checkout workflow."""


class Customer:
    def __init__(self, customer_id, tier):
        self.customer_id = customer_id
        self.tier = tier  # "standard" | "premium"


class OrderItem:
    def __init__(self, sku, qty, price):
        self.sku = sku
        self.qty = qty
        self.price = price
        self.status = "PENDING"  # PENDING | RESERVED | BACKORDERED


class Order:
    def __init__(self, order_id, customer, items):
        self.order_id = order_id
        self.customer = customer
        self.items = items  # list[OrderItem]
        self.total = 0.0
        self.status = "DRAFT"  # DRAFT | APPROVED | FLAGGED | PAID | PAYMENT_FAILED
