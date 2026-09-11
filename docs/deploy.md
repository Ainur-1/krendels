# Как развернуть сервис на сервере

Инструкция под Timeweb Cloud, но подойдёт любому серверу с Ubuntu. Всё вместе занимает около двадцати минут, из них половина — ожидание сборки.

Сервер собирает образ сам, из публичного репозитория. Заливать образ со своей машины не нужно: это 360 мегабайт, а по домашнему каналу это долго и ненадёжно.

## 1. Заказать сервер

В панели Timeweb Cloud создайте облачный сервер со следующими параметрами:

| параметр | значение | почему так |
|---|---|---|
| образ | Ubuntu 24.04 | свежий и с обычным набором пакетов |
| конфигурация | 2 ядра, 2 ГБ памяти | на 1 ГБ подбор конфигурации считается в один процесс и идёт минуту |
| диск | от 20 ГБ | образ со всеми слоями занимает меньше двух гигабайт |
| регион | Москва | жюри российское, задержка меньше |
| оплата | почасовая | трое суток обойдутся примерно в сотню рублей вместо месячного тарифа |

При создании добавьте свой SSH-ключ — так не придётся возиться с паролем. Если ключа ещё нет, создайте его на своей машине:

```bash
ssh-keygen -t ed25519 -C "cosmo-net" -f ~/.ssh/cosmonet
```

Публичную часть (`~/.ssh/cosmonet.pub`) вставьте в панели при создании сервера.

После создания панель покажет IP-адрес. Дальше он подставляется вместо `СЕРВЕР`.

## 2. Подключиться и подготовить систему

```bash
ssh -i ~/.ssh/cosmonet root@СЕРВЕР
```

Обновите пакеты и поставьте то, что понадобится:

```bash
apt update && apt upgrade -y && apt install -y ca-certificates curl git
```

## 3. Поставить Docker

Ставим из официального репозитория Docker — так в комплекте идёт `buildx`, который нужен нашей сборке.

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
```

### Зеркало реестра — обязательный шаг

Docker Hub не отдаёт слои на российские адреса: запрос манифеста проходит, а скачивание встаёт намертво. Это проверено на практике, а не предположение. Пропишите зеркала, иначе сборка зависнет без сообщения об ошибке:

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

## 4. Собрать и запустить сервис

```bash
git clone https://github.com/Ainur-1/krendels.git /opt/cosmo-net
cd /opt/cosmo-net
docker build -t cosmo-net:latest .
```

Сборка занимает пять-десять минут: она качает базовые образы, ставит зависимости Python и собирает интерфейс.

Запуск:

```bash
docker run -d \
  --name cosmo-net \
  --restart unless-stopped \
  -p 80:8000 \
  -v cosmo-net-data:/app/state \
  cosmo-net:latest
```

Что здесь важно:

- `--restart unless-stopped` — контейнер поднимается сам после перезагрузки сервера и после падения. Это половина ответа на вопрос «надёжно ли».
- `-p 80:8000` — сервис на обычном порту, жюри открывает `http://СЕРВЕР` без двоеточия и номера.
- `-v cosmo-net-data:/app/state` — сохранённые варианты переживают пересборку. Без тома они живут ровно столько, сколько контейнер.

## 5. Проверить, что всё поднялось

```bash
curl -s http://localhost/api/health
docker inspect --format '{{.State.Health.Status}}' cosmo-net
```

Первая команда должна ответить `"status":"ok"` и `"frontend_bundled":true`. Вторая — `healthy`: в образ встроена проверка, которая раз в полминуты сама дёргает `/api/health`, так что Docker знает о состоянии сервиса, а не только о том, что процесс запущен.

Со своей машины:

```bash
curl -s http://СЕРВЕР/api/health
```

Если не отвечает, а на сервере отвечает — дело в сетевом экране. В панели Timeweb откройте входящий трафик на порт 80.

## 6. Обновление после изменений в коде

```bash
ssh -i ~/.ssh/cosmonet root@СЕРВЕР
cd /opt/cosmo-net && git pull
docker build -t cosmo-net:latest .
docker rm -f cosmo-net
docker run -d --name cosmo-net --restart unless-stopped -p 80:8000 -v cosmo-net-data:/app/state cosmo-net:latest
curl -s http://localhost/api/health
```

Сохранённые варианты при этом не теряются — они на томе.

## 7. Что делать, если что-то пошло не так

```bash
docker logs --tail 100 cosmo-net      # что пишет сервис
docker stats --no-stream cosmo-net    # сколько ест памяти и процессора
docker restart cosmo-net              # перезапустить
free -h                               # осталась ли память на сервере
```

Отдельно про память. Подбор конфигурации — самая тяжёлая операция; она сама определяет, сколько процессов может себе позволить, читая ограничения контейнера. Проверить, что она решила:

```bash
curl -s http://localhost/api/health | grep -o '"sweep_workers":[0-9]*'
```

На сервере с двумя гигабайтами должно получиться больше одного. Если единица — памяти меньше, чем заказывали.

## 8. HTTPS, если понадобится

По протоколу достаточно, чтобы сервис открывался в браузере, и обычного `http://СЕРВЕР` для этого хватает: страница не тянет ничего извне, поэтому предупреждений о смешанном содержимом не будет.

Если всё же хочется замок в адресной строке, самый короткий путь — Caddy: он сам получит сертификат. Домен для этого нужен, но можно обойтись сервисом вида `nip.io`, который превращает IP в имя.

```bash
docker rm -f cosmo-net
docker run -d --name cosmo-net --restart unless-stopped \
  -v cosmo-net-data:/app/state cosmo-net:latest

docker run -d --name caddy --restart unless-stopped \
  -p 80:80 -p 443:443 \
  --link cosmo-net \
  -v caddy-data:/data \
  caddy:2 caddy reverse-proxy --from СЕРВЕР.nip.io --to cosmo-net:8000
```

После этого сервис открывается по `https://СЕРВЕР.nip.io`.

## 9. Чтобы сервис не заснул

На Timeweb сервер не засыпает — это особенность бесплатных платформ вроде Render, а не арендованного сервера. Дополнительно ничего настраивать не нужно.

Единственное, что стоит сделать перед сдачей, — открыть ссылку с постороннего устройства, через мобильный интернет. Это проверяет и сетевой экран, и то, что сервис виден снаружи, а не только из вашей сети.
