# syntax=docker/dockerfile:1

# Три стадии, и работающий образ не наследует напрямую ни от одной: Node нужен, чтобы
# собрать интерфейс, и ему нечего делать в контейнере, который отвечает на запросы, а
# uv — это 50 МБ инструментов сборки с той же проблемой.

# --- интерфейс ------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build

# Сначала package.json и файл блокировки, чтобы правка компонента не приводила к
# переустановке 170 пакетов. `npm ci` ставит ровно то, что записано, ничего не
# разрешая заново.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Vite настроен писать в пакет Python, которого на этой стадии нет, поэтому вывод
# перенаправляется сюда, а на место его копирует последняя стадия.
RUN npx vite build --outDir dist --emptyOutDir

# --- зависимости ----------------------------------------------------------------
FROM python:3.12-slim AS builder

# Версия закреплена той, которой получен uv.lock: образ ничего не разрешает заново и
# ставит ровно то, на чём прогонялись тесты.
COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Копируются только два файла, закрепляющие дерево зависимостей, а --no-install-project
# оставляет сам пакет снаружи. Именно это делает слой независимым от исходников:
# правка чего угодно в src/ его не обесценивает.
#
# Ставится только основной набор — ни pytest, ни ruff, ни httpx. Ничто из
# дополнительного набора для разработки из запроса недостижимо.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src/ ./src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# --- то, что работает -----------------------------------------------------------
FROM python:3.12-slim AS runtime

# Сервис пишет только базу вариантов и больше ничего, поэтому владеть своим кодом ему
# не нужно. Работать под root в контейнере, который разбирает загруженный из интернета
# JSON, — подарок, который никто не обязан делать.
RUN useradd --create-home --uid 10001 cosmo

WORKDIR /app

COPY --from=builder --chown=cosmo:cosmo /app/.venv /app/.venv
COPY --from=builder --chown=cosmo:cosmo /app/src /app/src
COPY --from=frontend --chown=cosmo:cosmo /build/dist /app/src/cosmo_net/serving/static

# Четыре выданных сценария отдаются через /api/scenarios, поэтому они часть
# приложения, а не данные, примонтированные рядом.
COPY --chown=cosmo:cosmo data/ /app/data/

# Здесь живут сохранённые варианты. Каталог создаётся сразу от имени пользователя
# сервиса, чтобы именованный том, смонтированный поверх, унаследовал владельца, а не
# пришёл принадлежащим root и недоступным для записи. Без тома проекты живут столько
# же, сколько контейнер, — для демонстрации этого всё равно хватает.
RUN install -d -o cosmo -g cosmo /app/state

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COSMO_NET_DB=/app/state/runs.sqlite3

USER cosmo
EXPOSE 8000

# --proxy-headers нужен, чтобы за обратным прокси сервис видел настоящую схему и
# адрес клиента, а не адрес самого прокси.
CMD ["uvicorn", "cosmo_net.serving.api:app", \
     "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
