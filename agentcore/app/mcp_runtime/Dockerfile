# Governed Companion — AgentCore MCP-Runtime container (the /mcp face)
# ---------------------------------------------------------------------------
# The SECOND protocol face of the same engine: serves the AgentCore MCP contract (stateless
# streamable-HTTP /mcp) so a protocol-None Gateway + http-runtime target can front the governed
# engine directly. Same engine + gate as the /invocations image; the addition is the MCP SDK.
#   docker build -f agentcore/app/mcp_runtime/Dockerfile -t gac-mcp-runtime .

FROM public.ecr.aws/docker/library/python:3.12-slim

# The AgentCore MCP runtime serves streamable-HTTP on PORT 8000 at /mcp (the platform
# health-checks 8000 and manages the session id). Binding elsewhere fails the health check.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GAC_ENGINE_ROOT=/app/engine \
    GAC_MCP_PORT=8000

ENV AGENT_OBSERVABILITY_ENABLED=true \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    OTEL_RESOURCE_ATTRIBUTES=service.name=gac-mcp-runtime

WORKDIR /app

# 1) Deps first.
COPY agentcore/app/mcp_runtime/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 2) Copy the companion engine directly from the repo-root context (single source of truth).
COPY agents/            /app/engine/agents/
COPY governance/        /app/engine/governance/
COPY knowledge/         /app/engine/knowledge/
COPY PRINCIPLES.md      /app/engine/PRINCIPLES.md
COPY config.yaml.example /app/engine/config.yaml.example

# 3) The MCP entrypoint.
COPY agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py /app/runtime_mcp_entrypoint.py

EXPOSE 8000

# ── Non-root user (defense in depth; the port is > 1024) ─────────────────────
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# ── Container HEALTHCHECK — TCP-connect to the MCP port (/mcp is not a bare-GET 200) ─
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,socket,sys; s=socket.create_connection(('127.0.0.1', int(os.environ.get('GAC_MCP_PORT','8000'))), timeout=4); s.close(); sys.exit(0)"]

# Launch under the ADOT wrapper, then serve the stateless MCP app under uvicorn.
CMD ["opentelemetry-instrument", "python", "/app/runtime_mcp_entrypoint.py"]
