#!/usr/bin/env bash
# Put the service somewhere a browser can reach it.
#
# Two destinations, one image. `local` runs the container on this machine, which is
# what a laptop-hosted demonstration needs; `remote` copies it to a host over SSH
# and runs it there. Both end by asking /api/health whether the thing actually came
# up, because "the command exited 0" and "the service answers" are different claims.
#
#   scripts/deploy.sh local
#   scripts/deploy.sh remote user@host
#
# The tunnel is deliberately not started here. It prints a URL that has to be read
# and passed on, and burying that in a deploy script is how a link gets lost.
# docs/deploy.md has the one command.

set -euo pipefail

IMAGE="${IMAGE:-cosmo-net}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
PORT="${PORT:-8000}"
CONTAINER="${CONTAINER:-cosmo-net}"

usage() {
    echo "usage: $0 local | remote <user@host>" >&2
    exit 2
}

wait_for_health() {
    local base="$1"
    for _ in $(seq 1 30); do
        if curl -fsS "${base}/api/health" >/dev/null 2>&1; then
            echo "healthy: ${base}/api/health"
            curl -fsS "${base}/api/health"
            echo
            return 0
        fi
        sleep 1
    done
    echo "no answer from ${base}/api/health after 30 s" >&2
    return 1
}

build() {
    echo "building ${IMAGE}:${TAG}"
    docker build -t "${IMAGE}:${TAG}" -t "${IMAGE}:latest" .
}

run_local() {
    build
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true

    # The variant database lives on a named volume, so saved designs survive a
    # rebuild. Without it they would last exactly as long as the container.
    docker run -d \
        --name "${CONTAINER}" \
        --restart unless-stopped \
        -p "${PORT}:8000" \
        -v cosmo-net-data:/app/state \
        -e COSMO_NET_DB=/app/state/runs.sqlite3 \
        "${IMAGE}:${TAG}" >/dev/null

    wait_for_health "http://127.0.0.1:${PORT}"
}

run_remote() {
    local target="$1"
    build

    echo "shipping the image to ${target}"
    docker save "${IMAGE}:${TAG}" | gzip | ssh "${target}" 'gunzip | docker load'

    ssh "${target}" bash -s <<EOF
set -euo pipefail
docker rm -f ${CONTAINER} >/dev/null 2>&1 || true
docker run -d \
    --name ${CONTAINER} \
    --restart unless-stopped \
    -p ${PORT}:8000 \
    -v cosmo-net-data:/app/state \
    -e COSMO_NET_DB=/app/state/runs.sqlite3 \
    ${IMAGE}:${TAG} >/dev/null
EOF

    # Checked from here rather than on the host: what matters is that the service
    # answers from outside, which is the thing a remote deploy can get wrong.
    local host="${target#*@}"
    wait_for_health "http://${host}:${PORT}"
}

case "${1:-}" in
    local) run_local ;;
    remote) [ $# -eq 2 ] || usage; run_remote "$2" ;;
    *) usage ;;
esac
