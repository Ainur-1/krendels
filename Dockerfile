# syntax=docker/dockerfile:1

# Three stages, and the running image inherits from none of them directly: Node is
# needed to build the interface and has no business being in a container that
# answers requests, and uv is 50 MB of build tooling with the same problem.

# --- the interface ------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build

# package.json and the lockfile first, so editing a component does not reinstall
# 170 packages. `npm ci` installs exactly the lockfile rather than resolving again.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Vite is configured to write into the Python package, which does not exist in this
# stage - so the output is redirected here and copied into place by the last stage.
RUN npx vite build --outDir dist --emptyOutDir

# --- the dependencies ---------------------------------------------------------
FROM python:3.12-slim AS builder

# Pinned to the version that produced uv.lock, so the image resolves nothing and
# installs exactly what the tests ran against.
COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Only the two files that pin the tree are copied, and --no-install-project keeps
# the package itself out. That is what makes this layer independent of the source:
# editing anything under src/ cannot invalidate it.
#
# The core set only - no pytest, no ruff, no httpx. Nothing in the dev extra is
# reachable from a request.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src/ ./src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# --- what actually runs -------------------------------------------------------
FROM python:3.12-slim AS runtime

# The service writes the variant database and nothing else, so it does not need to
# own its code. Running as root in a container that parses uploaded JSON from the
# open internet is a gift nobody has to give.
RUN useradd --create-home --uid 10001 cosmo

WORKDIR /app

COPY --from=builder --chown=cosmo:cosmo /app/.venv /app/.venv
COPY --from=builder --chown=cosmo:cosmo /app/src /app/src
COPY --from=frontend --chown=cosmo:cosmo /build/dist /app/src/cosmo_net/serving/static

# The four supplied scenarios are served from /api/scenarios, so they are part of
# the application rather than data mounted alongside it.
COPY --chown=cosmo:cosmo data/ /app/data/

# Saved variants live here. The directory is created owned by the service user so
# that a named volume mounted over it inherits that ownership rather than arriving
# root-owned and unwritable. Without a volume the designs last as long as the
# container, which is still long enough for a demonstration.
RUN install -d -o cosmo -g cosmo /app/state

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COSMO_NET_DB=/app/state/runs.sqlite3

USER cosmo
EXPOSE 8000

# --proxy-headers so that behind a reverse proxy the service sees the real scheme
# and client address rather than the proxy's.
CMD ["uvicorn", "cosmo_net.serving.api:app", \
     "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
