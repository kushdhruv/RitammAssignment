"""Tests for the lightweight durable workflow engine."""

from decimal import Decimal
from pathlib import Path
import pytest

from app.models import Transaction, Category
from app.workflow import WorkflowEngine, canonical_json


def test_canonical_json_deterministic():
    data1 = {"b": 2, "a": Decimal("100.50"), "c": [3, 1]}
    data2 = {"a": Decimal("100.50"), "c": [3, 1], "b": 2}
    assert canonical_json(data1) == canonical_json(data2)


def test_workflow_runs_and_persists(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"
    call_count = 0

    def compute(x: int, y: int) -> int:
        nonlocal call_count
        call_count += 1
        return x + y

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        result = engine.run_activity("sum_step", compute, 10, 20)
        assert result == 30
        assert call_count == 1
        assert engine.is_completed("sum_step")

        record = engine.get_activity_record("sum_step")
        assert record is not None
        assert record["status"] == "COMPLETED"
        assert record["output_json"] == "30"


def test_workflow_cache_hit_skips_execution(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"
    call_count = 0

    def compute_expensive(val: str) -> dict:
        nonlocal call_count
        call_count += 1
        return {"value": val, "processed": True}

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        first = engine.run_activity("expensive_step", compute_expensive, "test_input")
        assert first == {"value": "test_input", "processed": True}
        assert call_count == 1

        # Second call with identical arguments: MUST skip execution and return cached value
        second = engine.run_activity("expensive_step", compute_expensive, "test_input")
        assert second == {"value": "test_input", "processed": True}
        assert call_count == 1  # Not called again!


def test_workflow_input_change_reexecutes(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"
    call_count = 0

    def compute(val: str) -> str:
        nonlocal call_count
        call_count += 1
        return val.upper()

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        engine.run_activity("upper_step", compute, "hello")
        assert call_count == 1

        # Changing input must re-execute
        result = engine.run_activity("upper_step", compute, "world")
        assert result == "WORLD"
        assert call_count == 2


def test_workflow_retry_on_transient_failure(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"
    attempts = 0

    def flaky_network_call() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Temporary network hiccup")
        return "success"

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        result = engine.run_activity("flaky_step", flaky_network_call, max_retries=3, initial_delay=0.01)
        assert result == "success"
        assert attempts == 3
        assert engine.is_completed("flaky_step")


def test_workflow_persists_error_on_exhaustion(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"

    def fatal_step() -> None:
        raise ValueError("FATAL_DATA_CORRUPTION")

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        with pytest.raises(ValueError, match="FATAL_DATA_CORRUPTION"):
            engine.run_activity("fatal_step", fatal_step, max_retries=2, initial_delay=0.01)

        record = engine.get_activity_record("fatal_step")
        assert record is not None
        assert record["status"] == "FAILED"
        assert "FATAL_DATA_CORRUPTION" in record["error_text"]


def test_workflow_clean_purges_state(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        engine.run_activity("step_a", lambda: "val_a")
        assert engine.is_completed("step_a")

    # Re-opening with clean=True
    with WorkflowEngine(db_file, workflow_id="close_q4", clean=True) as engine:
        assert not engine.is_completed("step_a")
        assert engine.get_activity_record("step_a") is None


def test_workflow_pydantic_serialization(tmp_path: Path):
    db_file = tmp_path / "test_workflow.db"

    txn = Transaction(
        transaction_id="txn-001",
        date="2022-10-05",
        description="Harbourside capital call",
        debit=Decimal("0.00"),
        credit=Decimal("500000.00"),
        balance=Decimal("500000.00"),
        category=Category.CAPITAL_CALL,
    )

    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        saved = engine.run_activity(
            "parse_txn",
            lambda: [txn],
            serializer=lambda txns: [t.model_dump(mode="json") for t in txns],
            deserializer=lambda raw: [Transaction.model_validate(item) for item in raw],
        )
        assert len(saved) == 1
        assert saved[0].transaction_id == "txn-001"

    # In a new session, rehydrate from cache
    with WorkflowEngine(db_file, workflow_id="close_q4") as engine:
        rehydrated = engine.run_activity(
            "parse_txn",
            lambda: pytest.fail("Should not execute function on cache hit!"),
            serializer=lambda txns: [t.model_dump(mode="json") for t in txns],
            deserializer=lambda raw: [Transaction.model_validate(item) for item in raw],
        )
        assert len(rehydrated) == 1
        assert rehydrated[0].transaction_id == "txn-001"
        assert rehydrated[0].category == Category.CAPITAL_CALL

