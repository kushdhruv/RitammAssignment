import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.orchestrator import run_close


class EndToEndTests(unittest.TestCase):
    def test_runs_a_complete_local_close_and_writes_required_outputs(self) -> None:
        root = Path(__file__).parents[1]
        pack_dir = (root / "pack") if (root / "pack").is_dir() else (root.parent / "pack")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            result = run_close(pack_dir, output, classifier_mode="deterministic", agent_a_port=0, agent_b_port=0)
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

    def test_creates_a_missing_output_parent(self) -> None:
        root = Path(__file__).parents[1]
        pack_dir = (root / "pack") if (root / "pack").is_dir() else (root.parent / "pack")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "missing-parent" / "output"
            run_close(pack_dir, output, classifier_mode="deterministic", agent_a_port=0, agent_b_port=0)
            self.assertTrue((output / "nav.json").is_file())

    def test_rejects_missing_live_llm_configuration_before_starting_http_servers(self) -> None:
        root = Path(__file__).parents[1]
        pack_dir = (root / "pack") if (root / "pack").is_dir() else (root.parent / "pack")
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {"LLM_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": ""},
        ), patch("app.orchestrator.LocalAgentServer") as server:
            with self.assertRaisesRegex(ValueError, "LLM_CONFIGURATION_MISSING"):
                run_close(pack_dir, Path(temporary) / "output", classifier_mode="llm", agent_a_port=0, agent_b_port=0)
        server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
