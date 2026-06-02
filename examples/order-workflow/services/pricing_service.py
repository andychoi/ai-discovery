"""Order pricing — loop to sum line items, conditional tier discount."""


class PricingService:
    PREMIUM_DISCOUNT = 0.10
    BULK_THRESHOLD = 10
    BULK_DISCOUNT = 0.05

    def compute_total(self, order):
        subtotal = 0.0
        units = 0
        for item in order.items:
            subtotal += item.price * item.qty
            units += item.qty

        discount = 0.0
        if order.customer.tier == "premium":
            discount = subtotal * self.PREMIUM_DISCOUNT
        elif units >= self.BULK_THRESHOLD:
            discount = subtotal * self.BULK_DISCOUNT

        return round(subtotal - discount, 2)
