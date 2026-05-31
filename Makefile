.PHONY: ui api dev ui-build test lint

# Frontend dev server (Vite). Proxies /api/* to the backend on :8000, so the
# backend MUST also be running — use `make dev` to start both at once.
ui:
	cd frontend && npm run dev

# Backend API server (FastAPI/uvicorn) on :8000.
api:
	zotero-summarizer serve --host 127.0.0.1 --port 8000

# Run backend + frontend together (Ctrl+C stops both). This is what you want
# for local UI work: the Vite proxy has a live backend to talk to.
dev:
	@echo "Starting backend (:8000) + frontend (:5173). Ctrl+C stops both."
	@trap 'kill 0' INT TERM EXIT; \
	  zotero-summarizer serve --host 127.0.0.1 --port 8000 & \
	  cd frontend && npm run dev; \
	  wait

# Production build (what CI / CLAUDE.md verify step uses)
ui-build:
	cd frontend && npm run build

# Run Python tests (forked, per CLAUDE.md)
test:
	pytest -q --forked

# Pre-commit checks (LOC, layering, README freshness)
lint:
	pre-commit run --all-files
