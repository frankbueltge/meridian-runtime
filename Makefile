.PHONY: format format-check lint typecheck test test-contract test-integration test-e2e test-adversarial benchmark security-check

format:
	uv run ruff format .
	uv run ruff check --fix .

format-check:
	uv run ruff format --check .
	uv run ruff check .

lint:
	uv run ruff check .
	uv run lint-imports

typecheck:
	uv run mypy .

test:
	uv run python scripts/run_test_tier.py unit property

test-contract:
	uv run python scripts/run_test_tier.py contract

test-integration:
	uv run python scripts/run_test_tier.py integration

test-e2e:
	uv run python scripts/run_test_tier.py e2e

test-adversarial:
	uv run python scripts/run_test_tier.py adversarial

benchmark:
	uv run python scripts/run_test_tier.py meridianbench

security-check:
	uv export --quiet --no-emit-project --format requirements.txt -o .security-check-requirements.txt
	uv run pip-audit --strict -r .security-check-requirements.txt
	rm -f .security-check-requirements.txt
	uv run bandit -c pyproject.toml -r packages adapters
