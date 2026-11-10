PY ?= python3
RUFF ?= $(PY) -m ruff

.PHONY: help install-dev test test-backend test-frontend lint lint-backend lint-frontend format migrate backup reconcile run

help:
	@echo "make install-dev   安装后端开发依赖（含 pytest / ruff）"
	@echo "make test          运行后端 pytest + 前端 jest"
	@echo "make lint          后端 ruff + 前端 tsc/biome"
	@echo "make format        后端 ruff format"
	@echo "make migrate       执行数据库迁移 (flask db upgrade)"
	@echo "make backup        备份数据库"
	@echo "make reconcile     账户余额对账报告"

install-dev:
	$(PY) -m pip install -r requirements-dev.txt

test: test-backend test-frontend

test-backend:
	$(PY) -m pytest

test-frontend:
	cd frontend && npm test -- --ci

lint: lint-backend lint-frontend

lint-backend:
	$(RUFF) check .

lint-frontend:
	cd frontend && npm run tsc && npm run biome:lint

format:
	$(RUFF) format .

migrate:
	$(PY) -m flask db upgrade

backup:
	./scripts/backup_db.sh

reconcile:
	$(PY) scripts/reconcile_accounts.py

run:
	$(PY) run.py
