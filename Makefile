.DEFAULT_GOAL := help

COMPOSE_FILE := docker/docker-compose.yml
ENV_FILE := .env
SERVICE := knowl

# .env がある時だけ --env-file を渡す。無くても docker-compose.yml 側で ${VAR:-} fallback が効く。
COMPOSE := docker compose -f $(COMPOSE_FILE) $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE))

.PHONY: help start stop run-once logs

help: ## 利用可能なターゲット一覧
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

start: ## 監視開始 (cron 常駐コンテナを build + 起動)
	$(COMPOSE) up -d --build

stop: ## 監視終了 (コンテナ停止・削除)
	$(COMPOSE) down

run-once: ## 一回だけ実行 (1 サイクルを ephemeral コンテナで走らせて破棄)
	$(COMPOSE) build $(SERVICE)
	$(COMPOSE) run --rm --entrypoint /usr/local/bin/knowl-run-cycle $(SERVICE)

logs: ## 監視コンテナのログを追跡
	docker logs -f $(SERVICE)
