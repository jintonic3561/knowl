.DEFAULT_GOAL := help

COMPOSE_FILE := docker/docker-compose.yml
ENV_FILE := .env
SERVICE := knowl

# .env がある時だけ --env-file を渡す。無くても docker-compose.yml 側で ${VAR:-} fallback が効く。
COMPOSE := docker compose -f $(COMPOSE_FILE) $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE))

KEEPALIVE_SCRIPT := $(abspath scripts/keepalive.sh)
KEEPALIVE_MARKER := \# knowl-keepalive
KEEPALIVE_CRON   ?= */30 * * * *
KEEPALIVE_LOG    := $(abspath .logs/keepalive.log)

.PHONY: help start stop deploy run-once logs keepalive-start keepalive-stop keepalive-status keepalive-now keepalive-logs

help: ## 利用可能なターゲット一覧
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

start: ## 監視開始 (cron 常駐コンテナを build + 起動。起動直後に 1 サイクル即時実行)
	$(COMPOSE) up -d --build

deploy: ## 稼働中コンテナの差し替え (rebuild + 再起動。即時 1 サイクルは走らせず次 tick から開始)
	KNOWL_SKIP_INITIAL_RUN=1 $(COMPOSE) up -d --build

stop: ## 監視終了 (コンテナ停止・削除)
	$(COMPOSE) down

run-once: ## 一回だけ実行 (1 サイクルを ephemeral コンテナで走らせて破棄)
	$(COMPOSE) build $(SERVICE)
	$(COMPOSE) run --rm --entrypoint /usr/local/bin/knowl-run-cycle $(SERVICE)

logs: ## 監視コンテナのログを追跡
	docker logs -f $(SERVICE)

keepalive-start: ## OAuth 自動 refresh の host cron を登録 (KEEPALIVE_CRON で周期上書き可)
	@( crontab -l 2>/dev/null | grep -vF '$(KEEPALIVE_MARKER)' ; \
	   echo "$(KEEPALIVE_CRON) $(KEEPALIVE_SCRIPT) $(KEEPALIVE_MARKER)" \
	 ) | crontab -
	@echo "installed: $(KEEPALIVE_CRON) $(KEEPALIVE_SCRIPT)"

keepalive-stop: ## OAuth 自動 refresh の host cron を解除
	@if crontab -l 2>/dev/null | grep -qF '$(KEEPALIVE_MARKER)'; then \
	   crontab -l 2>/dev/null | grep -vF '$(KEEPALIVE_MARKER)' | crontab - ; \
	   echo "uninstalled" ; \
	 else \
	   echo "not installed" ; \
	 fi

keepalive-status: ## 現在登録されている keepalive 行を表示
	@crontab -l 2>/dev/null | grep -F '$(KEEPALIVE_MARKER)' || echo "not installed"

keepalive-now: ## keepalive を即時実行 (cron を待たない動作確認用)
	@$(KEEPALIVE_SCRIPT)

keepalive-logs: ## keepalive のログを追跡
	@touch $(KEEPALIVE_LOG)
	@tail -f $(KEEPALIVE_LOG)
