"""Stock reservation — loops over line items, guards item state transitions."""


class OutOfStock(Exception):
    pass


class InventoryService:
    def __init__(self, repo):
        self.repo = repo

    def reserve_all(self, order):
        """Reserve every line item; raises if any item is short on stock."""
        for item in order.items:
            self._reserve_item(item)

    def _reserve_item(self, item):
        available = self.repo.stock_for(item.sku)
        if available < item.qty:
            item.status = "BACKORDERED"
            raise OutOfStock(item.sku)
        else:
            item.status = "RESERVED"
        self.repo.decrement(item.sku, item.qty)
