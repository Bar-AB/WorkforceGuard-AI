COMPOSE := docker compose -f infra/docker-compose.yml --env-file $(if $(wildcard .env),.env,.env.example)
# Exported, not prefixed per command, so recipes also work when make runs them with cmd.exe.
export PYTHONPATH := backend

.PHONY: up down migrate seed mcp-user fmt lint lint-backend lint-frontend test test-backend test-frontend eval mcp mcp-inspect

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

migrate:
	uv run alembic upgrade head

SEED ?= 42

seed: migrate
	uv run python -m app.seed --seed $(SEED)

mcp-user: migrate
	uv run python -m app.db.mcp_password

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
	uv run python -m evals.run_rules

mcp: .env.mcp
	uv run python -m mcp_server

mcp-inspect: .env.mcp
	npx @modelcontextprotocol/inspector --cli uv run python -m mcp_server -- -e PYTHONPATH=backend --method tools/list

.env.mcp:
	@echo "Missing .env.mcp: copy .env.mcp.example to .env.mcp, set the password, then run make mcp-user." && exit 1
