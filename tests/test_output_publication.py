from pathlib import Path
import tempfile
import unittest

from app.output_writer import publish_directory, write_json


class OutputPublicationTests(unittest.TestCase):
    def test_replaces_a_previous_complete_run_only_after_new_staging_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            write_json(output / "nav.json", {"run": "old"})
            staging = root / "staging"
            staging.mkdir()
            write_json(staging / "nav.json", {"run": "new"})
            write_json(staging / "transactions.json", [])
            publish_directory(staging, output)
            self.assertFalse(staging.exists())
            self.assertIn('"new"', (output / "nav.json").read_text())
            self.assertTrue((output / "transactions.json").is_file())

    def test_refuses_to_publish_a_missing_staging_directory_without_touching_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            write_json(output / "nav.json", {"run": "old"})
            with self.assertRaisesRegex(ValueError, "OUTPUT_STAGING_MISSING"):
                publish_directory(root / "missing", output)
            self.assertIn('"old"', (output / "nav.json").read_text())


if __name__ == "__main__":
    unittest.main()
