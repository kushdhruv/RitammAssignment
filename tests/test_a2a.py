import json
from pathlib import Path
import unittest

from app.a2a import ValuationA2AHandler


class A2ATests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = ValuationA2AHandler(Path(__file__).parents[1] / "pack" / "agent_b_data.json")

    def test_marks_are_withheld_until_activity_matches(self) -> None:
        first = self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m1", "parts": [
            {"kind": "data", "data": {"fund": "GPF", "valuation_date": "2022-12-31"}}
        ]}})
        self.assertEqual(first["status"]["state"], "input-required")
        retry = self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m2", "taskId": first["id"], "contextId": first["contextId"], "parts": [
            {"kind": "data", "data": {"purchases": [], "sales": []}}
        ]}})
        self.assertEqual(retry["status"]["state"], "input-required")

    def test_matching_activity_releases_the_unmodified_marks_artifact(self) -> None:
        first = self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m1", "parts": [
            {"kind": "data", "data": {"fund": "GPF", "valuation_date": "2022-12-31"}}
        ]}})
        completed = self.handler.handle({"message": {"kind": "message", "role": "user", "messageId": "m2", "taskId": first["id"], "contextId": first["contextId"], "parts": [
            {"kind": "data", "data": {
                "purchases": [{"company": "Ionmere Materials Ltd", "date": "2022-12-15", "amount_usd": 14000000}],
                "sales": [{"company": "Fjord Payments AS", "date": "2022-11-18", "proceeds_usd": 29000000}],
            }}
        ]}})
        self.assertEqual(completed["status"]["state"], "completed")
        self.assertEqual(completed["artifacts"][0]["parts"][0]["data"]["fund"], "The Greatness PE Fund, L.P.")
        self.assertEqual(completed["artifacts"][0]["parts"][0]["data"]["marks"][0]["company"], "Aurora Logistics Pvt Ltd")

    def test_repeated_initial_message_id_is_idempotent(self) -> None:
        message = {"message": {"kind": "message", "role": "user", "messageId": "same-message", "parts": [
            {"kind": "data", "data": {"fund": "GPF", "valuation_date": "2022-12-31"}}
        ]}}
        first = self.handler.handle(message)
        retry = self.handler.handle(message)
        self.assertEqual(retry["id"], first["id"])
        self.assertEqual(retry["contextId"], first["contextId"])


if __name__ == "__main__":
    unittest.main()
