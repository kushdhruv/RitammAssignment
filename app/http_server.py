"""Threaded local HTTP transport for A2A agent cards and JSON-RPC requests."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from typing import Any, Callable
from decimal import Decimal

MAX_REQUEST_BYTES = 1_048_576


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
                    request_id = rpc.get("id") if isinstance(rpc, dict) else None
                    if rpc.get("jsonrpc") != "2.0" or rpc.get("method") != "message/send":
                        raise ValueError("unsupported JSON-RPC request")
                    result = outer.handler(rpc.get("params", {}))
                    self._write(HTTPStatus.OK, {"jsonrpc": "2.0", "id": request_id, "result": result})
                except (ValueError, KeyError, json.JSONDecodeError) as error:
                    self._write(HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(error)}})
                except Exception:
                    self._write(HTTPStatus.INTERNAL_SERVER_ERROR, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "internal error"}})

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _write(self, status: HTTPStatus, body: dict[str, Any]) -> None:
                encoded = json.dumps(body, default=_json_default).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

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
