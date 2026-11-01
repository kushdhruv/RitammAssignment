"""Unique enterprise-grade edge case tests for PE fund close automation."""

from datetime import date
from decimal import Decimal
import unittest

from app.classifier import (
    ClassificationContext,
    classify_transactions,
)
from app.models import Category, Mark, Transaction
from app.valuation import Q3Data, Q3Valuation, calculate_close


def _make_txn(
    txn_id: str,
    desc: str,
    debit: str = "0.00",
    credit: str = "0.00",
    balance: str = "2001000.00",
    date_str: str = "2022-10-15",
) -> Transaction:
    return Transaction(
        transaction_id=txn_id,
        date=date_str,
        description=desc,
        reference="REF-001",
        debit=Decimal(debit),
        credit=Decimal(credit),
        balance=Decimal(balance),
    )


class AdvancedEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ClassificationContext(
            lp_directory={
                "LAKESHORE TEACHERS": "LP01",
                "FJELLHEIM": "LP02",
                "R. MEHTA FAMILY TRUST": "LP10",
                "R MEHTA FAM TRST": "LP10",
            },
            known_companies=(
                "Aurora Logistics Pvt Ltd",
                "Fjord Payments AS",
                "Ionmere Materials Ltd",
            ),
            prior_period_liability_payees=(
                "Coral & Finch LLP",
                "Harbourside Fund Services",
            ),
            prior_period_liability_amounts={
                "Coral & Finch LLP": Decimal("120000.00"),
                "Harbourside Fund Services": Decimal("65000.00"),
            },
            prior_period_liability_descriptions={
                "Coral & Finch LLP": "Audit fee accrual",
                "Harbourside Fund Services": "Administration fee payable Q3",
            },
            prior_period_end_date=date(2022, 9, 30),
        )

    def test_canonicalizes_acquirer_prefixed_sale_wire(self) -> None:
        """When an LLM returns a full buyer-prefixed string for a sale wire,
        canonicalize_company must isolate the portfolio company being sold.
        """
        row = _make_txn(
            "txn-sale",
            "INWARD WIRE NORDIC ACQUISITIONS AS SPA FJORD PAYMENTS AS COMPLETION",
            debit="0.00",
            credit="29000000.00",
        )

        class AcquirerSaleMockClassifier:
            def classify(self, rows):
                return [{
                    "transaction_id": r.transaction_id,
                    "category": "sale_proceeds",
                    "lp_code": None,
                    "company": "Nordic Acquisitions AS SPA Fjord Payments AS",
                } for r in rows]

        classified = classify_transactions([row], AcquirerSaleMockClassifier(), self.context)
        self.assertEqual(classified[0].company, "Fjord Payments AS")

    def test_future_period_expense_is_not_accrual_settlement(self) -> None:
        """A wire paid in Q4 that covers 2023 D&O premium must be categorized as a Q4 expense,
        NOT an accrual settlement, even if it covers a future annual period.
        """
        row = _make_txn(
            "txn-do-prem",
            "OUTWARD WIRE NORTHBAY SPECIALTY INSURANCE D&O PREMIUM 2023",
            debit="42000.00",
            credit="0.00",
        )

        class ExpenseMockClassifier:
            def classify(self, rows):
                return [{
                    "transaction_id": r.transaction_id,
                    "category": "expense",
                    "lp_code": None,
                    "company": None,
                } for r in rows]

        classified = classify_transactions([row], ExpenseMockClassifier(), self.context)
        self.assertEqual(classified[0].category, Category.EXPENSE)
        self.assertIsNone(classified[0].classification_override_reason)

    def test_expense_refund_preserves_credit_cash_direction_and_nets_in_nav_bridge(self) -> None:
        """A vendor refund for overbilling (credit) must be categorized as expense_refund
        and must mathematically net against fund expenses in the NAV bridge.
        """
        row = _make_txn(
            "txn-refund",
            "MISC CREDIT HALDANE & ROSS LLP REFUND OVERBILLING INV HR-4471",
            debit="0.00",
            credit="3500.00",
        )

        class RefundMockClassifier:
            def classify(self, rows):
                return [{
                    "transaction_id": r.transaction_id,
                    "category": "expense_refund",
                    "lp_code": None,
                    "company": None,
                } for r in rows]

        classified = classify_transactions([row], RefundMockClassifier(), self.context)
        self.assertEqual(classified[0].category, Category.EXPENSE_REFUND)
        self.assertEqual(classified[0].credit, Decimal("3500.00"))

    def test_r2_method_change_detected_even_if_fair_value_is_identical(self) -> None:
        """If a valuer changes the valuation method from DCF to Market Multiples,
        Rule R2 must trigger even if the fair value amount did not move at all.
        """
        # Reconciled Balance Sheet: Cash $2,000,000 + Investment $15,000,000 = NAV $17,000,000
        q3 = Q3Data(
            nav_q3=Decimal("17000000.00"),
            q3_liabilities=Decimal("0.00"),
            q4_liabilities=Decimal("0.00"),
            costs_usd={"Static Co": Decimal("10000000.00")},
            valuations={
                "Static Co": Q3Valuation(
                    company="Static Co",
                    currency="USD",
                    fair_value_local=Decimal("15000000.00"),
                    fair_value_usd=Decimal("15000000.00"),
                    valuation_method="Discounted cash flow",
                )
            },
        )
        # 1 interest transaction of $1,000 -> closing cash becomes $2,001,000.00
        txns = [
            _make_txn(
                "txn-001",
                "CREDIT INTEREST",
                debit="0.00",
                credit="1000.00",
                balance="2001000.00",
            )
        ]
        txns[0] = txns[0].model_copy(update={"category": Category.INTEREST_INCOME})

        q4_marks = [
            Mark(
                company="Static Co",
                currency="USD",
                fair_value=Decimal("15000000.00"),
                valuation_method="Market multiples",
                valuation_date=date(2022, 12, 31),
            )
        ]

        result = calculate_close(q3, txns, q4_marks, "1.0000", "20.0")
        review = next(r for r in result.review if r["company"] == "Static Co")
        self.assertEqual(review["change_pct"], Decimal("0.00"))
        self.assertIn("R2", review["flags"])
        self.assertIn("Valuation method changed", review["reason"])
        self.assertEqual(result.nav["bridge"]["difference"], Decimal("0.00"))

    def test_foreign_currency_materiality_isolates_fx_movement(self) -> None:
        """Rule R1 must evaluate % change in local currency (€), not after USD conversion.
        If EUR value moves -2.96% but USD value moves +6.17% due to EUR appreciation,
        R1 must NOT flag because the operating asset did not change materially.
        """
        # Q3: Cash $1,000,000 + Investment $13,159,800 = NAV $14,159,800.00
        q3 = Q3Data(
            nav_q3=Decimal("14159800.00"),
            q3_liabilities=Decimal("0.00"),
            q4_liabilities=Decimal("0.00"),
            costs_usd={"Ember Retail GmbH": Decimal("14184000.00")},
            valuations={
                "Ember Retail GmbH": Q3Valuation(
                    company="Ember Retail GmbH",
                    currency="EUR",
                    fair_value_local=Decimal("13500000.00"),
                    fair_value_usd=Decimal("13159800.00"),
                    valuation_method="Market multiples",
                )
            },
        )
        # Interest of $500 -> closing cash $1,000,500.00
        txns = [
            _make_txn(
                "txn-001",
                "CREDIT INTEREST",
                credit="500.00",
                balance="1000500.00",
            )
        ]
        txns[0] = txns[0].model_copy(update={"category": Category.INTEREST_INCOME})

        # Q4 mark in EUR is 13.1M (-2.96% change).
        # At 1.0666, USD fair value is $13,972,460.00.
        # Q4 NAV = $1,000,500 + $13,972,460 = $14,972,960.00
        # Q3 Unrealized = 13,159,800 - 14,184,000 = -1,024,200
        # Q4 Unrealized = 13,972,460 - 14,184,000 = -211,540
        # Change in Unrealized = -211,540 - (-1,024,200) = +812,660.00
        # Bridge = 14,159,800 + 500 (income) + 812,660 = 14,972,960.00 -> Diff = 0.00!
        q4_marks = [
            Mark(
                company="Ember Retail GmbH",
                currency="EUR",
                fair_value=Decimal("13100000.00"),
                valuation_method="Market multiples",
                valuation_date=date(2022, 12, 31),
            )
        ]

        result = calculate_close(q3, txns, q4_marks, eurusd_rate="1.0666", threshold_pct="20.0")
        ember_review = next(r for r in result.review if r["company"] == "Ember Retail GmbH")

        self.assertEqual(ember_review["change_pct"], Decimal("-2.96"))
        self.assertEqual(ember_review["flags"], [])
        self.assertEqual(result.nav["bridge"]["difference"], Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
