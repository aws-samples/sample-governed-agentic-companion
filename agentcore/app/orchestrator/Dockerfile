# Governed Companion — AgentCore Runtime container (/invocations face)
# ---------------------------------------------------------------------------
# Packages the companion engine + the thin runtime entrypoint. The Governance Gate ships
# INSIDE this image and gates every response by construction (Tenets 1/3/4/6/11/12/13).
#
# BUILD CONTEXT = THE REPOSITORY ROOT (single source of truth). This Dockerfile COPYs the
# engine directly from the repo root; .dockerignore keeps .git/.venv/outputs out of the context.
#   docker build -f agentcore/app/orchestrator/Dockerfile -t gac-orchestrator .

FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GAC_ENGINE_ROOT=/app/engine \
    PORT=8080

# ── Observability (BYO-container: we launch under the ADOT wrapper ourselves) ─
ENV AGENT_OBSERVABILITY_ENABLED=true \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    OTEL_RESOURCE_ATTRIBUTES=service.name=gac-orchestrator

WORKDIR /app

# 1) Deps first (better layer caching).
COPY agentcore/app/orchestrator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 2) Copy the companion engine directly from the repo-root context (single source of truth).
COPY agents/            /app/engine/agents/
COPY governance/        /app/engine/governance/
COPY knowledge/         /app/engine/knowledge/
COPY PRINCIPLES.md      /app/engine/PRINCIPLES.md
COPY config.yaml.example /app/engine/config.yaml.example

# 3) The thin runtime entrypoint.
COPY agentcore/app/orchestrator/runtime_entrypoint.py /app/runtime_entrypoint.py

EXPOSE 8080

# ── Non-root user (defense in depth; the port is > 1024) ─────────────────────
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# ── Container HEALTHCHECK (BedrockAgentCoreApp serves /ping on $PORT) ─────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/ping' % os.environ.get('PORT','8080'), timeout=4).status==200 else 1)"]

# Launch under the ADOT wrapper so custom + auto-instrumented OTEL export to CloudWatch.
CMD ["opentelemetry-instrument", "python", "/app/runtime_entrypoint.py"]
