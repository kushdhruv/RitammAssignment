import unittest
from decimal import Decimal

from app.classifier import DeterministicClassifier, classify_transactions
from app.models import Category, Transaction


class ClassificationTests(unittest.TestCase):
    def test_classifies_and_extracts_a_purchase(self) -> None:
        row = Transaction(
            transaction_id="txn-0001",
            date="2022-12-15",
            description="OUTWARD WIRE IONMERE MATERIALS LTD SUBSCRIPTION SERIES B SHARES",
            reference="OW-221215-01",
            debit=Decimal("14000000"),
            credit=Decimal("0"),
            balance=Decimal("1"),
        )
        classified = classify_transactions([row], DeterministicClassifier())[0]
        self.assertEqual(classified.category, Category.INVESTMENT_PURCHASE)
        self.assertEqual(classified.company, "Ionmere Materials Ltd")

    def test_rejects_a_category_with_invalid_cash_direction(self) -> None:
        row = Transaction(
            transaction_id="txn-0001", date="2022-10-01", description="wire", reference=None,
            debit=Decimal("10"), credit=Decimal("0"), balance=Decimal("90"),
        )

        class InvalidClassifier:
            def classify(self, _rows):
                return [{"transaction_id": "txn-0001", "category": "capital_call", "lp_code": "LP01", "company": None}]

        with self.assertRaisesRegex(ValueError, "CASH_DIRECTION"):
            classify_transactions([row], InvalidClassifier())


if __name__ == "__main__":
    unittest.main()
