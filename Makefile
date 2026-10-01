COMPOSE := docker compose -f infra/docker-compose.yml --env-file $(if $(wildcard .env),.env,.env.example)

.PHONY: up down migrate seed fmt lint lint-backend lint-frontend test test-backend test-frontend eval

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

migrate:
	uv run alembic upgrade head

SEED ?= 42

seed: migrate
	PYTHONPATH=backend uv run python -m app.seed --seed $(SEED)

fmt:
	uv run ruff format .
	uv run ruff check --fix .
	cd frontend && npm run format

lint: lint-backend lint-frontend

lint-backend:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy backend mcp_server evals

lint-frontend:
	cd frontend && npm run lint

test: test-backend test-frontend

test-backend:
	uv run pytest

test-frontend:
	cd frontend && npm test

eval:
	PYTHONPATH=backend uv run python -m evals.run_rules
