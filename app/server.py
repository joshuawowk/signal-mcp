"""signal-mcp: MCP server that lets Claude ping Josh on Signal.

Communicates with the signal-cli HTTP JSON-RPC daemon already running
on the host. Stdio transport — invoked per Claude session via
`docker exec -i signal-mcp python3 /app/server.py`.

Tools exposed:
  signal_send_message          - one-shot DM
  signal_send_question         - blocking question, optional numbered choices
  signal_send_permission_request - permission ask with action + context
  signal_check_replies         - read recent inbound messages (from subscriber)
  signal_health                - daemon version + recipient sanity
"""
from __future__ import annotations
import ast
import json
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

import rpc
from config import (
    SIGNAL_INBOX_PATH,
    default_recipient,
    require_sender,
)

SIGNAL_TCP_HOST = os.environ.get("SIGNAL_TCP_HOST", "127.0.0.1")
SIGNAL_TCP_PORT = int(os.environ.get("SIGNAL_TCP_PORT", "7583"))


def _ensure_inbox() -> Path:
    """Guarantee the inbox file exists so callers never see a missing
    file as a false 'subscriber down' signal. Cheap and idempotent."""
    path = Path(SIGNAL_INBOX_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
    except OSError:
        pass  # health/check tools degrade gracefully if /data is read-only
    return path


def _coerce_options(options: Any) -> list[str]:
    """Normalize the `options` argument into a list of strings.

    MCP clients (and the harness) sometimes serialize a list argument
    into a single string before it reaches the server. Accept any of:
      - a real list/tuple            -> [str(x) for x in it]
      - a JSON array string          -> '["a","b"]'
      - a Python-repr list string    -> "['a', 'b']"
      - a newline-separated string   -> "a\\nb\\nc"
      - a comma-separated string     -> "a, b, c"
      - a single bare string         -> ["the string"]
    Empty/whitespace entries are dropped. Returns [] for None/empty.
    """
    if options is None:
        return []
    if isinstance(options, (list, tuple)):
        return [str(x).strip() for x in options if str(x).strip()]
    if not isinstance(options, str):
        return [str(options).strip()] if str(options).strip() else []
    s = options.strip()
    if not s:
        return []
    # Try structured parses first (JSON, then Python literal).
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(s)
            if isinstance(parsed, (list, tuple)):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (ValueError, SyntaxError):
            pass
    # Fall back to delimiter splitting: newlines win over commas.
    if "\n" in s:
        parts = s.split("\n")
    elif "," in s:
        parts = s.split(",")
    else:
        parts = [s]
    return [p.strip() for p in parts if p.strip()]

mcp = FastMCP(
    name="signal-josh",
    instructions=(
        "Use this server to message Josh on Signal. Default recipient is "
        "preconfigured; only pass `recipient` to override. Keep messages "
        "concise, include enough context that Josh can act without "
        "opening Claude, and prefix with the session_id if the caller "
        "provides one so replies can be matched."
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _receive_port_reachable(timeout: float = 2.0) -> bool:
    """True if the daemon's TCP receive endpoint accepts a connection.
    This is the channel the subscriber streams from; if it's down,
    replies won't flow even though sends (HTTP) still work."""
    try:
        with socket.create_connection(
            (SIGNAL_TCP_HOST, SIGNAL_TCP_PORT), timeout=timeout
        ):
            return True
    except OSError:
        return False


def _subscriber_running() -> bool:
    """True if a subscriber.py process is alive in this container.
    Scans /proc directly (no `ps` in the slim image)."""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\x00", b" ").decode(
                        "utf-8", "replace"
                    )
            except (OSError, FileNotFoundError):
                continue
            if "subscriber.py" in cmd:
                return True
    except OSError:
        pass
    return False


def _last_message_at() -> str | None:
    """received_at of the most recent inbox record, or None if empty."""
    path = Path(SIGNAL_INBOX_PATH)
    if not path.exists():
        return None
    last = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("received_at")
                if ts and (last is None or ts > last):
                    last = ts
    except OSError:
        return None
    return last


def _send_text(text: str, recipient: str | None) -> dict[str, Any]:
    """Send a text message and return a structured result.

    The host daemon is account-scoped (started with `-a +14013752241`),
    so the JSON-RPC payload does NOT include an `account` field — some
    signal-cli versions reject unrecognized params.
    """
    rcpt = recipient.strip() if recipient else default_recipient()
    sender = require_sender()  # validated but not in payload (see above)
    params = {
        "recipient": [rcpt],
        "message": text,
    }
    result = rpc.call("send", params)
    # signal-cli returns {"results": [...], "timestamp": ...} for send.
    timestamp = None
    if isinstance(result, dict):
        timestamp = result.get("timestamp")
    return {
        "ok": True,
        "recipient": rcpt,
        "sender": sender,
        "sent_at": _now_iso(),
        "signal_timestamp": timestamp,
        "preview": text[:120],
    }


@mcp.tool(
    annotations={
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def signal_send_message(
    text: str,
    recipient: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Send a plain Signal DM to Josh (or `recipient` if overridden).

    Use for task-completion pings and FYI updates that don't require a
    reply. Keep `text` under ~500 chars; include the task name and a
    one-line result. If the caller has a session_id, pass it so Josh
    can correlate the message back to this conversation.

    Args:
        text: The message body. Plain text.
        recipient: Optional E.164 number to override the default.
        session_id: Optional short tag prefixed to the message.
    """
    if not text or not text.strip():
        raise ValueError("text must be a non-empty string")
    body = f"[{session_id}] {text}" if session_id else text
    return _send_text(body, recipient)


@mcp.tool(
    annotations={
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def signal_send_question(
    question: str,
    options: list[str] | str | None = None,
    recipient: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Send a blocking question to Josh.

    Renders as: `QUESTION [session_id]\\n<question>\\n\\n1) opt1\\n2) opt2`.
    Josh replies with the number or free text; poll `signal_check_replies`
    afterwards to read the answer. Use when the agent genuinely cannot
    proceed without input.

    Args:
        question: The question text. Be specific; include enough context
            that Josh doesn't need to open Claude to answer.
        options: Optional choices, rendered as a numbered list. Accepts a
            real list (["A", "B"]) OR a string the client may have
            serialized: a JSON array '["A","B"]', or newline- or
            comma-separated text ("A\\nB" / "A, B"). All forms are
            normalized server-side, so it's safe whichever way the client
            passes it.
        recipient: Optional E.164 override.
        session_id: Optional short tag for correlating the reply.
    """
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")
    opts = _coerce_options(options)
    tag = f" [{session_id}]" if session_id else ""
    lines = [f"QUESTION{tag}", question.strip()]
    if opts:
        lines.append("")
        for i, opt in enumerate(opts, 1):
            lines.append(f"{i}) {opt}")
        lines.append("")
        lines.append("Reply with the number or text.")
    body = "\n".join(lines)
    result = _send_text(body, recipient)
    result["question_sent_at"] = result["sent_at"]
    result["expected_reply_via"] = "signal_check_replies"
    result["options_parsed"] = opts
    return result


@mcp.tool(
    annotations={
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def signal_send_permission_request(
    action: str,
    context: str,
    recipient: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Ask Josh to approve an action before the agent proceeds.

    Renders as: `PERMISSION [session_id]\\n<action>\\n\\nContext:\\n<context>\\n\\nReply YES / NO / details.`.
    Use for irreversible or high-impact operations: deletions, prod
    changes, spending money, sending external email, anything Josh
    would normally want to confirm.

    Args:
        action: One-line description of what the agent wants to do.
        context: Why, scope, and blast radius. Include enough detail
            that Josh can decide without asking follow-ups.
        recipient: Optional E.164 override.
        session_id: Optional short tag for correlating the reply.
    """
    if not action or not action.strip():
        raise ValueError("action must be a non-empty string")
    if not context or not context.strip():
        raise ValueError("context must be a non-empty string")
    tag = f" [{session_id}]" if session_id else ""
    body = (
        f"PERMISSION{tag}\n"
        f"{action.strip()}\n\n"
        f"Context:\n{context.strip()}\n\n"
        f"Reply YES / NO / details."
    )
    result = _send_text(body, recipient)
    result["permission_requested_at"] = result["sent_at"]
    result["expected_reply_via"] = "signal_check_replies"
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def signal_check_replies(
    since: str | None = None,
    from_recipient: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return recent Signal messages received by the bot account.

    Reads the inbox.jsonl populated by the subscriber sidecar. Filter
    by `since` (ISO timestamp), `from_recipient` (E.164), and/or
    `session_id` (matches messages whose body contains `[session_id]`).
    Results are newest-first.

    Args:
        since: Only return messages received at-or-after this ISO ts.
        from_recipient: Only return messages from this E.164 number.
        session_id: Only return messages whose body contains `[session_id]`.
        limit: Max messages to return (default 20).
    """
    # Ensure the file exists so an empty/pruned inbox reads as "no
    # replies yet" rather than a missing-file error. The subscriber may
    # legitimately have an empty inbox (no replies, or retention pruned
    # old ones) while running perfectly.
    path = _ensure_inbox()
    cutoff = None
    if since:
        try:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"`since` is not a valid ISO timestamp: {e}")
    rcpt_filter = from_recipient.strip() if from_recipient else None
    tag_filter = f"[{session_id}]" if session_id else None

    matches: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff:
                try:
                    ts = datetime.fromisoformat(
                        msg["received_at"].replace("Z", "+00:00")
                    )
                except (KeyError, ValueError):
                    continue
                if ts < cutoff:
                    continue
            if rcpt_filter and msg.get("from") != rcpt_filter:
                continue
            if tag_filter and tag_filter not in (msg.get("body") or ""):
                continue
            matches.append(msg)
    matches.sort(key=lambda m: m.get("received_at", ""), reverse=True)
    return {
        "ok": True,
        "count": len(matches[:limit]),
        "messages": matches[:limit],
        "receive_channel_ok": _receive_port_reachable(),
        "subscriber_running": _subscriber_running(),
    }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def signal_health() -> dict[str, Any]:
    """Full health check for the signal-mcp server. Call this first if
    any other tool is misbehaving.

    Reports three independent subsystems:
      - send path:    daemon_ok / daemon_version (HTTP JSON-RPC :8080)
      - receive path: receive_channel_ok (TCP :7583) + subscriber_running
      - inbox:        always ensured to exist; size + last_message_at

    `ok` is True only when BOTH send and receive paths are healthy. An
    empty inbox is normal and does NOT make the server unhealthy.
    """
    sender = require_sender()
    rcpt = default_recipient()
    try:
        version = rpc.ping()
        daemon_ok = True
        daemon_error = None
    except Exception as e:  # noqa: BLE001 - surfaced to the agent
        version = None
        daemon_ok = False
        daemon_error = str(e)

    inbox = _ensure_inbox()
    receive_ok = _receive_port_reachable()
    subscriber_ok = _subscriber_running()
    try:
        inbox_size = inbox.stat().st_size if inbox.exists() else 0
    except OSError:
        inbox_size = 0

    return {
        "ok": bool(daemon_ok and receive_ok and subscriber_ok),
        "send_path": {
            "daemon_ok": daemon_ok,
            "daemon_version": version,
            "daemon_error": daemon_error,
        },
        "receive_path": {
            "receive_channel_ok": receive_ok,
            "subscriber_running": subscriber_ok,
            "tcp_endpoint": f"{SIGNAL_TCP_HOST}:{SIGNAL_TCP_PORT}",
        },
        "inbox": {
            "path": str(inbox),
            "exists": inbox.exists(),
            "size_bytes": inbox_size,
            "last_message_at": _last_message_at(),
        },
        "sender": sender,
        "default_recipient": rcpt,
    }


if __name__ == "__main__":
    # stdio transport — Claude Desktop will spawn us via `docker exec -i`.
    mcp.run()
