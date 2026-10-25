# 用法：make help
COMPOSE := docker compose -p rengu-backend
RUN := uv run

.PHONY: help up down db-shell migrate revision seed seed-dry dev test lint fmt check

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

up: ## 启动 PostgreSQL 17 + pgvector（宿主机 5440）
	$(COMPOSE) up -d --wait postgres

down: ## 停止容器（保留数据卷）
	$(COMPOSE) down

db-shell: ## psql 进库
	$(COMPOSE) exec postgres psql -U rengu rengu

migrate: ## 迁移到最新
	$(RUN) alembic upgrade head

revision: ## 生成迁移：make revision m="st: add object_type"
	$(RUN) alembic revision --autogenerate -m "$(m)"

seed: ## 把 seeds/json/<module>/ 幂等写入数据库（可 make seed m=versioning）
	$(RUN) python -m app.cli seed $(if $(m),-m $(m),)

seed-dry: ## 只校验 seed，不写入
	$(RUN) python -m app.cli seed --dry-run

dev: ## 本地启动（热重载），文档 http://127.0.0.1:8000/docs
	$(RUN) uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

test: ## 跑测试（使用独立库 rengu_test）
	$(RUN) pytest -q

lint: ## ruff 检查 + 格式检查 + 迁移与模型一致性
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) alembic check

fmt: ## 自动格式化
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

check: lint test ## 提交前必跑
