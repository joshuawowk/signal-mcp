"""Shared config for the MCP server and subscriber.

All values come from environment variables (see .env.example). Nothing
secret is hard-coded; the sender/recipient numbers are operational
config, not credentials.
"""
from __future__ import annotations
import os
from pathlib import Path

SIGNAL_SENDER = os.environ.get("SIGNAL_SENDER", "").strip()
SIGNAL_DEFAULT_RECIPIENT = os.environ.get("SIGNAL_DEFAULT_RECIPIENT", "").strip()
SIGNAL_RPC_URL = os.environ.get(
    "SIGNAL_RPC_URL", "http://127.0.0.1:8080/api/v1/rpc"
).strip()
SIGNAL_POLL_INTERVAL = float(os.environ.get("SIGNAL_POLL_INTERVAL", "3"))
SIGNAL_INBOX_RETENTION_DAYS = int(
    os.environ.get("SIGNAL_INBOX_RETENTION_DAYS", "14")
)
SIGNAL_INBOX_PATH = Path(
    os.environ.get("SIGNAL_INBOX_PATH", "/data/inbox.jsonl")
)


def require_sender() -> str:
    if not SIGNAL_SENDER:
        raise RuntimeError(
            "SIGNAL_SENDER not set. Configure .env with the signal-cli "
            "account registered on the host."
        )
    return SIGNAL_SENDER


def default_recipient() -> str:
    if not SIGNAL_DEFAULT_RECIPIENT:
        raise RuntimeError(
            "SIGNAL_DEFAULT_RECIPIENT not set. Configure .env with the "
            "Signal number to ping by default."
        )
    return SIGNAL_DEFAULT_RECIPIENT
