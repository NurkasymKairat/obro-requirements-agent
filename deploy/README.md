# Деплой веб-интерфейса (hack.obroai.com)

Схема: на сервере один общий прокси Caddy (`/opt/proxy`) и проекты в своих папках (`/opt/tzcontrol`).
Прокси и проекты видят друг друга через внешнюю сеть Docker `web`. Порты наружу публикует только прокси,
проекты подключаются к нему файлами `sites/*.caddy`.

```
/opt/proxy/                общий прокси
  compose.yaml             из deploy/proxy/compose.yaml
  Caddyfile                из deploy/proxy/Caddyfile (одна строка: import sites/*.caddy)
  .env                     хеши паролей проектов (не в git)
  sites/hack.caddy         из deploy/sites/hack.caddy
/opt/tzcontrol/            клон репозитория
  deploy/compose.yaml      сервис tzcontrol
  deploy/.env.prod         ключи модели (не в git)
```

Все команды выполняются на сервере (Ubuntu 24.04) от пользователя с правом запускать `docker`.

## 1. Первый запуск сервера

DNS: A-запись `hack.obroai.com` указывает на IP сервера. Порты 80 и 443 открыты (`sudo ufw allow 80,443/tcp`
и `sudo ufw allow 443/udp`, если включён ufw).

Docker (если ещё не стоит):

```
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # затем перелогиниться
```

Сеть и общий прокси:

```
docker network create web
sudo mkdir -p /opt/proxy/sites && sudo chown -R $USER /opt/proxy
```

Файлы прокси берутся из репозитория, поэтому сначала клонируем проект (шаг 2), затем:

```
cp /opt/tzcontrol/deploy/proxy/compose.yaml /opt/tzcontrol/deploy/proxy/Caddyfile /opt/proxy/
cp /opt/tzcontrol/deploy/proxy/.env.example /opt/proxy/.env
cp /opt/tzcontrol/deploy/sites/hack.caddy /opt/proxy/sites/
```

Пароль жюри — раздел 4. После этого:

```
cd /opt/proxy && docker compose up -d
```

Caddy сам получит сертификат Let's Encrypt для `hack.obroai.com` при первом запросе.

## 2. Деплой проекта

```
sudo mkdir -p /opt/tzcontrol && sudo chown $USER /opt/tzcontrol
git clone <адрес репозитория> /opt/tzcontrol
cd /opt/tzcontrol/deploy
cp .env.prod.example .env.prod
nano .env.prod                     # ключ модели: OPENAI_API_KEY или GEMINI_API_KEY, LLM_PROVIDER
APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
```

Проверка:

```
docker compose ps                  # STATUS: healthy
docker compose logs -f tzcontrol
curl -s https://hack.obroai.com/api/health
```

Без ключа можно поднять заглушку: `WEB_PIPELINE=stub` в `.env.prod` (проходы проигрываются из примера,
модель не вызывается).

Контейнер работает от непривилегированного пользователя, лимит памяти 512 МБ, порты не публикует.
Кеш ответов модели лежит в именованном томе `tzcontrol_cache` (`/data/cache` в контейнере) и переживает
пересборку: повторный прогон того же документа берёт ответы из кеша.

## 3. Обновление

```
cd /opt/tzcontrol && git pull && cd deploy && APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
```

Если изменился `deploy/sites/hack.caddy`:

```
cp /opt/tzcontrol/deploy/sites/hack.caddy /opt/proxy/sites/
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Старые образы: `docker image prune -f`.

## 4. Пароль жюри

Загрузка документа (`POST /api/runs`) закрыта логином `jury` и паролем; пример, просмотр результатов и
скачивание отчёта открыты. Интерфейс сам показывает поля логина и пароля перед загрузкой.

Хеш пароля:

```
docker run --rm caddy:2 caddy hash-password --plaintext 'пароль-для-жюри'
```

Вывод (строка вида `$2a$14$...`) вписать в `/opt/proxy/.env` **в одинарных кавычках** — в хеше есть знаки `$`,
кавычки отключают подстановку переменных в compose:

```
TZCONTROL_JURY_HASH='$2a$14$...'
```

Применить: переменные окружения читаются при старте контейнера, поэтому пересоздать прокси:

```
cd /opt/proxy && docker compose up -d --force-recreate
```

Проверка: `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://hack.obroai.com/api/runs` даёт `401`,
с `-u jury:пароль` и файлом (`-F file=@doc.md`) — `202`.

## 5. Второй проект на том же сервере

1. Сервис проекта подключить к сети `web` (как в `deploy/compose.yaml`: `networks: [web]`, `external: true`),
   порты не публиковать, имя сервиса уникальное, например `otherapp`.
2. Положить файл сайта в `/opt/proxy/sites/other.caddy`:

   ```
   other.obroai.com {
   	reverse_proxy otherapp:8080
   }
   ```

3. Если нужен пароль — своя переменная в `/opt/proxy/.env` (например `OTHERAPP_HASH='...'`) и
   `docker compose up -d --force-recreate` в `/opt/proxy`; без новых переменных достаточно
   `docker exec caddy caddy reload --config /etc/caddy/Caddyfile`.
4. DNS: A-запись нового домена на тот же IP.

## Что где настраивается

| Файл | Что |
| :--- | :--- |
| `deploy/Dockerfile` | сборка: фронт (node) → python:3.12-slim, uvicorn на 0.0.0.0:8000, HEALTHCHECK на `/api/health` |
| `deploy/compose.yaml` | сервис `tzcontrol`: `.env.prod`, сеть `web`, 512 МБ, том кеша |
| `deploy/.env.prod.example` | переменные: провайдер, ключи, модели, `WEB_TITLE`, `CACHE_DIR`, `WEB_PIPELINE` |
| `deploy/proxy/` | общий Caddy: порты 80/443, тома данных и конфига, `import sites/*.caddy` |
| `deploy/sites/hack.caddy` | домен, прокси на `tzcontrol:8000` (read_timeout 600 с), тело до 10 МБ, пароль на загрузку |

Версия в паспорте прогона: в образе нет `.git` (`.dockerignore`), поэтому коммит передаётся при сборке —
`APP_COMMIT=$(git rev-parse --short HEAD)` перед `docker compose up -d --build` (build arg → ENV `APP_COMMIT`).
Версия получается `0.1.0+<коммит>`; без `APP_COMMIT` — просто `0.1.0`.
