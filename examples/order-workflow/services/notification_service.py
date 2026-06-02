"""Customer / ops notifications (external boundary)."""

import requests


class NotificationService:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def notify_risk(self, order):
        requests.post(
            self.webhook_url,
            json={"event": "order.flagged", "order_id": order.order_id},
            timeout=5,
        )

    def notify_failure(self, order):
        requests.post(
            self.webhook_url,
            json={"event": "order.payment_failed", "order_id": order.order_id},
            timeout=5,
        )
