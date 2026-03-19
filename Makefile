.PHONY: dev test paper api flatten migrate lint format logs clean help

PYTHON := python3
PYTEST := pytest
UVICORN := uvicorn

help:
	@echo "Trading Bot Platform - Available commands:"
	@echo "  make dev        Start all services via docker-compose"
	@echo "  make test       Run the full test suite"
	@echo "  make paper      Start paper trading bot"
	@echo "  make api        Start the FastAPI control plane only"
	@echo "  make flatten    Flatten all open positions via API"
	@echo "  make migrate    Run Alembic database migrations"
	@echo "  make lint       Run ruff + mypy"
	@echo "  make format     Format code with black + ruff"
	@echo "  make logs       Tail application logs"
	@echo "  make clean      Remove compiled/cache files"

dev:
	docker-compose up --build

test:
	PYTHONPATH=. $(PYTEST) tests/ -v --tb=short

test-cov:
	PYTHONPATH=. $(PYTEST) tests/ --cov=packages --cov=workers --cov=apps --cov-report=term-missing

paper:
	PYTHONPATH=. TRADING_MODE=paper $(PYTHON) -m apps.trader.main

api:
	PYTHONPATH=. $(UVICORN) apps.api.main:app --host 0.0.0.0 --port 8000 --reload

flatten:
	@echo "Flattening all positions via API..."
	curl -X POST http://localhost:8000/flatten-all \
		-H "Content-Type: application/json" \
		-d '{"confirm": true}' \
		-H "Authorization: Bearer $$JWT_TOKEN"

migrate:
	PYTHONPATH=. alembic -c infra/alembic.ini upgrade head

migrate-down:
	PYTHONPATH=. alembic -c infra/alembic.ini downgrade -1

lint:
	ruff check packages/ workers/ apps/ tests/
	mypy packages/ workers/ apps/ --ignore-missing-imports

format:
	black packages/ workers/ apps/ tests/
	ruff check --fix packages/ workers/ apps/ tests/

logs:
	tail -f logs/app.jsonl

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	find . -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
