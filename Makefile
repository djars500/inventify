COMPOSE = docker compose
COMPOSE_PROD = docker compose -f docker-compose.prod.yml

# Имя образа выводится из git-remote: в ghcr пространство имён совпадает с
# владельцем репозитория, и запушить в чужое нельзя (permission_denied:
# create_package). Так имя само поедет за репозиторием при смене origin.
REPO_SLUG := $(shell git remote get-url origin | sed 's|\.git$$||' | tr ':' '/' | rev | cut -d/ -f1,2 | rev | tr '[:upper:]' '[:lower:]')
IMAGE ?= ghcr.io/$(REPO_SLUG)
BRANCH := $(shell git rev-parse --abbrev-ref HEAD)
SHA := $(shell git rev-parse HEAD)

# ============================================================
# Сборка образа — ЗАПУСКАТЬ НА СВОЕЙ МАШИНЕ, НЕ НА СЕРВЕРЕ
#
# Обычно образ собирает GitHub Actions. Эти цели — запасной путь, когда
# Actions недоступны. Сервер x86, а мак на Apple Silicon, поэтому сборка
# идёт через эмуляцию и занимает 20-40 минут.
#
# Другой реестр (например Docker Hub) задаётся переменной:
#   make image-release IMAGE=<логин>/inventify
# ============================================================

.PHONY: image-release
image-release:         ## собрать под сервер, проверить и запушить в реестр
	$(MAKE) image-build
	$(MAKE) image-check
	docker push $(IMAGE):$(BRANCH)
	docker push $(IMAGE):sha-$(SHA)
	@echo "Готово. На сервере: make deploy"
	@echo "Откат на этот коммит: INVENTIFY_IMAGE=$(IMAGE):sha-$(SHA)"

.PHONY: image-build
image-build:           ## собрать образ под linux/amd64 и оставить локально
	docker buildx build \
		--platform linux/amd64 \
		-t $(IMAGE):$(BRANCH) \
		-t $(IMAGE):sha-$(SHA) \
		--load .

# Та же проверка, что делает workflow перед публикацией: ловит образы,
# в которых приложение не поднимается, до того как они уедут на прод.
.PHONY: image-check
image-check:           ## проверить, что приложение в собранном образе стартует
	docker run --rm --platform linux/amd64 \
		-e SECRET_KEY=smoke-test -e DEBUG=0 \
		-e CELERY_BROKER_URL=redis://localhost:6379/0 \
		-e CELERY_CACHE_URL=redis://localhost:6379/1 \
		$(IMAGE):$(BRANCH) \
		poetry run python manage.py check
	docker run --rm --platform linux/amd64 \
		$(IMAGE):$(BRANCH) poetry run pip show drf-api-logger | head -2

# ============================================================
# Деплой на прод: подтянуть код и готовый образ, поднять, почистить
#
# На прод-сервере 1.9 ГБ RAM без swap — `docker compose build` там уходит
# в OOM, поэтому образ только скачивается.
# ============================================================

.PHONY: deploy
deploy:                ## git pull -> pull образа -> up -d -> migrate -> clean
	git pull
	$(COMPOSE_PROD) pull
	$(COMPOSE_PROD) up -d
	$(MAKE) migrate-prod
	$(MAKE) clean

.PHONY: migrate-prod
migrate-prod:
	$(COMPOSE_PROD) exec web poetry run python manage.py migrate
	$(COMPOSE_PROD) exec web poetry run python manage.py collectstatic --noinput

# Откат на предыдущий образ: посмотреть теги и запустить нужный
.PHONY: rollback-list
rollback-list:         ## показать скачанные образы приложения
	docker images $(IMAGE) --format '{{.Tag}}\t{{.CreatedSince}}'

# Безопасная чистка. Не трогает БД, фото (media_volume) и статику.
# Запускается автоматически после каждого deploy.
.PHONY: clean
clean:                 ## journald + висячие образы + build-кэш + apt-кэш
	-sudo journalctl --vacuum-size=300M
	# Без -a: удаляем только образы без тегов. С -a сносились и предыдущие
	# версии приложения, а они нужны для мгновенного отката (make rollback-list).
	-docker image prune -f
	-docker builder prune -f
	-sudo apt-get clean
	@echo "Чистка завершена (БД, фото, статика и образы для отката не затронуты)."

# Раз в несколько месяцев, когда старые теги накопятся и займут место
.PHONY: clean-all-images
clean-all-images:      ## удалить ВСЕ неиспользуемые образы, включая откатные
	docker image prune -a -f

# ============================================================
# Разовая настройка лимитов логов (чтобы диск больше не забивался).
# Запустить ОДИН раз на проде: make setup-logs
# ============================================================

.PHONY: setup-logs
setup-logs:            ## лимит journald 300M + лимит логов контейнеров 50m x 3
	# journald -> 300M
	sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=300M/' /etc/systemd/journald.conf
	grep -qE '^SystemMaxUse=' /etc/systemd/journald.conf || echo 'SystemMaxUse=300M' | sudo tee -a /etc/systemd/journald.conf
	sudo systemctl restart systemd-journald
	# docker log-opts -> 50m x 3 (только если daemon.json ещё нет)
	@if [ -f /etc/docker/daemon.json ]; then \
		echo "!! /etc/docker/daemon.json уже есть — не трогаю. Добавь log-opts вручную."; \
	else \
		printf '{\n  "log-driver": "json-file",\n  "log-opts": { "max-size": "50m", "max-file": "3" }\n}\n' | sudo tee /etc/docker/daemon.json; \
		sudo systemctl restart docker; \
		echo "daemon.json создан. Пересоздай контейнеры: make recreate"; \
	fi
	@echo "Лимиты логов настроены."

# Пересоздать контейнеры, чтобы подхватили лимит логов Docker
.PHONY: recreate
recreate:              ## up -d --force-recreate (применить лимит логов к контейнерам)
	$(COMPOSE) up -d --force-recreate

# ============================================================
# Management-команды приложения
# ============================================================

.PHONY: migrate
migrate:
	$(COMPOSE) exec web poetry run python manage.py migrate

.PHONY: seed
seed:
	$(COMPOSE) exec web poetry run python manage.py seed
	$(COMPOSE) exec web poetry run python manage.py import_warehouse

.PHONY: create_category
create_category:
	$(COMPOSE) exec web poetry run python manage.py create_category

.PHONY: create_cars
create_cars:
	$(COMPOSE) exec web poetry run python manage.py create_car_models

.PHONY: create_modifications
create_modifications:
	$(COMPOSE) exec web poetry run python manage.py import_modifications

.PHONY: create_engines
create_engines:
	$(COMPOSE) exec web poetry run python manage.py create_engines
