"""Shipping rate calculator — the colliding `calculate` nobody in web/ calls.

No file in this fixture imports or calls into shipping, so its files form a
separate call-graph community from {handler, invoice, pricing}.
"""


class RateCalc:
    def calculate(self):
        return 5
