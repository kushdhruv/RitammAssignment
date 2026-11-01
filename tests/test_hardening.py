import json
from pathlib import Path
import random
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.a2a import A2AClient, ValuationA2AHandler
from app.bank_parser import parse_bank_statement
from app.classifier import DeterministicClassifier, classify_transactions
from app.http_server import LocalAgentServer
from app.models import Transaction
from app.reconciliation import reconcile_bank_statement
from app.valuation import calculate_close, load_q3_data


class A2AInputHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = ValuationA2AHandler(Path(__file__).parents[2] / "pack" / "agent_b_data.json")
        self.first = self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m1", "parts": [
            {"kind": "data", "data": {"fund": "GPF", "valuation_date": "2022-12-31"}}
        ]}})

    def test_rejects_non_list_activity_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "A2A_ACTIVITY_INVALID"):
            self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m2", "taskId": self.first["id"], "contextId": self.first["contextId"], "parts": [
                {"kind": "data", "data": {"purchases": "not-a-list", "sales": []}}
            ]}})

    def test_rejects_non_finite_activity_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "A2A_ACTIVITY_INVALID"):
            self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m2", "taskId": self.first["id"], "contextId": self.first["contextId"], "parts": [
                {"kind": "data", "data": {"purchases": [{"company": "Ionmere Materials Ltd", "date": "2022-12-15", "amount_usd": "NaN"}], "sales": []}}
            ]}})

    def test_rejects_nonnumeric_activity_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "A2A_ACTIVITY_INVALID"):
            self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m2", "taskId": self.first["id"], "contextId": self.first["contextId"], "parts": [
                {"kind": "data", "data": {"purchases": [{"company": "Ionmere Materials Ltd", "date": "2022-12-15", "amount_usd": "not-a-number"}], "sales": []}}
            ]}})

    def test_concurrent_new_tasks_have_unique_ids(self) -> None:
        def start(index: int) -> dict:
            return self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": f"concurrent-{index}", "parts": [
                {"kind": "data", "data": {"fund": "GPF", "valuation_date": "2022-12-31"}}
            ]}})
        with ThreadPoolExecutor(max_workers=12) as pool:
            tasks = list(pool.map(start, range(48)))
        self.assertEqual(len({task["id"] for task in tasks}), 48)


class ValuationInputHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).parents[2] / "pack"
        _, extracted = parse_bank_statement(root / "GPF_Bank_Statement_Q4_2022.pdf")
        self.transactions = classify_transactions(extracted, DeterministicClassifier())
        self.q3 = load_q3_data(root / "GPF_NAV_Summary_Q3_2022.xlsx", root / "CVP_Valuation_Report_Q3_2022.xlsx")
        self.marks = json.loads((root / "agent_b_data.json").read_text())["marks"]

    def test_rejects_duplicate_q4_marks(self) -> None:
        duplicate_marks = [*self.marks, dict(self.marks[0])]
        with self.assertRaisesRegex(ValueError, "Q4_MARK_DUPLICATE"):
            calculate_close(self.q3, self.transactions, duplicate_marks, "1.0666", "20")

    def test_rejects_non_finite_q4_mark(self) -> None:
        invalid_marks = [dict(mark) for mark in self.marks]
        invalid_marks[0]["fair_value"] = "NaN"
        with self.assertRaisesRegex(ValueError, "Q4_MARK_INVALID"):
            calculate_close(self.q3, self.transactions, invalid_marks, "1.0666", "20")


class FinancialInputHardeningTests(unittest.TestCase):
    def test_rejects_source_amounts_with_more_than_two_decimal_places(self) -> None:
        with self.assertRaisesRegex(Exception, "at most two decimal places"):
            Transaction(date="2022-10-01", description="bad amount", debit="1.005", credit="0", balance="1")

    def test_randomized_balance_sequences_reconcile_and_detect_a_cent_mutation(self) -> None:
        for seed in range(100):
            generator = random.Random(seed)
            opening = Decimal(generator.randint(1_000, 1_000_000)) / 100
            balance = opening
            rows: list[Transaction] = []
            for index in range(generator.randint(1, 40)):
                amount = Decimal(generator.randint(1, 100_000)) / 100
                if generator.choice([True, False]):
                    debit, credit = Decimal("0"), amount
                else:
                    debit, credit = amount, Decimal("0")
                balance += credit - debit
                rows.append(Transaction(transaction_id=f"txn-{index:04d}", date="2022-10-01", description="generated", debit=debit, credit=credit, balance=balance))
            self.assertEqual(reconcile_bank_statement(opening, rows).closing_cash, balance)
            broken = list(rows)
            broken[-1] = broken[-1].model_copy(update={"balance": broken[-1].balance + Decimal("0.01")})
            with self.assertRaisesRegex(ValueError, "BANK_RUNNING_BALANCE_MISMATCH"):
                reconcile_bank_statement(opening, broken)


