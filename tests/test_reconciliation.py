import unittest
from decimal import Decimal

from app.models import Transaction
from app.reconciliation import reconcile_bank_statement


class ReconciliationTests(unittest.TestCase):
    def test_reconciles_a_valid_running_balance(self) -> None:
        transactions = [
            Transaction(date="2022-10-01", description="credit", reference="a", debit="0", credit="10", balance="110"),
            Transaction(date="2022-10-02", description="debit", reference="b", debit="2", credit="0", balance="108"),
        ]
        result = reconcile_bank_statement("100", transactions)
        self.assertEqual(result.closing_cash, Decimal("108.00"))

    def test_identifies_a_one_cent_running_balance_error(self) -> None:
        transactions = [
            Transaction(date="2022-10-01", description="credit", reference="a", debit="0", credit="10", balance="109.99")
        ]
        with self.assertRaisesRegex(ValueError, "txn-0001.*0.01"):
            reconcile_bank_statement("100", transactions)


if __name__ == "__main__":
    unittest.main()
