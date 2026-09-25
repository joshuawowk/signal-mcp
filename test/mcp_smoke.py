"""Smoke test: launch the MCP server via `docker exec`, run initialize +
tools/list, then exit. Reports per-tool annotations. Does NOT send any
Signal messages. Run from the host (needs the docker group):

    sg docker -c "python3 /home/jwowk/Repos/signal-mcp/test/mcp_smoke.py"
"""
import json, subprocess, sys

REQS = [
    {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "signal_health", "arguments": {}}},
]

p = subprocess.Popen(
    ["docker", "exec", "-i", "signal-mcp", "python3", "/app/server.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True,
)
payload = "".join(json.dumps(r) + "\n" for r in REQS)
try:
    out, err = p.communicate(input=payload, timeout=15)
except subprocess.TimeoutExpired:
    p.kill(); out, err = p.communicate()

print("=== stdout ===")
for line in out.splitlines():
    try:
        obj = json.loads(line)
        if "result" in obj and "tools" in obj.get("result", {}):
            print(f"tools/list -> {len(obj['result']['tools'])} tools:")
            for t in obj["result"]["tools"]:
                print(f"  - {t['name']}")
        elif "result" in obj:
            print(f"id={obj.get('id')} result: "
                  f"{json.dumps(obj['result'])[:300]}")
        elif "error" in obj:
            print(f"id={obj.get('id')} ERROR: {obj['error']}")
        else:
            print(json.dumps(obj)[:200])
    except json.JSONDecodeError:
        print(line[:200])

if err:
    print("=== stderr ===")
    print(err[:800])
