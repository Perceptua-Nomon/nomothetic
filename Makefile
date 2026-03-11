.PHONY: install install-dev install-pi test lint format type-check clean start-stream start-api stop-stream stop-api stop

install:
	uv sync

install-dev:
	uv sync --all-extras --no-extra pi

install-pi:
	uv venv --system-site-packages
	uv sync --extra pi --extra web --extra api --extra telemetry

test:
	pytest tests/ -v --cov=src/nomothetic --cov-report=html

lint:
	ruff check src/ tests/
	black --check src/ tests/

format:
	black src/ tests/
	ruff check --fix src/ tests/

type-check:
	mypy src/ tests/

start-stream:
	./scripts/start.sh stream

start-api:
	./scripts/start.sh api

stop-stream:
	./scripts/stop.sh stream

stop-api:
	./scripts/stop.sh api

stop:
	./scripts/stop.sh all

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/ dist/ .coverage htmlcov/ .pytest_cache/ .mypy_cache/

help:
	@echo "Available targets:"
	@echo "  install      - Install the package"
	@echo "  install-dev  - Install package and development dependencies"
	@echo "  install-pi   - Install package on Raspberry Pi"
	@echo "  test         - Run tests with coverage"
	@echo "  lint         - Check code style"
	@echo "  format       - Format code with black and ruff"
	@echo "  type-check   - Run type checking with mypy"
	@echo "  clean        - Remove generated files and caches"
	@echo "  start-stream - Start the MJPEG stream server in the background"
	@echo "  start-api    - Start the REST API server in the background"
	@echo "  stop-stream  - Stop the MJPEG stream server"
	@echo "  stop-api     - Stop the REST API server"
	@echo "  stop         - Stop all background servers"
