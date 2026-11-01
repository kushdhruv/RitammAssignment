import unittest
from decimal import Decimal

from app.money import money, sum_money


class MoneyTests(unittest.TestCase):
    def test_money_preserves_cents_without_binary_float_rounding(self) -> None:
        self.assertEqual(money("0.10") + money("0.20"), Decimal("0.30"))

    def test_sum_money_returns_two_decimal_places(self) -> None:
        self.assertEqual(sum_money(["12.1", "0.02"]), Decimal("12.12"))


if __name__ == "__main__":
    unittest.main()
