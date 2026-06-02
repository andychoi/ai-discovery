"""Persistence boundary (DB operations) for orders and stock."""


class OrderRepository:
    def __init__(self, session):
        self.session = session

    def save(self, order):
        self.session.add(order)
        self.session.commit()
        return order

    def stock_for(self, sku):
        row = self.session.execute(
            "SELECT qty FROM stock WHERE sku = :sku", {"sku": sku}
        ).first()
        return row.qty if row else 0

    def decrement(self, sku, qty):
        self.session.execute(
            "UPDATE stock SET qty = qty - :qty WHERE sku = :sku",
            {"qty": qty, "sku": sku},
        )
        self.session.commit()
