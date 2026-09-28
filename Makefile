COMPOSE := docker compose -f infra/docker-compose.yml --env-file $(if $(wildcard .env),.env,.env.example)

.PHONY: up down fmt lint lint-backend lint-frontend test test-backend test-frontend eval

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

fmt:
	uv run ruff format .
	uv run ruff check --fix .
	cd frontend && npm run format

lint: lint-backend lint-frontend

lint-backend:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy backend mcp_server

lint-frontend:
	cd frontend && npm run lint

test: test-backend test-frontend

test-backend:
	uv run pytest

test-frontend:
	cd frontend && npm test

eval:
	@echo "No evals yet (stub until slice 3)."
