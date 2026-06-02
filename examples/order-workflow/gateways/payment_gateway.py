"""External payment provider boundary (HTTP)."""

import requests


class PaymentResult:
    def __init__(self, ok, reference):
        self.ok = ok
        self.reference = reference


class PaymentGateway:
    def __init__(self, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key

    def charge(self, order):
        """Charge the order total against the external payment API."""
        response = requests.post(
            f"{self.base_url}/charges",
            json={"order_id": order.order_id, "amount": order.total},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=10,
        )
        body = response.json()
        if response.status_code == 200 and body.get("captured"):
            return PaymentResult(True, body["reference"])
        return PaymentResult(False, body.get("reference", ""))
