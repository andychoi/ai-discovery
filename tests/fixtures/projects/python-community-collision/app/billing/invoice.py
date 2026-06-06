"""Billing invoice — internally bound to billing.pricing."""

from app.billing.pricing import PriceCalc


class Invoice:
    @staticmethod
    def build():
        PriceCalc.base_rate()
