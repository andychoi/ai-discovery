from .services import OrderService

class CheckoutHandler:
    def __init__(self, order_service: OrderService):
        self.order_service = order_service

    def run(self):
        self.order_service.process()
