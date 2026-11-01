import json
from decimal import Decimal
from pathlib import Path
import unittest

from app.bank_parser import parse_bank_statement
from app.classifier import DeterministicClassifier, classify_transactions
from app.valuation import calculate_close, load_q3_data


class NavTests(unittest.TestCase):
    def test_calculates_the_supplied_q4_close_and_balancing_bridge(self) -> None:
        root = Path(__file__).parents[2] / "pack"
        opening_cash, extracted = parse_bank_statement(root / "GPF_Bank_Statement_Q4_2022.pdf")
        transactions = classify_transactions(extracted, DeterministicClassifier())
        q3 = load_q3_data(root / "GPF_NAV_Summary_Q3_2022.xlsx", root / "CVP_Valuation_Report_Q3_2022.xlsx")
        marks = json.loads((root / "agent_b_data.json").read_text())["marks"]
        result = calculate_close(q3, transactions, marks, eurusd_rate="1.0666", threshold_pct="20")
        self.assertEqual(opening_cash, Decimal("12450000.00"))
        self.assertEqual(result.nav["nav_q4"], Decimal("169764039.25"))
        self.assertEqual(result.nav["bridge"]["difference"], Decimal("0.00"))
        ionmere = next(row for row in result.review if row["company"] == "Ionmere Materials Ltd")
        self.assertEqual(ionmere["flags"], ["R3"])
        self.assertEqual(len(result.holdings), 8)


if __name__ == "__main__":
    unittest.main()
