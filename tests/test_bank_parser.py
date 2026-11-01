import unittest
from decimal import Decimal
from pathlib import Path

from app.bank_parser import parse_bank_statement


class BankParserTests(unittest.TestCase):
    def test_extracts_every_statement_transaction_and_balances(self) -> None:
        statement = Path(__file__).parents[2] / "pack" / "GPF_Bank_Statement_Q4_2022.pdf"
        opening_cash, transactions = parse_bank_statement(statement)
        self.assertEqual(opening_cash, Decimal("12450000.00"))
        self.assertEqual(len(transactions), 39)
        self.assertEqual(transactions[-1].balance, Decimal("23896579.25"))
        self.assertIn("ADVISORS LLC REF MGMT FEE Q4-22 PER LPA 8.1", transactions[0].description)


if __name__ == "__main__":
    unittest.main()
