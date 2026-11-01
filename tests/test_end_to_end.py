import json
import os
from pathlib import Path
import tempfile
import unittest

from app.orchestrator import run_close


class EndToEndTests(unittest.TestCase):
    def test_runs_a_complete_local_close_and_writes_required_outputs(self) -> None:
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            result = run_close(root / "pack", output, classifier_mode="deterministic", agent_a_port=0, agent_b_port=0)
            self.assertEqual(result["nav_q4"], "169764039.25")
            required = {"transactions.json", "a2a_log.jsonl", "marks.json", "review.json", "nav.json"}
            self.assertTrue(required <= {path.name for path in output.iterdir()})
            manifest = json.loads((output / "run_manifest.json").read_text())
            self.assertEqual(manifest["classifier_mode"], "deterministic")
            self.assertEqual(set(manifest["input_sha256"]), {
                "GPF_Bank_Statement_Q4_2022.pdf", "GPF_NAV_Summary_Q3_2022.xlsx",
                "CVP_Valuation_Report_Q3_2022.xlsx", "agent_b_data.json",
            })
            nav = json.loads((output / "nav.json").read_text())
            self.assertEqual(nav["bridge"]["difference"], 0.0)
            self.assertEqual(nav["management_fee"], 1250000.0)
            self.assertEqual(len((output / "a2a_log.jsonl").read_text().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
