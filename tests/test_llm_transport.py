"""Regression tests for the bounded, fail-closed LLM classification boundary."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.classifier import (
    ClassificationContext,
    DeterministicClassifier,
    OpenAICompatibleClassifier,
    classify_transactions,
    load_classification_context,
)
from app.bank_parser import parse_bank_statement
from app.models import Transaction


class _JsonResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _model_response(classifications: list[dict]) -> _JsonResponse:
    return _JsonResponse({"choices": [{"message": {"content": json.dumps({"classifications": classifications})}}]})


def _row(identifier: str, *, description: str = "WIRE IN R MEHTA FAM TRST CALL NO 7", debit: str = "0", credit: str = "10") -> Transaction:
    return Transaction(
        transaction_id=identifier,
        date=date(2022, 10, 1),
        description=description,
        reference="reference",
        debit=Decimal(debit),
        credit=Decimal(credit),
        balance=Decimal("100"),
    )


class LlmTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ClassificationContext(
            lp_directory={"R. Mehta Family Trust": "LP10"},
            known_companies=("Fjord Payments AS",),
            prior_period_liability_payees=("Coral & Finch LLP", "Harbourside Fund Services"),
            prior_period_liability_amounts={
                "Coral & Finch LLP": Decimal("120000.00"),
                "Harbourside Fund Services": Decimal("65000.00"),
            },
            prior_period_liability_descriptions={
                "Coral & Finch LLP": "Audit fee accrual",
                "Harbourside Fund Services": "Administration fee payable Q3",
            },
            prior_period_end_year=2022,
        )

    def test_identifies_the_client_to_provider_and_supplies_grounding_context(self) -> None:
        captured = []

        def fake_urlopen(request, **_kwargs):
            captured.append(request)
            return _model_response([{"transaction_id": "txn-0001", "category": "capital_call", "lp_code": "LP10", "company": None}])

        classifier = OpenAICompatibleClassifier("test-key", "https://example.invalid/v1", "test-model", context=self.context)
        with patch("app.classifier.urlopen", side_effect=fake_urlopen):
            self.assertEqual(len(classifier.classify([_row("txn-0001")])), 1)

        self.assertEqual(captured[0].get_header("User-agent"), "gpf-nav-close/1.0")
        self.assertEqual(captured[0].get_header("Content-type"), "application/json")
        self.assertEqual(captured[0].get_header("Accept"), "application/json")
        self.assertEqual(captured[0].get_header("Authorization"), "Bearer test-key")
        prompt = json.loads(captured[0].data.decode("utf-8"))["messages"][1]["content"]
        self.assertIn("R. Mehta Family Trust -> LP10", prompt)
        self.assertIn("Coral & Finch LLP", prompt)
        self.assertIn("Harbourside Fund Services", prompt)

    def test_splits_large_ledgers_into_bounded_requests(self) -> None:
        captured_batches: list[list[dict]] = []

        def fake_urlopen(request, **_kwargs):
            payload = json.loads(request.data.decode("utf-8"))
            ledger = json.loads(payload["messages"][1]["content"].rsplit("\nTransactions:\n", 1)[1])
            captured_batches.append(ledger)
            return _model_response([{"transaction_id": item["transaction_id"], "category": "capital_call", "lp_code": "LP10", "company": None} for item in ledger])

        rows = [_row(f"txn-{index:04d}") for index in range(39)]
        classifier = OpenAICompatibleClassifier("test-key", "https://example.invalid/v1", "test-model", context=self.context)
        with patch("app.classifier.urlopen", side_effect=fake_urlopen):
            responses = classifier.classify(rows)

        self.assertEqual(len(responses), 39)
        self.assertEqual([len(batch) for batch in captured_batches], [8, 8, 8, 8, 7])

    def test_retries_an_incomplete_batch_before_returning_any_result(self) -> None:
        attempts = 0
        row = _row("txn-0001")

        def fake_urlopen(_request, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return _model_response([])
            return _model_response([{"transaction_id": row.transaction_id, "category": "capital_call", "lp_code": "LP10", "company": None}])

        classifier = OpenAICompatibleClassifier("test-key", "https://example.invalid/v1", "test-model", context=self.context, attempts=2)
        with patch("app.classifier.urlopen", side_effect=fake_urlopen), patch("app.classifier.time.sleep"):
            classified = classify_transactions([row], classifier, self.context)

        self.assertEqual(attempts, 2)
        self.assertEqual(classified[0].lp_code, "LP10")


class LlmFinancialValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ClassificationContext(
            lp_directory={"R. Mehta Family Trust": "LP10"},
            known_companies=("Fjord Payments AS",),
            prior_period_liability_payees=("Coral & Finch LLP", "Harbourside Fund Services"),
            prior_period_liability_amounts={
                "Coral & Finch LLP": Decimal("120000.00"),
                "Harbourside Fund Services": Decimal("65000.00"),
            },
            prior_period_liability_descriptions={
                "Coral & Finch LLP": "Audit fee accrual",
                "Harbourside Fund Services": "Administration fee payable Q3",
            },
            prior_period_end_year=2022,
        )

    def test_rejects_an_unknown_lp_code_instead_of_accepting_a_plausible_hallucination(self) -> None:
        class HallucinatingClassifier:
            def classify(self, _rows):
                return [{"transaction_id": "txn-0001", "category": "capital_call", "lp_code": "LP999", "company": None}]

        with self.assertRaisesRegex(ValueError, "LLM_LP_CODE_UNRECOGNIZED"):
            classify_transactions([_row("txn-0001")], HallucinatingClassifier(), self.context)

    def test_rejects_a_company_that_is_not_evidenced_by_the_transaction_text(self) -> None:
        row = _row(
            "txn-0001",
            description="OUTWARD WIRE FJORD PAYMENTS AS SHARE PURCHASE",
            debit="100",
            credit="0",
        )

        class HallucinatingCompanyClassifier:
            def classify(self, _rows):
                return [{"transaction_id": row.transaction_id, "category": "investment_purchase", "lp_code": None, "company": "Imaginary Holdings Ltd"}]

        with self.assertRaisesRegex(ValueError, "LLM_COMPANY_UNGROUNDED"):
            classify_transactions([row], HallucinatingCompanyClassifier(), self.context)

    def test_overrides_a_known_prior_period_liability_payment_to_an_accrual_settlement(self) -> None:
        row = _row(
            "txn-0001",
            description="OUTWARD WIRE CORAL AND FINCH LLP INV CF/22/1187 AUDIT FY21 FINAL",
            debit="120000",
            credit="0",
        )

        class IncorrectExpenseClassifier:
            def classify(self, _rows):
                return [{"transaction_id": row.transaction_id, "category": "expense", "lp_code": None, "company": None}]

        result = classify_transactions([row], IncorrectExpenseClassifier(), self.context)
        self.assertEqual(result[0].category.value, "accrual_settlement")
        self.assertEqual(result[0].classification_override_reason, "prior_period_liability_settlement")

    def test_does_not_treat_a_larger_new_invoice_from_the_same_vendor_as_a_prior_period_settlement(self) -> None:
        context = ClassificationContext(
            lp_directory={},
            known_companies=(),
            prior_period_liability_payees=("Coral & Finch LLP",),
            prior_period_liability_amounts={"Coral & Finch LLP": Decimal("120000.00")},
            prior_period_liability_descriptions={"Coral & Finch LLP": "Audit fee accrual"},
            prior_period_end_year=2022,
        )
        row = _row(
            "txn-0001",
            description="OUTWARD WIRE CORAL AND FINCH LLP AUDIT FY22 FINAL",
            debit="130000",
            credit="0",
        )

        class NewExpenseClassifier:
            def classify(self, _rows):
                return [{"transaction_id": row.transaction_id, "category": "expense", "lp_code": None, "company": None}]

        result = classify_transactions([row], NewExpenseClassifier(), context)
        self.assertEqual(result[0].category.value, "expense")

    def test_does_not_treat_a_smaller_current_period_invoice_from_the_same_vendor_as_a_settlement(self) -> None:
        context = ClassificationContext(
            lp_directory={},
            known_companies=(),
            prior_period_liability_payees=("Coral & Finch LLP",),
            prior_period_liability_amounts={"Coral & Finch LLP": Decimal("120000.00")},
            prior_period_liability_descriptions={"Coral & Finch LLP": "Audit fee accrual"},
            prior_period_end_year=2022,
        )
        row = _row(
            "txn-0001",
            description="OUTWARD WIRE CORAL AND FINCH LLP AUDIT FY22 FINAL",
            debit="1",
            credit="0",
        )

        class NewExpenseClassifier:
            def classify(self, _rows):
                return [{"transaction_id": row.transaction_id, "category": "expense", "lp_code": None, "company": None}]

        result = classify_transactions([row], NewExpenseClassifier(), context)
        self.assertEqual(result[0].category.value, "expense")

    def test_rejects_cumulative_settlements_above_the_approved_opening_liability(self) -> None:
        context = ClassificationContext(
            lp_directory={},
            known_companies=(),
            prior_period_liability_payees=("Coral & Finch LLP",),
            prior_period_liability_amounts={"Coral & Finch LLP": Decimal("120000.00")},
            prior_period_liability_descriptions={"Coral & Finch LLP": "Audit fee accrual"},
            prior_period_end_year=2022,
        )
        rows = [
            _row("txn-0001", description="OUTWARD WIRE CORAL AND FINCH LLP AUDIT FY21 PARTIAL SETTLEMENT", debit="70000", credit="0"),
            _row("txn-0002", description="OUTWARD WIRE CORAL AND FINCH LLP AUDIT FY21 PARTIAL SETTLEMENT", debit="70000", credit="0"),
        ]

        class OverpayingSettlementClassifier:
            def classify(self, source_rows):
                return [{"transaction_id": row.transaction_id, "category": "accrual_settlement", "lp_code": None, "company": None} for row in source_rows]

        with self.assertRaisesRegex(ValueError, "ACCRUAL_SETTLEMENT_EXCEEDS_Q3_LIABILITY"):
            classify_transactions(rows, OverpayingSettlementClassifier(), context)

    def test_corrects_a_purchase_sale_pair_that_violates_cash_direction(self) -> None:
        row = _row(
            "txn-0001",
            description="INWARD WIRE FJORD PAYMENTS AS COMPLETION",
            debit="0",
            credit="29000000",
        )

        class IncorrectPurchaseClassifier:
            def classify(self, _rows):
                return [{"transaction_id": row.transaction_id, "category": "investment_purchase", "lp_code": None, "company": "Fjord Payments AS"}]

        result = classify_transactions([row], IncorrectPurchaseClassifier(), self.context)
        self.assertEqual(result[0].category.value, "sale_proceeds")
        self.assertEqual(result[0].classification_override_reason, "cash_direction_sale_proceeds")


class ClassificationContextTests(unittest.TestCase):
    def test_loads_assignment_reference_data_and_validates_every_sample_identity(self) -> None:
        pack = Path(__file__).parents[2] / "pack"
        context = load_classification_context(
            pack / "GPF_NAV_Summary_Q3_2022.xlsx",
            pack / "CVP_Valuation_Report_Q3_2022.xlsx",
        )
        self.assertEqual(context.lp_directory["R. Mehta Family Trust"], "LP10")
        self.assertEqual(context.prior_period_liability_payees, ("Coral & Finch LLP", "Harbourside Fund Services"))
        self.assertEqual(context.prior_period_liability_amounts["Coral & Finch LLP"], Decimal("120000.00"))
        _opening, transactions = parse_bank_statement(pack / "GPF_Bank_Statement_Q4_2022.pdf")
        classified = classify_transactions(transactions, DeterministicClassifier(), context)
        self.assertEqual(len(classified), 39)
        self.assertEqual(classified[18].lp_code, "LP10")
        self.assertEqual(classified[1].category.value, "accrual_settlement")
        self.assertEqual(classified[3].category.value, "accrual_settlement")

