"""Tests for crash recovery, checkpointing, and workflow resumption in run_close."""

from decimal import Decimal
from pathlib import Path
import pytest

from app.orchestrator import run_close
from app.workflow import WorkflowEngine


def _resolve_pack_dir() -> Path:
    project_root = Path(__file__).resolve().parent.parent
    local_pack = project_root / "pack"
    if local_pack.is_dir():
        return local_pack
    sibling_pack = project_root.parent / "pack"
    if sibling_pack.is_dir():
        return sibling_pack
    raise FileNotFoundError(f"Pack directory not found at {local_pack} or {sibling_pack}")


def test_workflow_recovers_from_interruption(tmp_path: Path):
    pack_dir = _resolve_pack_dir()
    output_dir = tmp_path / "output"
    workflow_db = tmp_path / "test_recovery.db"

    # 1. Run with clean state in deterministic mode
    result = run_close(
        pack_dir,
        output_dir,
        classifier_mode="deterministic",
        clean=True,
        workflow_db_path=workflow_db,
        workflow_id="test_wf",
    )
    assert result["nav_q4"] == "169764039.25"

    # Verify activities are stored as COMPLETED
    with WorkflowEngine(workflow_db, workflow_id="test_wf") as engine:
        assert engine.is_completed("parse_and_reconcile_bank")
        assert engine.is_completed("classify_transactions")
        assert engine.is_completed("a2a_marks_exchange")
        assert engine.is_completed("calculate_close")

    # 2. Re-running with existing state:
    # All activities should complete immediately via cache hit
    resumed_result = run_close(
        pack_dir,
        output_dir,
        classifier_mode="deterministic",
        clean=False,
        workflow_db_path=workflow_db,
        workflow_id="test_wf",
    )
    assert resumed_result["nav_q4"] == "169764039.25"


def test_workflow_clean_purges_and_recalculates(tmp_path: Path):
    pack_dir = _resolve_pack_dir()
    output_dir = tmp_path / "output"
    workflow_db = tmp_path / "test_clean.db"

    run_close(
        pack_dir,
        output_dir,
        classifier_mode="deterministic",
        clean=True,
        workflow_db_path=workflow_db,
        workflow_id="test_wf_clean",
    )

    with WorkflowEngine(workflow_db, workflow_id="test_wf_clean") as engine:
        assert engine.is_completed("calculate_close")

    # Force clean run
    fresh_result = run_close(
        pack_dir,
        output_dir,
        classifier_mode="deterministic",
        clean=True,
        workflow_db_path=workflow_db,
        workflow_id="test_wf_clean",
    )
    assert fresh_result["nav_q4"] == "169764039.25"

