.PHONY: ui api dev ui-build test lint scan scan-diff

# --- code-health scan knobs (system-owned defaults; override only when needed) ---
# Base branch for `scan-diff` (what "changed" is measured against).
BASE ?= main
# The function-overlap pass is deterministic + offline by default (fast, no model).
# `make scan EMBED=1` / `make scan-diff EMBED=1` adds the semantic code-embedding signal
# (downloads a local code model on first run; `ZS_OFFLINE=1` runs offline afterwards).
OVERLAP_FLAGS := --no-embed
ifeq ($(EMBED),1)
OVERLAP_FLAGS :=
endif

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

# ┌──────────────────────────────────────────────────────────────────────────────┐
# │ CODE-HEALTH SCAN — two commands, one choice (scope). Both are advisory REPORTS │
# │ (always exit 0, never block); enforcement of NEW findings is `make lint`.      │
# │   make scan          every detector across the WHOLE tree                       │
# │   make scan-diff      the same, scoped to what changed vs the base branch        │
# │ Knobs: EMBED=1 (add the semantic code-model overlap pass) · BASE=<branch>.       │
# └──────────────────────────────────────────────────────────────────────────────┘
#
# Detectors: Tier 1 dead-code orphans · Tier 2 vulture · Tier 6 redundancy+clones ·
# Tier 7 AI-slop · all-pairs function-overlap audit.
#
# Hardened to exit 0 under any shell: `set +e` defeats an inherited errexit, vulture's
# exit-3-on-findings is neutralized with `|| true` (it propagates through `| sed` under
# `pipefail`), and the recipe ends on `true`.

# FULL: every finding (frozen or not) across the whole runtime package — the backlog.
scan:
	@set +e; \
	echo "═══ Code-health scan · WHOLE TREE (advisory — never blocks) ═══"; \
	echo "── Dead-code orphans · Tier 1 ──"; \
	python3 tools/precommit/check_dead_code.py dump-orphans | sed 's/^/  /'; \
	echo "── Unused code · Tier 2 (vulture) ──"; \
	if python3 -c "import vulture" 2>/dev/null; then \
	  python3 tools/precommit/check_dead_code.py vulture-scan 2>&1 | sed 's/^/  /' || true; \
	else echo "  ⚠ SKIPPED — vulture not installed in this env (it runs in the gate: 'make lint')"; fi; \
	echo "── Redundant transforms + near-duplicate functions · Tier 6 ──"; \
	python3 tools/precommit/check_redundancy.py dump | sed 's/^/  /'; \
	echo "── AI-slop · Tier 7 ──"; \
	python3 tools/precommit/check_slop.py dump | sed 's/^/  /'; \
	echo "── Function overlaps · all-pairs semantic ($(if $(filter 1,$(EMBED)),code-model,deterministic)) ──"; \
	python3 tools/precommit/check_overlaps.py audit $(OVERLAP_FLAGS) 2>&1 | sed 's/^/  /'; \
	echo "── Stale allowlist entries (grandfathers with no live finding) ──"; \
	python3 tools/precommit/check_allowlists.py reconcile 2>&1 | sed 's/^/  /'; \
	echo "── Totals (all findings, frozen or not — goal: shrink to empty) ──"; \
	echo "  orphans=$$(python3 tools/precommit/check_dead_code.py dump-orphans | wc -l | tr -d ' ')" \
	     "redundancy=$$(python3 tools/precommit/check_redundancy.py dump | wc -l | tr -d ' ')" \
	     "slop=$$(python3 tools/precommit/check_slop.py dump | wc -l | tr -d ' ')" \
	     "vulture=$$(python3 -c 'import vulture' 2>/dev/null && echo OK || echo SKIPPED-not-installed)"; \
	echo "  Enforce new findings: make lint   ·   semantic overlaps: make scan EMBED=1"; \
	true

# VS BASE BRANCH: the same detectors, scoped to the .py you changed off $(BASE). Fast.
scan-diff:
	@set +e; \
	base="$$(git merge-base $(BASE) HEAD 2>/dev/null || echo $(BASE))"; \
	files="$$(git diff --name-only --diff-filter=d "$$base" -- '*.py' | sed -n 's#^\(zotero_summarizer/.*\)#\1#p')"; \
	if [ -z "$$files" ]; then echo "No changed Python files vs $(BASE) ($$base) — nothing to scan."; exit 0; fi; \
	n=$$(echo "$$files" | wc -l | tr -d ' '); \
	echo "═══ Code-health scan · $$n changed file(s) vs $(BASE) (advisory — never blocks) ═══"; \
	echo "── Dead-code orphans · Tier 1 ──"; \
	python3 tools/precommit/check_dead_code.py consumer-check $$files 2>&1 | sed 's/^/  /'; \
	echo "── Redundant transforms · Tier 6 ──"; \
	python3 tools/precommit/check_redundancy.py transforms-check $$files 2>&1 | sed 's/^/  /'; \
	echo "── AI-slop · Tier 7 ──"; \
	python3 tools/precommit/check_slop.py slop-check $$files 2>&1 | sed 's/^/  /'; \
	echo "── Function overlaps · your changed functions vs the whole codebase ($(if $(filter 1,$(EMBED)),code-model,deterministic)) ──"; \
	python3 tools/precommit/check_overlaps.py audit --changed $$files $(OVERLAP_FLAGS) 2>&1 | sed 's/^/  /'; \
	echo "  Knobs: EMBED=1 (semantic overlap) · BASE=<branch> (default main) · enforce: make lint"; \
	true
