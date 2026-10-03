.PHONY: up down logs build test test-workspaces lint migrate seed psql bots run

run:
	python run.py

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

build:
	docker compose build

test:
	cd backend && python -m pytest -q

test-workspaces:
	cd backend && python -m pytest -q tests/test_workspaces.py

test-integration:
	docker compose exec -T -e TEST_DATABASE_URL=postgresql+asyncpg://$${POSTGRES_USER:-support}:$${POSTGRES_PASSWORD:-support}@postgres:5432/$${POSTGRES_DB:-support} backend python -m pytest -q tests/test_integration.py

migrate:
	docker compose run --rm backend alembic upgrade head

seed:
	docker compose run --rm backend python -m app.seed

psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-support} -d $${POSTGRES_DB:-support}

bots:
	docker compose --profile bots up --build -d
