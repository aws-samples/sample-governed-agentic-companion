# Governed Companion — AgentCore MCP-Runtime container (the /mcp face)
# ---------------------------------------------------------------------------
# The SECOND protocol face of the same engine: serves the AgentCore MCP contract (stateless
# streamable-HTTP /mcp) so a protocol_type=MCP Gateway + mcp_server target can front the governed
# engine directly. Same engine + gate as the /invocations image; the addition is the MCP SDK.
#   docker build -f agentcore/app/mcp_runtime/Dockerfile -t gac-mcp-runtime .
#
# Base image: Chainguard Python (Wolfi), pinned by multi-arch index digest. The build stages use
# the `latest-dev` variant (shell, apk, compiler); the runtime stage uses `latest` (no shell, no
# package manager, non-root uid 65532). Both digests are taken at the same refresh so the Python
# minor matches (the venv's home is /usr/bin). Refresh procedure: docs/DEPLOY.md, "Base image".
# ── Stage 1: the AWS Architecture Icons catalog (discarded after the build) ────
# The deployer stages the official AWS Architecture Icons Asset Package zip in
# agentcore/diagram-icons/ (gitignored; never committed; THIRD-PARTY-LICENSES.md). This stage
# turns it into a sanitized, checksummed catalog. No zip staged: the build continues with a
# WARNING and the diagram specialist declines in this image. A bad zip fails with FATAL:.
# cgr.dev/chainguard/python:latest-dev (refreshed 2026-11-03)
# pragma: allowlist nextline secret
FROM cgr.dev/chainguard/python@sha256:f543e40c72e83ef926f91dba626efa4fcc6ad997cd603fee07c038e4b6902fb7 AS diagram-assets
ARG GAC_DIAGRAM_ICONS_SHA256=""
USER root
RUN mkdir -p /out /build && chown 65532:65532 /out /build
USER 65532
WORKDIR /build
COPY agentcore/app/diagram-assets/requirements.txt /build/requirements.txt
RUN python -m venv /build/venv \
    && /build/venv/bin/pip install --no-cache-dir -r /build/requirements.txt
COPY scripts/build_icon_catalog.py /build/build_icon_catalog.py
COPY agentcore/diagram-icons/ /staging/
RUN /build/venv/bin/python /build/build_icon_catalog.py --staging /staging --out /out \
      --expected-sha256 "${GAC_DIAGRAM_ICONS_SHA256}"
# Hand the catalog to root: the runtime user can read it but not change it.
USER root
RUN chown -R 0:0 /out
USER 65532
# ── Stage 2: runtime dependencies (discarded; only the venv and two fonts are copied out) ─
# cgr.dev/chainguard/python:latest-dev (refreshed 2026-11-03)
# pragma: allowlist nextline secret
FROM cgr.dev/chainguard/python@sha256:f543e40c72e83ef926f91dba626efa4fcc6ad997cd603fee07c038e4b6902fb7 AS runtime-deps
USER root
# Pinned fonts for the in-process PNG rasterizer (resvg-py).
RUN apk add --no-cache ttf-dejavu=2.37-r9
WORKDIR /app
COPY agentcore/app/mcp_runtime/requirements.txt /app/requirements.txt
# The runtime never runs pip. Remove it after the install: pip vendors its own copies of
# urllib3, msgpack and pkg_resources, which carry fixable CVEs even in the latest pip.
RUN python -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir pip==26.2.2 \
    && /app/venv/bin/pip install --no-cache-dir -r /app/requirements.txt \
    && /app/venv/bin/python -m pip uninstall -y pip
# The only paths the runtime user may write: the engine's local audit, feedback and plan stores
# (agents/audit.py, agents/feedback.py, agents/staged.py). Everything else in /app stays root-owned.
RUN mkdir -p /stage/engine/.audit /stage/engine/.feedback /stage/engine/.plans \
    && chown 65532:65532 /stage/engine/.audit /stage/engine/.feedback /stage/engine/.plans
USER 65532
# ── Stage 3: the runtime image (no shell, no package manager) ─────────────────
# cgr.dev/chainguard/python:latest (refreshed 2026-11-03)
# pragma: allowlist nextline secret
FROM cgr.dev/chainguard/python@sha256:1cd5d9ebc11ac60b133b1fc6bbfa9631c5156cf6334902d721b1e87160be3f20
# The AgentCore MCP runtime serves streamable-HTTP on 0.0.0.0:8000 at /mcp, as the AgentCore MCP
# protocol contract requires. Stateless mode is this kit's design choice (stateful is also supported).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GAC_ENGINE_ROOT=/app/engine \
    PATH=/app/venv/bin:/usr/bin:/bin \
    GAC_MCP_PORT=8000
ENV AGENT_OBSERVABILITY_ENABLED=true \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    OTEL_RESOURCE_ATTRIBUTES=service.name=gac-mcp-runtime
# ── Diagram specialist: catalog location and the two fonts the rasterizer needs ─
ENV GAC_DIAGRAM_ASSETS_DIR=/app/engine/diagram_assets
WORKDIR /app
# Files are root-owned (COPY without a chown flag; COPY --from keeps the source owner), so
# the runtime user cannot modify the code, the venv or the catalog.
# 1) Dependencies (the venv built in runtime-deps; it has no pip).
COPY --from=runtime-deps /app/venv /app/venv
COPY --from=runtime-deps /usr/share/fonts/ttf-dejavu/DejaVuSans.ttf /usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf /usr/share/fonts/truetype/dejavu/
# 2) Copy the companion engine directly from the repo-root context (single source of truth).
COPY agents/            /app/engine/agents/
COPY governance/        /app/engine/governance/
COPY knowledge/         /app/engine/knowledge/
COPY PRINCIPLES.md      /app/engine/PRINCIPLES.md
COPY config.yaml.example /app/engine/config.yaml.example
COPY --from=diagram-assets /out/ /app/engine/diagram_assets/
# The writable state directories (owned by 65532 in runtime-deps).
COPY --from=runtime-deps /stage/engine/ /app/engine/
# 3) The MCP entrypoint.
COPY agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py /app/runtime_mcp_entrypoint.py
EXPOSE 8000
# ── Non-root user (the base's nonroot uid; defense in depth; the port is > 1024) ─
USER 65532
# ── Container HEALTHCHECK — TCP-connect to the MCP port (/mcp is not a bare-GET 200) ─
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,socket,sys; s=socket.create_connection(('127.0.0.1', int(os.environ.get('GAC_MCP_PORT','8000'))), timeout=4); s.close(); sys.exit(0)"]
# The base's default ENTRYPOINT is /usr/bin/python; clear it so CMD runs as written.
ENTRYPOINT []
# Launch under the ADOT wrapper, then serve the stateless MCP app under uvicorn.
CMD ["opentelemetry-instrument", "python", "/app/runtime_mcp_entrypoint.py"]
