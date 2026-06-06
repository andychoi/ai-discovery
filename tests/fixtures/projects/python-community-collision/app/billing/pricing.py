"""Billing price calculator — one side of the cross-module `calculate` collision."""


class PriceCalc:
    @staticmethod
    def base_rate():
        return 100

    def calculate(self):
        return 110