class LlmFailureHardeningTests(unittest.TestCase):
    def test_does_not_retry_a_permanent_provider_error(self) -> None:
        from app.classifier import OpenAICompatibleClassifier

        classifier = OpenAICompatibleClassifier("key", "https://example.invalid/v1", "model", attempts=3)
        error = HTTPError("https://example.invalid/v1/chat/completions", 401, "unauthorized", {}, None)
        with patch("app.classifier.urlopen", side_effect=error) as mocked:
            with self.assertRaisesRegex(RuntimeError, "LLM_CLASSIFICATION_FAILED"):
                classifier.classify([Transaction(
                    transaction_id="txn-0001", date="2022-10-01", description="wire", debit="0", credit="1", balance="1"
                )])
        self.assertEqual(mocked.call_count, 1)

    def test_honors_retry_after_for_a_rate_limited_provider_response(self) -> None:
        from app.classifier import OpenAICompatibleClassifier

        class JsonResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps({"choices": [{"message": {"content": json.dumps({"classifications": [
                    {"transaction_id": "txn-0001", "category": "capital_call", "lp_code": "LP01", "company": None}
                ]})}}]}).encode()

        row = Transaction(transaction_id="txn-0001", date="2022-10-01", description="wire", debit="0", credit="1", balance="1")
        rate_limited = HTTPError("https://example.invalid/v1/chat/completions", 429, "rate limited", {"Retry-After": "7"}, None)
        classifier = OpenAICompatibleClassifier("key", "https://example.invalid/v1", "model", attempts=2)
        with patch("app.classifier.urlopen", side_effect=[rate_limited, JsonResponse()]), patch("app.classifier.time.sleep") as sleep:
            self.assertEqual(len(classifier.classify([row])), 1)
        sleep.assert_called_once_with(7.0)


class HttpTransportHardeningTests(unittest.TestCase):
    def test_returns_a_json_rpc_error_for_an_unhandled_handler_failure(self) -> None:
        server = LocalAgentServer("127.0.0.1", 0, {"name": "test"}, lambda _params: (_ for _ in ()).throw(RuntimeError("boom")))
        server.start()
        try:
            request = Request(f"http://127.0.0.1:{server.port}/a2a", data=json.dumps({"jsonrpc": "2.0", "id": "request-1", "method": "message/send", "params": {}}).encode(), method="POST", headers={"Content-Type": "application/json"})
            with self.assertLogs("gpf_nav_close.http", level="ERROR") as captured, self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 500)
            self.assertEqual(json.loads(raised.exception.read())["error"]["code"], -32603)
            self.assertIn("request_id='request-1'", "\n".join(captured.output))
            raised.exception.close()
        finally:
            server.stop()

    def test_does_not_expose_financial_workflow_errors_to_an_a2a_caller(self) -> None:
        server = LocalAgentServer(
            "127.0.0.1",
            0,
            {"name": "test"},
            lambda _params: (_ for _ in ()).throw(ValueError("BANK_RUNNING_BALANCE_MISMATCH txn-0019: expected 120.00")),
        )
        server.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.port}/a2a",
                data=json.dumps({"jsonrpc": "2.0", "id": "request-2", "method": "message/send", "params": {}}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            try:
                body = json.loads(raised.exception.read())
                self.assertEqual(raised.exception.code, 500)
                self.assertEqual(body["error"]["message"], "internal error")
                self.assertNotIn("txn-0019", json.dumps(body))
            finally:
                raised.exception.close()
        finally:
            server.stop()

    def test_client_surfaces_a_structured_server_error_without_a_transport_traceback(self) -> None:
        server = LocalAgentServer("127.0.0.1", 0, {"name": "test"}, lambda _params: (_ for _ in ()).throw(RuntimeError("boom")))
        server.start()
        try:
            client = A2AClient(f"http://127.0.0.1:{server.port}/a2a", lambda _direction, _payload: None)
            with self.assertRaisesRegex(RuntimeError, "A2A_HTTP_ERROR 500: internal error"):
                client.send({"kind": "message", "role": "user", "messageId": "m", "parts": []})
        finally:
            server.stop()

    def test_rejects_an_oversized_request_before_json_parsing(self) -> None:
        invoked = False

        def handler(_params: dict) -> dict:
            nonlocal invoked
            invoked = True
            return {"ok": True}

        server = LocalAgentServer("127.0.0.1", 0, {"name": "test"}, handler)
        server.start()
        try:
            request = Request(f"http://127.0.0.1:{server.port}/a2a", data=b"x" * (1_048_577), method="POST", headers={"Content-Type": "application/json"})
            try:
                urlopen(request, timeout=5)
            except HTTPError as error:
                self.assertEqual(error.code, 413)
            except ConnectionAbortedError:
                # Windows may abort a client that continues streaming after a server rejects its declared body size.
                pass
            self.assertFalse(invoked)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
