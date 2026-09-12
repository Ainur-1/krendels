# Как развернуть сервис на сервере

Сервис живёт на **http://161.104.53.181:8000/** и обновляется сам: каждый пуш в ветку `main` собирает образ в CI, кладёт его в реестр контейнеров и перезапускает контейнер на сервере. Ручной деплой нужен только для первого запуска сервера или когда нужно сделать это без CI.

## 1. Автоматический деплой (обычный путь)

Всё делает пайплайн `.github/workflows/pipeline.yml`:

1. **backend + frontend** — тесты, линтер, сверка отчётов с эталоном; падают — пайплайн дальше не идёт.
2. **image** — собирает образ и пушит в GitHub Container Registry: `ghcr.io/ainur-1/krendels:latest` и с тегом хеша коммита.
3. **deploy** — по SSH заходит на сервер, делает `docker pull`, пересоздаёт контейнер `krendels` и проверяет `/api/health`.

Доступ по SSH CI получает секретом `DEPLOY_SSH_KEY` (приватный ключ), а публичная половина лежит в `/root/.ssh/authorized_keys` на сервере.

Ничего делать не нужно: запушили в `main` — через пару минут сервис уже обновлён.

## 2. Первый запуск сервера

Сервер Ubuntu, на нём ставится Docker из официального репозитория (в комплекте `buildx`).

```bash
apt update && apt upgrade -y && apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
```

### Зеркало реестра — обязательный шаг

Docker Hub не отдаёт слои на российские адреса: запрос манифеста проходит, а скачивание встаёт намертво. Это проверено на практике, а не предположение. Пропишите зеркала, иначе сборка и даже pull зависнут без сообщения об ошибке:

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://dockerhub.timeweb.cloud",
    "https://dockerhub1.beget.com",
    "https://mirror.gcr.io"
  ]
}
EOF
systemctl restart docker
```

Проверить, что зеркала подхватились:

```bash
docker info --format '{{json .RegistryConfig.Mirrors}}'
```

### Добавить ключ для CI

Публичный ключ, соответствующая приватной половине которого лежит в секрете `DEPLOY_SSH_KEY` репозитория:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
cat >> /root/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAA… ci-deploy
EOF
chmod 600 /root/.ssh/authorized_keys
```

## 3. Ручной запуск без CI

Когда нужно поднять сервис, не дожидаясь пайплайна (или CI недоступен). Образ берётся из реестра:

```bash
docker pull ghcr.io/ainur-1/krendels:latest
docker rm -f krendels || true
docker run -d \
  --name krendels \
  --restart unless-stopped \
  -p 8000:8000 \
  -v cosmo-net-data:/app/state \
  ghcr.io/ainur-1/krendels:latest
```

Что здесь важно:

- `--restart unless-stopped` — контейнер поднимается сам после перезагрузки сервера и после падения.
- `-p 8000:8000` — сервис на порту 8000, жюри открывает `http://СЕРВЕР:8000`.
- `-v cosmo-net-data:/app/state` — сохранённые варианты переживают пересборку. **Том нужен обязательно:** без него контейнер работает, но варианты живут ровно столько, сколько контейнер.

## 4. Проверить, что всё поднялось

```bash
curl -s http://localhost:8000/api/health
docker inspect --format '{{.State.Health.Status}}' krendels
```

Первая команда должна ответить `"status":"ok"` и `"frontend_bundled":true`. Вторая — `healthy`: в образ встроена проверка, которая раз в полминуты сама дёргает `/api/health`.

Если снаружи не отвечает, а на сервере отвечает — дело в сетевом экране: откройте входящий трафик на порт 8000.

## 5. Что делать, если что-то пошло не так

```bash
docker logs --tail 100 krendels      # что пишет сервис
docker stats --no-stream krendels    # сколько ест памяти и процессора
docker restart krendels              # перезапустить
free -h                              # осталась ли память на сервере
```

Отдельно про память. Подбор конфигурации — самая тяжёлая операция; она сама определяет, сколько процессов может себе позволить, читая ограничения контейнера:

```bash
curl -s http://localhost:8000/api/health | grep -o '"sweep_workers":[0-9]*'
```

На сервере с двумя гигабайтами должно получиться больше одного. Если единица — памяти меньше, чем заказывали.

## 6. Переезд на GitVerse

Пока репозиторий живёт на GitHub, деплой работает целиком на GitHub Actions и GHCR. Если по регламенту проект переезжает на GitVerse, следующие части деплоя **сломаются как есть** и их нужно перенести:

1. **CI**. Файл `.github/workflows/pipeline.yml` на GitVerse не выполняется: GitVerse ждёт workflow в `.gitverse/workflows/`. Нужно портировать сборку и тесты под синтаксис GitVerse (YAML в `.gitverse/workflows/`, облачные раннеры).
2. **Реестр образов.** GitVerse даёт свой контейнерный реестр `gitverse.ru/<владелец>/<пакет>` (`docker login` + `push`). Образ придётся класть туда, а не в `ghcr.io`, и с сервера делать `docker pull gitverse.ru/...`.
3. **Секрет `DEPLOY_SSH_KEY`.** Приватный ключ и любые токены переносятся в секреты GitVerse; публичная половина ключа на сервере остаётся та же.
4. **Доступ к Docker Hub.** Зеркала из раздела 2 остаются нужными и после переезда: GitVerse-реестр содержит только наш образ, базовые же образы (`python`, `node`) CI и сервер по-прежнему тянут с Docker Hub.

Готовое решение для GitVerse: один workflow, который собирает образ, пушит в `gitverse.ru/...` и по SSH перезапускает контейнер на сервере — по структуре он повторяет `.github/workflows/pipeline.yml`, меняются только путь файла, адрес реестра и секреты.