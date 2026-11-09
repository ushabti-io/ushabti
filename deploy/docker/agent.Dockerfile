# syntax=docker/dockerfile:1

# Image for one ushabti (one agent instance).
#
# Build from the repository root:
#   docker build -f deploy/docker/agent.Dockerfile -t ushabti-agent:dev .
#
# IMAGE is the name the image is published under.

# The image tags are written out in full, not built from arguments, so that
# Dependabot can see and update them. Keep the two python lines the same. The
# workflows read the uv version from the line below, and CI checks that both
# Dockerfiles name the same images.
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# Build stage: resolve and install into a virtual environment with uv.
FROM python:3.14-slim AS build

COPY --from=uv /uv /bin/uv

# Extras of ushabti-agent to install. The default covers both model providers.
# Add kubernetes, proxmox, or prometheus once those implements are built.
ARG EXTRAS="anthropic openai"

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first, from the lock file alone, so this layer is rebuilt only
# when the lock changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/ushabti/pyproject.toml,target=packages/ushabti/pyproject.toml \
    --mount=type=bind,source=packages/ushabti-agent/pyproject.toml,target=packages/ushabti-agent/pyproject.toml \
    --mount=type=bind,source=packages/ushabti-overseer/pyproject.toml,target=packages/ushabti-overseer/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace --package ushabti-agent \
        $(for extra in ${EXTRAS}; do printf -- '--extra %s ' "${extra}"; done)

# Then the workspace packages themselves, as real installs and not editable
# links, so the runtime stage needs nothing but the virtual environment.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package ushabti-agent \
        $(for extra in ${EXTRAS}; do printf -- '--extra %s ' "${extra}"; done)

# Runtime stage: the slim image, the virtual environment, and a non-root user.
FROM python:3.14-slim AS runtime

ARG IMAGE=ghcr.io/ushabti-io/ushabti-agent

LABEL org.opencontainers.image.title="ushabti-agent" \
      org.opencontainers.image.description="One ushabti: an agent that tends infrastructure under a spell." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/ushabti-io/ushabti" \
      org.opencontainers.image.ref.name="${IMAGE}"

# The only writable path is the Papyrus directory, so the container runs with
# a read-only root filesystem. Mount a volume there to keep the audit log.
RUN groupadd --system --gid 10001 ushabti \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --no-create-home ushabti \
    && install -d -o 10001 -g 10001 -m 0750 /var/lib/ushabti \
    && install -d -m 0755 /etc/ushabti

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    USHABTI_HOST=0.0.0.0 \
    USHABTI_PORT=8080 \
    USHABTI_LOG_FORMAT=json

USER 10001:10001
WORKDIR /var/lib/ushabti
VOLUME ["/var/lib/ushabti"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('USHABTI_PORT', '8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["ushabti"]
CMD ["run", "--spell", "/etc/ushabti/spell.yaml"]
