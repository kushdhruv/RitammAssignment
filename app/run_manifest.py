"""Input-integrity metadata for a reproducible quarter-close run."""

from __future__ import annotations

import hashlib
from pathlib import Path

INPUT_FILENAMES = (
    "GPF_Bank_Statement_Q4_2022.pdf",
    "GPF_NAV_Summary_Q3_2022.xlsx",
    "CVP_Valuation_Report_Q3_2022.xlsx",
    "agent_b_data.json",
)


def input_manifest(pack_dir: Path, classifier_mode: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "classifier_mode": classifier_mode,
        "input_sha256": {filename: _sha256(pack_dir / filename) for filename in INPUT_FILENAMES},
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
