# Деплой

Образ собирается в GitHub Actions и публикуется в ghcr, на сервере он только
скачивается. Собирать на прод-сервере нельзя: там 1.9 ГБ RAM без swap, а
`poetry install` на Alpine компилирует pillow и gevent из исходников и уходит
в OOM.

- Прод-сервер: `86.107.45.177`, каталог `/opt/inventify`, домен `back-kaynar.kz`
- Прод-ветка: `in-8`
- Образ: `ghcr.io/djars500/inventify`
- Сборка: `.github/workflows/build-image.yml`

## Обычный деплой

Пуш в `in-8` запускает сборку. Дождитесь зелёной галочки во вкладке Actions —
workflow не только собирает образ, но и проверяет, что приложение в нём
поднимается (`manage.py check`). Если проверка упала, образ не публикуется.

Дальше на сервере:

```bash
cd /opt/inventify
make deploy
```

`make deploy` делает `git pull`, тянет образ, поднимает контейнеры, применяет
миграции, собирает статику и чистит мусор. То же самое вручную:

```bash
cd /opt/inventify
git pull
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec web poetry run python manage.py migrate
docker compose -f docker-compose.prod.yml exec web poetry run python manage.py collectstatic --noinput
```

Проверка после выкатки:

```bash
docker compose -f docker-compose.prod.yml logs --tail=20 web
curl -sI https://back-kaynar.kz/api/category/ | head -3
free -h
```

В логах нужен `Booting worker` без `Exception in worker process`.

## Откат

У каждой сборки два тега: подвижный по имени ветки (`in-8`) и неизменяемый по
коммиту (`sha-<полный хеш>`). Для отката пропишите нужный тег в `.env`:

```bash
cd /opt/inventify
echo 'INVENTIFY_IMAGE=ghcr.io/djars500/inventify:sha-<хеш>' >> .env
docker compose -f docker-compose.prod.yml up -d
```

Хеш видно в описании коммита на GitHub или в выводе workflow. Уже скачанные
образы:

```bash
make rollback-list
```

Вернуться к обычному режиму — убрать строку `INVENTIFY_IMAGE` из `.env` и
повторить `up -d`.

## Разовая настройка

### Доступ к образу

После первой сборки пакет в ghcr создаётся приватным. Проще всего сделать его
публичным — репозиторий и так открыт, и тогда серверу не нужна авторизация:

**GitHub → вкладка Packages → inventify → Package settings → Danger Zone →
Change visibility → Public.**

Если пакет решено оставить приватным, на сервере нужно один раз войти в реестр
токеном с правом `read:packages`:

```bash
echo '<токен>' | docker login ghcr.io -u <логин> --password-stdin
```

### Что уже настроено

- `docker-compose.prod.yml` — без секции `build`, чтобы на сервере нельзя было
  случайно запустить сборку
- `Dockerfile` — ставит зависимости строго по `poetry.lock` с закреплённой
  версией poetry, иначе версии в образе расходятся с локом
- `.dockerignore` — не пускает `.env` в образ (он публичный) и выкидывает
  `.git`, `media`, `staticfiles`, логи

## Если сборка упала

Смотрите шаг, на котором остановился workflow:

- **Собрать образ** — проблема в `Dockerfile` или зависимостях. Частый случай:
  `poetry.lock` разошёлся с `pyproject.toml`, лечится `poetry lock` локально.
- **Проверить, что приложение поднимается** — образ собрался, но Django не
  стартует: битый импорт, несовместимая версия библиотеки, ошибка в настройках.
  Прод при этом не затронут, старый образ продолжает работать.
- **Опубликовать** — нет прав на пакет; проверьте `permissions: packages: write`
  в workflow.
