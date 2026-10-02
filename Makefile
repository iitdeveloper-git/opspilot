.PHONY: help dev bot test test-cov lint format typecheck docker-build docker-up docker-down deploy backup clean

PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
COMPOSE ?= docker compose -f deploy/docker/docker-compose.yml

help:
	@echo "OpsPilot 2.0 — Autonomous Infrastructure Command Center"
	@echo "=========================================================="
	@echo "  make dev          - Run Web Command Center locally (Uvicorn auto-reload)"
	@echo "  make bot          - Run OpsPilot Telegram Bot & scheduler"
	@echo "  make test         - Run full pytest test suite"
	@echo "  make test-cov     - Run tests with HTML coverage report"
	@echo "  make lint         - Run ruff linter checks and auto-fix"
	@echo "  make format       - Format Python code with ruff format"
	@echo "  make typecheck    - Run static type checking with mypy"
	@echo "  make docker-build - Build production Docker image"
	@echo "  make docker-up    - Run Docker Compose stack"
	@echo "  make docker-down  - Stop Docker Compose stack"
	@echo "  make deploy       - Deploy to production VPS via deploy_manual.sh"
	@echo "  make backup       - Backup runtime SQLite databases"
	@echo "  make clean        - Remove caches, pyc, coverage artifacts"

dev:
	PYTHONPATH=src $(PYTHON) -m uvicorn opspilot.web.app:create_web_app --factory --host 0.0.0.0 --port 8080 --reload

bot:
	PYTHONPATH=src $(PYTHON) -m opspilot.main

test:
	PYTHONPATH=src $(PYTEST)

test-cov:
	PYTHONPATH=src $(PYTEST) --cov=opspilot --cov-report=term-missing --cov-report=html

lint:
	$(RUFF) check --fix src/ tests/

format:
	$(RUFF) format src/ tests/

typecheck:
	$(MYPY) src/

docker-build:
	docker build -f deploy/docker/Dockerfile -t opspilot:latest .

docker-up:
	$(COMPOSE) up -d

docker-down:
	$(COMPOSE) down

deploy:
	./deploy_manual.sh

backup:
	./scripts/backup_db.sh

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov/
