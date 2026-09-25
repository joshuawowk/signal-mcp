# signal-mcp: MCP server + inbox subscriber that fronts signal-cli's
# HTTP JSON-RPC daemon. Runs as a single long-lived container; the
# subscriber is the foreground process, the MCP server is invoked
# per-session via `docker exec -i ... python3 /app/server.py`.
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/joshuawowk/signal-mcp" \
      org.opencontainers.image.description="MCP server + inbox subscriber fronting a signal-cli JSON-RPC daemon" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tini procps \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app/ /app/

RUN mkdir -p /data && chmod 0777 /data

# Subscriber is PID 1; MCP server runs ad-hoc via docker exec.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/subscriber.py"]
