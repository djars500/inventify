# Сборка в два этапа: компиляторы и -dev пакеты нужны только чтобы собрать
# psycopg2, pillow и gevent, в готовом образе им делать нечего. Раньше они
# занимали ~900 МБ из 1.36 ГБ — на прод-сервере это лишние минуты docker pull.

# ---------- этап сборки ----------
FROM python:3.11-alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # venv кладём в проект, чтобы скопировать его одной директорией,
    # а не искать хешированный путь в кеше poetry
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

WORKDIR /usr/src/app

# psycopg2 собирается из исходников: под musl нет готовых колёс manylinux
RUN apk add --no-cache \
    build-base gcc musl-dev python3-dev make \
    postgresql-dev jpeg-dev zlib-dev libffi-dev

# Версия poetry закреплена: формат lock-файла привязан к мажорной версии,
# а `pip install poetry` ставит последнюю и может отказаться читать лок.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "poetry==1.8.3"

# poetry.lock обязателен: без него poetry резолвит зависимости заново по
# диапазонам из pyproject.toml, и образ получает версии новее зафиксированных
# (так в прод уехал drf-api-logger 1.4.0 вместо 1.1.16 из лока).
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

# ---------- рабочий образ ----------
FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1 \
    PATH="/usr/src/app/.venv/bin:$PATH"

WORKDIR /usr/src/app

# Только динамические библиотеки, с которыми слинкованы собранные пакеты:
# libpq — psycopg2, libjpeg-turbo и zlib — pillow, libffi — cffi.
# Если чего-то не хватит, приложение упадёт на импорте — это ловит
# `manage.py check` в CI до публикации образа.
RUN apk add --no-cache libpq libjpeg-turbo zlib libffi \
    && pip install --no-cache-dir "poetry==1.8.3"

COPY --from=builder /usr/src/app/.venv ./.venv

COPY . .
