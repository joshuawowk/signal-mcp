"""Thin JSON-RPC 2.0 client for the signal-cli HTTP daemon.

The daemon (started on the host by ~/.config/systemd/user/signal-cli-daemon.service)
exposes synchronous JSON-RPC at http://127.0.0.1:8080/api/v1/rpc. It
does NOT push notifications, so we drain incoming messages by calling
the `receive` method with a short timeout.
"""
from __future__ import annotations
import json
import uuid
from typing import Any

import httpx

from config import SIGNAL_RPC_URL


class SignalRpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"signal-cli RPC error {code}: {message}")
        self.code = code
        self.rpc_message = message
        self.data = data


def call(method: str, params: dict | None = None, *, timeout: float = 30.0) -> Any:
    """Synchronous JSON-RPC call. Returns the `result` field on success.

    Raises SignalRpcError on protocol-level errors and httpx errors on
    transport failures. Both messages are designed to be readable when
    surfaced back to the calling agent.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    resp = httpx.post(
        SIGNAL_RPC_URL,
        json=payload,
        timeout=timeout,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body and body["error"] is not None:
        err = body["error"]
        raise SignalRpcError(
            int(err.get("code", -1)),
            str(err.get("message", "")),
            err.get("data"),
        )
    return body.get("result")


def ping() -> str:
    """Return the daemon's reported signal-cli version, or raise."""
    result = call("version")
    if isinstance(result, dict) and "version" in result:
        return str(result["version"])
    return json.dumps(result)
