#!/usr/bin/env bash

# Разместить сервис там, где до него дотянется браузер.
#
# Один образ, два адресата:
#   - local  — контейнер на этой машине, для демонстрации с ноутбука;
#   - remote — образ пересылается на хост по SSH и запускается там.
#
# Оба режима завершаются вопросом к /api/health: «команда вернула ноль» и «сервис
# отвечает» — это не одно и то же. Туннель здесь не открывается — его адрес
# нужно прочитать и передать человеку, поэтому он один раз приведён в docs/deploy.md.

set -euo pipefail

readonly IMAGE="${IMAGE:-cosmo-net}"
readonly TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
readonly PORT="${PORT:-8000}"
readonly CONTAINER="${CONTAINER:-cosmo-net}"
readonly VOLUME="cosmo-net-data"

die() { echo "использование: $0 local | remote <user@host>" >&2; exit 2; }

# Дождаться, пока сервис начнёт отвечать: до тридцати попыток с шагом в секунду.
wait_health() {
    local base="$1"
    for _ in $(seq 1 30); do
        if curl -fsS "${base}/api/health" >/dev/null 2>&1; then
            echo "здоров: ${base}/api/health"
            curl -fsS "${base}/api/health"
            echo
            return 0
        fi
        sleep 1
    done
    echo "нет ответа от ${base}/api/health за 30 с" >&2
    return 1
}

build() {
    echo "сборка ${IMAGE}:${TAG}"
    docker build -t "${IMAGE}:${TAG}" -t "${IMAGE}:latest" .
}

# Сохранённые варианты живут на именованном томе и переживают пересборку;
# без тома они существуют ровно столько, сколько сам контейнер.
start_container() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    docker run -d \
        --name "${CONTAINER}" \
        --restart unless-stopped \
        -p "${PORT}:8000" \
        -v "${VOLUME}:/app/state" \
        -e COSMO_NET_DB=/app/state/runs.sqlite3 \
        "${IMAGE}:${TAG}" >/dev/null
}

deploy_local() {
    build
    start_container
    wait_health "http://127.0.0.1:${PORT}"
}

deploy_remote() {
    local target="$1"
    build

    echo "отправка образа на ${target}"
    docker save "${IMAGE}:${TAG}" | gzip | ssh "${target}" 'gunzip | docker load'

    # Собственно запуск выполняется на удалённом хосте; локальные переменные
    # подставляются в heredoc, поэтому путь к образу и порт приходят готовыми.
    ssh "${target}" bash -s <<EOF
set -euo pipefail
docker rm -f ${CONTAINER} >/dev/null 2>&1 || true
docker run -d \
    --name ${CONTAINER} \
    --restart unless-stopped \
    -p ${PORT}:8000 \
    -v ${VOLUME}:/app/state \
    -e COSMO_NET_DB=/app/state/runs.sqlite3 \
    ${IMAGE}:${TAG} >/dev/null
EOF

    # Проверка идёт отсюда, а не с хоста: удалённый деплой умеет ломать именно
    # внешнюю доступность, поэтому проверяется она.
    local host="${target#*@}"
    wait_health "http://${host}:${PORT}"
}

case "${1:-}" in
    local) deploy_local ;;
    remote)
        [ $# -eq 2 ] || die
        deploy_remote "$2"
        ;;
    *) die ;;
esac