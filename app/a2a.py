"""Small, version-pinned A2A 0.3 JSON-RPC implementation for this workflow."""

from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def agent_card(name: str, description: str, url: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "url": url,
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [{"id": "quarter-close", "name": "Quarter close", "description": description, "tags": ["private-equity", "nav"]}],
    }


def _data_part(message: dict[str, Any]) -> dict[str, Any]:
    parts = message.get("parts", [])
    for part in parts:
        if part.get("kind") == "data" and isinstance(part.get("data"), dict):
            return part["data"]
    raise ValueError("A2A_DATA_PART_MISSING")


def _status_message(text: str) -> dict[str, Any]:
    return {"kind": "message", "role": "agent", "messageId": str(uuid4()), "parts": [{"kind": "text", "text": text}]}


def _activity_counter(items: list[dict[str, Any]], amount_name: str) -> Counter[tuple[str, str, Decimal]]:
    if not isinstance(items, list):
        raise ValueError("A2A_ACTIVITY_INVALID: activity must be a list")
    counter: Counter[tuple[str, str, Decimal]] = Counter()
    for item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError("activity item must be an object")
            company = str(item["company"]).strip()
            activity_date = date.fromisoformat(str(item["date"])).isoformat()
            amount = Decimal(str(item[amount_name]))
            if not company or not amount.is_finite() or amount <= 0 or amount * 100 != (amount * 100).to_integral_value():
                raise ValueError("invalid activity fields")
            key = (company.casefold(), activity_date, amount)
        except (InvalidOperation, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"A2A_ACTIVITY_INVALID: expected {amount_name}") from error
        counter[key] += 1
    return counter


@dataclass
class _TaskContext:
    task_id: str
    context_id: str


class ValuationA2AHandler:
    """Agent B's A2A gate. It releases private marks only after activity validation."""

    def __init__(self, data_path: Path, q3_nav_path: Path | None = None, q3_valuation_path: Path | None = None) -> None:
        self._data = json.loads(data_path.read_text(encoding="utf-8"))
        self._q3_nav_path = q3_nav_path
        self._q3_valuation_path = q3_valuation_path
        self._tasks: dict[str, _TaskContext] = {}
        self._responses_by_message_id: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = RLock()

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message")
        if not isinstance(message, dict):
            raise ValueError("A2A_MESSAGE_MISSING")
        message_id = message.get("messageId")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("A2A_MESSAGE_ID_MISSING")
        with self._lock:
            cached = self._responses_by_message_id.get(message_id)
            if cached is not None:
                self._responses_by_message_id.move_to_end(message_id)
                return deepcopy(cached)
            response = self._continue_task(message) if message.get("taskId") else self._start_task(message)
            self._responses_by_message_id[message_id] = deepcopy(response)
            if len(self._responses_by_message_id) > 1_024:
                self._responses_by_message_id.popitem(last=False)
            return response

    def _start_task(self, message: dict[str, Any]) -> dict[str, Any]:
        data = _data_part(message)
        if data.get("fund") != self._data["fund"]["code"] or data.get("valuation_date") != self._data["valuation_date"]:
            raise ValueError("A2A_REQUEST_INVALID: fund or valuation_date")
        task = _TaskContext(task_id=f"task-{uuid4()}", context_id=str(uuid4()))
        with self._lock:
            self._tasks[task.task_id] = task
        return {
            "kind": "task", "id": task.task_id, "contextId": task.context_id,
            "status": {"state": "input-required", "message": _status_message("Provide the fund's Q4 purchases and sales of companies.")},
        }

    def _continue_task(self, message: dict[str, Any]) -> dict[str, Any]:
        task_id = message.get("taskId")
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None or message.get("contextId") != task.context_id:
            raise ValueError("A2A_TASK_NOT_FOUND")
        data = _data_part(message)
        actual_purchases = _activity_counter(data.get("purchases", []), "amount_usd")
        actual_sales = _activity_counter(data.get("sales", []), "proceeds_usd")
        expected_purchases = _activity_counter(self._data["purchases"], "amount_usd")
        expected_sales = _activity_counter(self._data["sales"], "proceeds_usd")
        if actual_purchases != expected_purchases or actual_sales != expected_sales:
            matches = sum((actual_purchases & expected_purchases).values()) + sum((actual_sales & expected_sales).values())
            expected_count = sum(expected_purchases.values()) + sum(expected_sales.values())
            return {
                "kind": "task", "id": task.task_id, "contextId": task.context_id,
                "status": {"state": "input-required", "message": _status_message(f"Activity did not match. {matches} of {expected_count} items matched. Check company, date, and amount.")},
            }
        artifact_data = {
            "fund": self._data["fund"]["name"], "valuation_date": self._data["valuation_date"],
            "marks": self._data["marks"], "notes": self._data["notes"],
        }
        return {
            "kind": "task", "id": task.task_id, "contextId": task.context_id,
            "status": {"state": "completed", "message": _status_message("Q4 marks released after activity validation.")},
            "artifacts": [{"artifactId": "marks-GPF-2022-12-31", "parts": [{"kind": "data", "data": artifact_data}]}],
        }

    def calculate_close(self, transactions: list[Any], marks_artifact: dict[str, Any]) -> Any:
        """Own Agent B's Q3 loading, mark validation, review, and NAV calculation."""
        if self._q3_nav_path is None or self._q3_valuation_path is None:
            raise RuntimeError("VALUATION_INPUTS_NOT_CONFIGURED")
        from app.valuation import calculate_close, load_q3_data

        q3 = load_q3_data(self._q3_nav_path, self._q3_valuation_path)
        return calculate_close(q3, transactions, marks_artifact["marks"], eurusd_rate="1.0666", threshold_pct="20")


class A2AClient:
    def __init__(self, endpoint: str, log: Callable[[str, dict[str, Any]], None]) -> None:
        self.endpoint = endpoint
        self.log = log

    def send(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid4())
        rpc = {"jsonrpc": "2.0", "id": request_id, "method": "message/send", "params": {"message": message}}
        self.log("request", rpc)
        request = Request(self.endpoint, data=json.dumps(rpc, default=_json_default).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
                self.log("response", payload)
                message_text = payload.get("error", {}).get("message", "HTTP error")
            except (json.JSONDecodeError, UnicodeDecodeError):
                message_text = "HTTP error"
            raise RuntimeError(f"A2A_HTTP_ERROR {error.code}: {message_text}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"A2A_TRANSPORT_FAILED: {type(error).__name__}") from error
        self.log("response", payload)
        if "error" in payload:
            raise RuntimeError(f"A2A_ERROR: {payload['error']}")
        return payload["result"]


def timestamped_log(direction: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "direction": direction, "payload": payload}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")
