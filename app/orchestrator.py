"""Runs the complete two-agent quarter-close workflow in one process."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from app.a2a import A2AClient, ValuationA2AHandler, agent_card, timestamped_log
from app.bank_parser import parse_bank_statement
from app.classifier import DeterministicClassifier, OpenAICompatibleClassifier, classify_transactions
from app.http_server import LocalAgentServer
from app.models import Transaction
from app.output_writer import publish_directory, write_json, write_jsonl
from app.reconciliation import reconcile_bank_statement
from app.run_manifest import input_manifest
from app.valuation import CloseResult
from app.workflow import WorkflowEngine, canonical_json


def run_close(
    pack_dir: Path,
    output_dir: Path,
    *,
    classifier_mode: str = "llm",
    agent_a_port: int = 8001,
    agent_b_port: int = 8002,
    clean: bool = False,
    workflow_db_path: Path | None = None,
    workflow_id: str | None = None,
) -> dict[str, str]:
    _verify_pack(pack_dir)
    if classifier_mode not in {"llm", "deterministic"}:
        raise ValueError("CLASSIFIER_MODE_INVALID")
    manifest = input_manifest(pack_dir, classifier_mode)
    traces: list[dict[str, Any]] = []
    a2a_log: list[dict[str, Any]] = []
    trace_id = uuid4().hex

    valuation_handler = ValuationA2AHandler(
        pack_dir / "agent_b_data.json",
        pack_dir / "GPF_NAV_Summary_Q3_2022.xlsx",
        pack_dir / "CVP_Valuation_Report_Q3_2022.xlsx",
    )
    # Validate all static dependencies before exposing either endpoint or allocating resources. This
    # prevents a missing LLM configuration from becoming a generic server 500 or leaking open files.
    classification_context = valuation_handler.classification_context()
    classifier = (
        DeterministicClassifier()
        if classifier_mode == "deterministic"
        else OpenAICompatibleClassifier.from_environment(classification_context)
    )

    manifest_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()[:16]
    state_dir = output_dir.parent / ".workflow_state"
    db_file = workflow_db_path or (state_dir / f"gpf_{manifest_hash}.db")
    wid = workflow_id or f"gpf_{manifest_hash}"
    engine = WorkflowEngine(db_file, workflow_id=wid, clean=clean)

    def trace(event: str, **attributes: Any) -> None:
        traces.append({"timestamp": datetime.now(timezone.utc).isoformat(), "trace_id": trace_id, "event": event, **attributes})

    b_server = LocalAgentServer("127.0.0.1", agent_b_port, agent_card("Valuation & NAV Agent", "Validates investment activity and releases Q4 marks.", "placeholder"), valuation_handler.handle)
    b_server.card["url"] = f"http://127.0.0.1:{b_server.port}/a2a"
    def execute_agent_a() -> dict[str, str]:
        # Activity 1: Parse and reconcile bank statement
        def _parse_and_reconcile() -> dict[str, Any]:
            opening, extracted_txns = parse_bank_statement(pack_dir / "GPF_Bank_Statement_Q4_2022.pdf")
            reconciliation = reconcile_bank_statement(opening, extracted_txns)
            return {
                "opening_cash": str(opening),
                "closing_cash": str(reconciliation.closing_cash),
                "extracted": [t.model_dump(mode="json") for t in extracted_txns],
            }

        bank_data = engine.run_activity("parse_and_reconcile_bank", _parse_and_reconcile)
        extracted = [Transaction.model_validate(row) for row in bank_data["extracted"]]
        trace("bank_statement_extracted", transaction_count=len(extracted))
        trace("bank_statement_reconciled", closing_cash=bank_data["closing_cash"])

        # Activity 2: Classify transactions with batch-level durable checkpoints
        def _classify() -> list[dict[str, Any]]:
            classified_txns = classify_transactions(
                extracted, classifier, classification_context, workflow_engine=engine
            )
            return [t.model_dump(mode="json") for t in classified_txns]

        classified_data = engine.run_activity("classify_transactions", _classify)
        transactions = [Transaction.model_validate(row) for row in classified_data]
        trace("transactions_classified", mode=classifier_mode)

        # Activity 3: A2A Marks Handshake
        def _exchange_marks() -> dict[str, Any]:
            captured_logs: list[dict[str, Any]] = []
            client = A2AClient(b_server.card["url"], lambda direction, payload: captured_logs.append(timestamped_log(direction, payload)))
            marks = _request_marks(client, transactions)
            return {"marks": marks, "logs": captured_logs}

        a2a_result = engine.run_activity("a2a_marks_exchange", _exchange_marks)
        marks_artifact = a2a_result["marks"]
        for entry in a2a_result.get("logs", []):
            a2a_log.append(entry)
        trace("marks_released")

        # Activity 4: Calculate Close & NAV Bridge
        def _calculate_close() -> dict[str, Any]:
            close_result = valuation_handler.calculate_close(transactions, marks_artifact)
            return {
                "holdings": close_result.holdings,
                "review": close_result.review,
                "nav": close_result.nav,
            }

        close_dict = engine.run_activity("calculate_close", _calculate_close)
        close = CloseResult(
            holdings=close_dict["holdings"],
            review=close_dict["review"],
            nav=close_dict["nav"],
        )
        trace("nav_calculated", nav_q4=str(close.nav["nav_q4"]))

        # Activity 5: Atomic output publication
        trace("outputs_written")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(tempfile.mkdtemp(prefix=".close-output-", dir=output_dir.parent))
        try:
            _write_outputs(staging_directory, transactions, a2a_log, marks_artifact, close, traces, manifest)
            write_jsonl(staging_directory / "run_trace.jsonl", traces)
            publish_directory(staging_directory, output_dir)
        except Exception:
            shutil.rmtree(staging_directory, ignore_errors=True)
            raise
        nav_q4_val = Decimal(str(close.nav["nav_q4"]))
        return {"nav_q4": f"{nav_q4_val:.2f}"}


    a_server = LocalAgentServer("127.0.0.1", agent_a_port, agent_card("Bank & Ingestion Agent", "Classifies and reconciles fund bank transactions.", "placeholder"), _agent_a_handler(execute_agent_a))
    a_server.card["url"] = f"http://127.0.0.1:{a_server.port}/a2a"
    b_server.start()
    a_server.start()
    trace("agents_started", agent_a_port=a_server.port, agent_b_port=b_server.port)
    try:
        # Agent A performs five bounded provider calls before responding, so its
        # coordinator request needs a longer timeout than the Agent B exchange.
        agent_a_client = A2AClient(a_server.card["url"], lambda _direction, _payload: None, timeout_seconds=240)
        completed = agent_a_client.send({"kind": "message", "role": "user", "messageId": str(uuid4()), "parts": [{"kind": "data", "data": {"fund": "GPF", "operation": "run_close"}}]})
        if completed.get("status", {}).get("state") != "completed":
            raise RuntimeError("AGENT_A_EXECUTION_FAILED")
        return completed["artifacts"][0]["parts"][0]["data"]
    finally:
        a_server.stop()
        b_server.stop()
        engine.close()





def _request_marks(client: A2AClient, transactions: list) -> dict[str, Any]:
    first = client.send({"kind": "message", "role": "user", "messageId": str(uuid4()), "parts": [{"kind": "data", "data": {"fund": "GPF", "valuation_date": "2022-12-31"}}]})
    if first.get("status", {}).get("state") != "input-required":
        raise RuntimeError("A2A_PROTOCOL_ERROR: Agent B did not request activity")
    purchases = [{"company": row.company, "date": row.date.isoformat(), "amount_usd": row.debit} for row in transactions if row.category and row.category.value == "investment_purchase"]
    sales = [{"company": row.company, "date": row.date.isoformat(), "proceeds_usd": row.credit} for row in transactions if row.category and row.category.value == "sale_proceeds"]
    completed = client.send({"kind": "message", "role": "user", "messageId": str(uuid4()), "taskId": first["id"], "contextId": first["contextId"], "parts": [{"kind": "data", "data": {"purchases": purchases, "sales": sales}}]})
    if completed.get("status", {}).get("state") != "completed":
        raise RuntimeError("A2A_ACTIVITY_VALIDATION_FAILED")
    try:
        return completed["artifacts"][0]["parts"][0]["data"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("A2A_MARKS_ARTIFACT_MISSING") from error


def _write_outputs(output_dir: Path, transactions: list, a2a_log: list[dict[str, Any]], marks: dict[str, Any], close: Any, traces: list[dict[str, Any]], manifest: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "transactions.json", [row.model_dump() for row in transactions])
    write_jsonl(output_dir / "a2a_log.jsonl", a2a_log)
    write_json(output_dir / "marks.json", marks)
    write_json(output_dir / "review.json", {"threshold_pct": 20, "eurusd_rate": 1.0666, "reviews": close.review})
    write_json(output_dir / "nav.json", close.nav)
    write_json(output_dir / "run_manifest.json", manifest)


def _verify_pack(pack_dir: Path) -> None:
    required = {"GPF_Bank_Statement_Q4_2022.pdf", "GPF_NAV_Summary_Q3_2022.xlsx", "CVP_Valuation_Report_Q3_2022.xlsx", "agent_b_data.json"}
    missing = sorted(name for name in required if not (pack_dir / name).is_file())
    if missing:
        raise FileNotFoundError(f"INPUT_PACK_MISSING: {', '.join(missing)}")


def _agent_a_handler(execute: Any) -> Any:
    def handle(params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message", {})
        try:
            data = next(part["data"] for part in message["parts"] if part.get("kind") == "data")
        except (KeyError, StopIteration, TypeError) as error:
            raise ValueError("AGENT_A_DATA_PART_MISSING") from error
        if data.get("fund") != "GPF" or data.get("operation") != "run_close":
            raise ValueError("AGENT_A_REQUEST_INVALID")
        result = execute()
        return {"kind": "task", "id": f"task-{uuid4()}", "contextId": str(uuid4()), "status": {"state": "completed"}, "artifacts": [{"artifactId": "close-result", "parts": [{"kind": "data", "data": result}]}]}
    return handle
