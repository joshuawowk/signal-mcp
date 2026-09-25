"""Inbox subscriber (TCP streaming).

The signal-cli HTTP daemon (port 8080) is purely synchronous — its
`receive` RPC method is permanently locked by the daemon's internal
auto-receive loop. The TCP endpoint (port 7583) is the supported way
to consume incoming messages: it pushes JSON-RPC notifications over a
long-lived newline-delimited socket. This subscriber:

  1. Opens a TCP connection to the daemon
  2. Reads newline-delimited JSON
  3. For each message with `method == "receive"`, extracts the envelope
     and appends a record to /data/inbox.jsonl
  4. Reconnects with exponential backoff on disconnect

Dedupes on Signal's per-sender `timestamp`. Prunes records older than
SIGNAL_INBOX_RETENTION_DAYS on startup. Logs to stderr.
"""
from __future__ import annotations
import json
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from config import (
    SIGNAL_INBOX_PATH,
    SIGNAL_INBOX_RETENTION_DAYS,
    require_sender,
)

TCP_HOST = os.environ.get("SIGNAL_TCP_HOST", "127.0.0.1")
TCP_PORT = int(os.environ.get("SIGNAL_TCP_PORT", "7583"))


def log(msg: str) -> None:
    print(f"[subscriber] {msg}", file=sys.stderr, flush=True)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_seen_timestamps(path: Path) -> set[int]:
    seen: set[int] = set()
    if not path.exists():
        return seen
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                ts = msg.get("signal_timestamp")
                if isinstance(ts, int):
                    seen.add(ts)
            except json.JSONDecodeError:
                continue
    return seen


def prune_old(path: Path, retention_days: int) -> None:
    if not path.exists() or retention_days <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
                ts = datetime.fromisoformat(
                    msg["received_at"].replace("Z", "+00:00")
                )
            except (KeyError, ValueError, json.JSONDecodeError):
                kept.append(stripped)
                continue
            if ts >= cutoff:
                kept.append(stripped)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Keep the file even when empty after pruning. Deleting it makes
    # signal_check_replies / signal_health look like the subscriber is
    # down when it's actually just an empty inbox.
    tmp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    tmp.replace(path)


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_record(rpc_msg: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one daemon-pushed JSON-RPC message into an inbox record.

    The daemon emits notifications like:
      {"jsonrpc":"2.0","method":"receive",
       "params":{"envelope":{...},"account":"+1..."}}

    We keep text body, sender, and Signal timestamp; ignore everything
    else (typing indicators, receipts, sync messages without bodies,
    reaction-only or attachment-only payloads).
    """
    if rpc_msg.get("method") != "receive":
        return None
    params = rpc_msg.get("params")
    if not isinstance(params, dict):
        return None
    envelope = params.get("envelope")
    if not isinstance(envelope, dict):
        return None
    data = envelope.get("dataMessage")
    if not isinstance(data, dict):
        return None
    body = data.get("message")
    if not body:
        return None
    source = envelope.get("sourceNumber") or envelope.get("source")
    ts = envelope.get("timestamp")
    return {
        "received_at": _iso(),
        "from": source,
        "body": body,
        "signal_timestamp": ts,
    }


def iter_jsonrpc(sock: socket.socket) -> Iterable[dict[str, Any]]:
    """Yield decoded JSON-RPC objects from a newline-delimited TCP stream."""
    buf = b""
    while True:
        chunk = sock.recv(8192)
        if not chunk:
            return  # peer closed
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as e:
                log(f"invalid JSON from daemon ({e}): {line[:200]!r}")


def connect_once(host: str, port: int) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(None)  # blocking reads for streaming
    # Keepalive on idle connections (rough defaults; Linux-only knobs
    # below are silently skipped on other platforms).
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for opt, val in (
        ("TCP_KEEPIDLE", 60),
        ("TCP_KEEPINTVL", 30),
        ("TCP_KEEPCNT", 4),
    ):
        if hasattr(socket, opt):
            sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
    return sock


def main() -> int:
    inbox = Path(SIGNAL_INBOX_PATH)
    sender = require_sender()
    log(f"starting; sender={sender}; inbox={inbox}; "
        f"tcp={TCP_HOST}:{TCP_PORT}; retention={SIGNAL_INBOX_RETENTION_DAYS}d")
    prune_old(inbox, SIGNAL_INBOX_RETENTION_DAYS)
    # Ensure the inbox file always exists on startup so consumers never
    # mistake an empty inbox for a dead subscriber.
    try:
        inbox.parent.mkdir(parents=True, exist_ok=True)
        if not inbox.exists():
            inbox.touch()
    except OSError as e:
        log(f"could not pre-create inbox: {e}")
    seen = load_seen_timestamps(inbox)
    log(f"loaded {len(seen)} known timestamps")

    backoff = 2.0
    while True:
        try:
            log(f"connecting to {TCP_HOST}:{TCP_PORT} …")
            sock = connect_once(TCP_HOST, TCP_PORT)
            log("connected; streaming notifications")
            backoff = 2.0
            try:
                for rpc_msg in iter_jsonrpc(sock):
                    rec = extract_record(rpc_msg)
                    if rec is None:
                        continue
                    ts = rec.get("signal_timestamp")
                    if isinstance(ts, int) and ts in seen:
                        continue
                    append_record(inbox, rec)
                    if isinstance(ts, int):
                        seen.add(ts)
                    log(f"appended message from {rec.get('from')} ts={ts}")
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            log("daemon closed the connection; reconnecting")
        except (ConnectionRefusedError, OSError) as e:
            log(f"connect/IO error: {e}; backing off {backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        except Exception as e:  # noqa: BLE001
            log(f"unexpected error: {e}; backing off {backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


if __name__ == "__main__":
    raise SystemExit(main())
