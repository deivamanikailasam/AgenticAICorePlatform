# One image recipe for every Project A service: `--build-arg PACKAGE=mcp-gateway`.
FROM docker.io/library/python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ARG PACKAGE
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock platform.yaml ./
COPY policies/openfga/relations.yaml ./policies/openfga/relations.yaml
COPY libs ./libs
COPY services ./services
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package "${PACKAGE}"
RUN useradd --uid 10001 --no-create-home app && mkdir -p /var/lib/agent && chown 10001 /var/lib/agent
USER 10001
ENV PATH="/app/.venv/bin:${PATH}" AGENT_PLATFORM_CONFIG=/app/platform.yaml
