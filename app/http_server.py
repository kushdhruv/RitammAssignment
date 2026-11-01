"""Threaded local HTTP transport for A2A agent cards and JSON-RPC requests."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from threading import Thread
from typing import Any, Callable
from decimal import Decimal

MAX_REQUEST_BYTES = 1_048_576
LOGGER = logging.getLogger("gpf_nav_close.http")
SAFE_CLIENT_ERROR_PREFIXES = ("A2A_", "AGENT_A_")


class LocalAgentServer:
    def __init__(self, host: str, port: int, card: dict[str, Any], handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.card = card
        self.handler = handler
        outer = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/.well-known/agent-card.json":
                    self._write(HTTPStatus.OK, outer.card)
                else:
                    self._write(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/a2a":
                    self._write(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                request_id: Any = None
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0:
                        raise ValueError("invalid Content-Length")
                    if length > MAX_REQUEST_BYTES:
                        self._write(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request body too large"}})
                        return
                    rpc = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(rpc, dict):
                        raise ValueError("unsupported JSON-RPC request")
                    request_id = rpc.get("id")
                    if rpc.get("jsonrpc") != "2.0" or rpc.get("method") != "message/send":
                        raise ValueError("unsupported JSON-RPC request")
                except (ValueError, KeyError, json.JSONDecodeError) as error:
                    self._write(HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(error)}})
                    return
                try:
                    result = outer.handler(rpc.get("params", {}))
                except ValueError as error:
                    if _is_safe_client_error(error):
                        self._write(HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(error)}})
                    else:
                        self._write_internal_error(request_id, error)
                except Exception as error:
                    self._write_internal_error(request_id, error)
                else:
                    self._write(HTTPStatus.OK, {"jsonrpc": "2.0", "id": request_id, "result": result})

            def _write_internal_error(self, request_id: Any, error: Exception) -> None:
                # Log a traceback for operators, but keep the wire response generic so
                # internal details, transaction data, and configuration never leak.
                LOGGER.exception(
                    "Unhandled A2A request failure request_id=%r path=%r error_type=%s",
                    request_id,
                    self.path,
                    type(error).__name__,
                )
                self._write(HTTPStatus.INTERNAL_SERVER_ERROR, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "internal error"}})

            def log_message(self, format_text: str, *args: Any) -> None:
                LOGGER.info("A2A HTTP %s", format_text % args)

            def _write(self, status: HTTPStatus, body: dict[str, Any]) -> None:
                try:
                    encoded = json.dumps(body, default=_json_default).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as error:
                    LOGGER.warning("A2A client disconnected before response delivery: %s", type(error).__name__)

        self._server = ThreadingHTTPServer((host, port), RequestHandler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def _is_safe_client_error(error: ValueError) -> bool:
    return str(error).startswith(SAFE_CLIENT_ERROR_PREFIXES)
