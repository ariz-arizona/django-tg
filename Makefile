# Автоматически подгружаем переменные из .env если файл существует
ifneq (,$(wildcard .env))
    include .env
    export
endif

# Переменные (значения по умолчанию если нет в .env)
PINGGY_PORT ?= $(NGINX_PORT)
PINGGY_USER ?= qr
PINGGY_HOST ?= free.pinggy.io
PINGGY_SSH_PORT ?= 443

# Директория для логов
LOG_DIR := tunnel

# Файлы состояния
TUNNEL_PID_FILE := $(LOG_DIR)/.tunnel.pid
TUNNEL_URL_FILE := $(LOG_DIR)/.tunnel.url
TUNNEL_LOG_FILE := $(LOG_DIR)/tunnel_output.log

# Цвета для вывода
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
BLUE := \033[0;34m
NC := \033[0m

# Строка подключения к туннелю
TUNNEL_SSH_CMD := ssh -p $(PINGGY_SSH_PORT) -R0:localhost:$(PINGGY_PORT) $(PINGGY_USER)@$(PINGGY_HOST) -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null

.PHONY: help init tunnel up down restart clean logs status run

help: ## Показать справку
	@echo "$(BLUE)📚 Доступные команды:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(BLUE)📌 Примеры:$(NC)"
	@echo "  $(YELLOW)make run$(NC)              - Запустить всё в одном терминале"
	@echo "  $(YELLOW)make down$(NC)             - Остановить всё (туннель + Docker)"
	@echo "  $(YELLOW)make status$(NC)           - Показать статус всех сервисов"
	@echo ""
	@echo "$(BLUE)📋 Текущая конфигурация:$(NC)"
	@echo "  PINGGY_PORT: $(PINGGY_PORT)"
	@echo "  PROJECT_NAME: $(PROJECT_NAME)"

init: ## Создать папку для логов
	@mkdir -p $(LOG_DIR)
	@echo "$(GREEN)✅ Папка $(LOG_DIR) создана$(NC)"

# === Функции для проверки состояния ===
define check_tunnel_alive
	@if [ -f $(TUNNEL_PID_FILE) ] && ps -p $$(cat $(TUNNEL_PID_FILE)) > /dev/null 2>&1; then \
		echo "$(YELLOW)⚠️  Туннель уже запущен (PID: $$(cat $(TUNNEL_PID_FILE)))$(NC)"; \
		[ -f $(TUNNEL_URL_FILE) ] && echo "$(GREEN)🔗 URL: $$(cat $(TUNNEL_URL_FILE))$(NC)" || echo "$(YELLOW)⚠️  URL неизвестен$(NC)"; \
		echo "$(YELLOW)💡 Используйте 'make down' для перезапуска$(NC)"; \
		exit 1; \
	fi
endef

define get_tunnel_url
	@echo "$(BLUE)⏳ Ожидание получения URL...$(NC)"; \
	URL=""; \
	for i in $$(seq 1 10); do \
		sleep 1; \
		URL=$$(grep -o 'https://[^ ]*\.pinggy-free\.link' $(TUNNEL_LOG_FILE) 2>/dev/null | head -1); \
		[ -n "$$URL" ] && break; \
	done; \
	if [ -n "$$URL" ]; then \
		echo "$$URL" > $(TUNNEL_URL_FILE); \
		echo "$(GREEN)🔗 URL туннеля: $$URL$(NC)"; \
	else \
		echo "$(RED)❌ Не удалось получить URL из лога$(NC)"; \
		echo "$(YELLOW)📋 Содержимое лога ($(TUNNEL_LOG_FILE)):$(NC)"; \
		cat $(TUNNEL_LOG_FILE) 2>/dev/null || echo "Файл лога пуст или не существует"; \
		echo ""; \
		echo "$(YELLOW)💡 Попробуйте запустить вручную:$(NC)"; \
		echo "  $(YELLOW)$(TUNNEL_SSH_CMD)$(NC)"; \
		exit 1; \
	fi
endef

# === Команды ===
tunnel: init ## Запустить Pinggy туннель и сохранить URL
	@echo "$(GREEN)🔗 Запуск Pinggy туннеля...$(NC)"
	@echo "$(BLUE)📡 Подключение к $(PINGGY_USER)@$(PINGGY_HOST):$(PINGGY_SSH_PORT)$(NC)"
	@echo "$(BLUE)🌐 Локальный порт: $(PINGGY_PORT)$(NC)"
	$(check_tunnel_alive)
	@echo ""
	@echo "$(BLUE)🔧 Выполняю команду:$(NC)"
	@echo "$(YELLOW)$(TUNNEL_SSH_CMD)$(NC)"
	@echo ""
	@$(TUNNEL_SSH_CMD) > $(TUNNEL_LOG_FILE) 2>&1 & \
	echo $$! > $(TUNNEL_PID_FILE); \
	echo "$(GREEN)📋 Лог туннеля сохранен в: $(TUNNEL_LOG_FILE)$(NC)"
	$(get_tunnel_url)
	@echo "$(GREEN)✅ Туннель запущен (PID: $$(cat $(TUNNEL_PID_FILE)))$(NC)"

up: tunnel ## Запустить Docker контейнеры с переменной окружения
	@echo "$(GREEN)🐳 Запуск Docker контейнеров...$(NC)"
	@if [ ! -f $(TUNNEL_URL_FILE) ]; then \
		echo "$(RED)❌ URL туннеля не найден. Сначала запустите туннель: make tunnel$(NC)"; \
		exit 1; \
	fi
	@TG_WEBHOOK_HOST=$$(cat $(TUNNEL_URL_FILE)) \
	docker compose -f compose.yaml -f compose.dev.yaml up --build -d
	@echo "$(GREEN)✅ Docker контейнеры запущены!$(NC)"
	@echo "$(BLUE)📋 Проверить статус: make status$(NC)"
	@echo "$(BLUE)📋 Посмотреть логи: make logs$(NC)"

down: ## Остановить всё (туннель + Docker)
	@echo "$(RED)🛑 Остановка всех сервисов...$(NC)"
	@echo "$(YELLOW)🐳 Остановка Docker контейнеров...$(NC)"
	@docker compose -f compose.yaml -f compose.dev.yaml down 2>/dev/null || true
	@echo "$(GREEN)✅ Docker контейнеры остановлены$(NC)"
	@echo "$(YELLOW)🔗 Остановка туннеля...$(NC)"
	@if [ -f $(TUNNEL_PID_FILE) ]; then \
		PID=$$(cat $(TUNNEL_PID_FILE)); \
		if ps -p $$PID > /dev/null 2>&1; then \
			kill $$PID 2>/dev/null || true; \
			echo "$(GREEN)✅ Туннель остановлен (PID: $$PID)$(NC)"; \
		else \
			echo "$(YELLOW)⚠️  Туннель с PID $$PID уже не работает$(NC)"; \
		fi; \
		rm -f $(TUNNEL_PID_FILE); \
	else \
		echo "$(YELLOW)⚠️  PID файл туннеля не найден$(NC)"; \
		pkill -f "ssh.*$(PINGGY_HOST)" 2>/dev/null && echo "$(GREEN)✅ Найденные процессы туннеля остановлены$(NC)" || true; \
	fi
	@rm -f $(TUNNEL_URL_FILE)
	@echo "$(GREEN)✅ Все сервисы остановлены$(NC)"

restart: down run ## Перезапустить всё

run: init ## Запустить всё в одном терминале
	@echo "$(BLUE)🚀 Запуск всего комплекса...$(NC)"
	@echo "$(BLUE)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"
	$(check_tunnel_alive)
	@echo "$(GREEN)Шаг 1/2: Запуск туннеля...$(NC)"
	@$(TUNNEL_SSH_CMD) > $(TUNNEL_LOG_FILE) 2>&1 & \
	echo $$! > $(TUNNEL_PID_FILE); \
	echo "$(GREEN)📋 Лог туннеля сохранен в: $(TUNNEL_LOG_FILE)$(NC)"
	@sleep 3
	$(get_tunnel_url)
	@echo "$(GREEN)Шаг 2/2: Запуск Docker...$(NC)"
	@TUNNEL_URL=$$(cat $(TUNNEL_URL_FILE)); \
	echo "$(BLUE)📋 TG_WEBHOOK_HOST=$$TUNNEL_URL$(NC)"; \
	echo "$(BLUE)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"; \
	TG_WEBHOOK_HOST=$$TUNNEL_URL \
	docker compose -f compose.yaml -f compose.dev.yaml up --build

logs: ## Показать логи Docker
	@docker compose -f compose.yaml -f compose.dev.yaml logs --tail=50 -f

status: ## Показать статус всех сервисов
	@echo "$(BLUE)📊 Статус сервисов:$(NC)"
	@echo "$(BLUE)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"
	@if [ -f $(TUNNEL_PID_FILE) ] && ps -p $$(cat $(TUNNEL_PID_FILE)) > /dev/null 2>&1; then \
		echo "$(GREEN)✅ Туннель: ЗАПУЩЕН (PID: $$(cat $(TUNNEL_PID_FILE)))$(NC)"; \
		if [ -f $(TUNNEL_URL_FILE) ]; then \
			echo "$(GREEN)🔗 URL: $$(cat $(TUNNEL_URL_FILE))$(NC)"; \
		else \
			echo "$(YELLOW)⚠️  URL: неизвестен$(NC)"; \
		fi; \
	else \
		echo "$(RED)❌ Туннель: НЕ ЗАПУЩЕН$(NC)"; \
	fi
	@echo "$(BLUE)─────────────────────────────────────────────$(NC)"
	@if docker compose -f compose.yaml -f compose.dev.yaml ps 2>/dev/null | grep -q "Up"; then \
		echo "$(GREEN)✅ Docker: ЗАПУЩЕН$(NC)"; \
		docker compose -f compose.yaml -f compose.dev.yaml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true; \
	else \
		echo "$(RED)❌ Docker: НЕ ЗАПУЩЕН$(NC)"; \
	fi

clean: down ## Полная очистка
	@echo "$(RED)🧹 Полная очистка...$(NC)"
	@rm -rf $(LOG_DIR)
	@docker compose -f compose.yaml -f compose.dev.yaml down --remove-orphans -v 2>/dev/null || true
	@echo "$(GREEN)✅ Полная очистка выполнена$(NC)"

.DEFAULT_GOAL := help