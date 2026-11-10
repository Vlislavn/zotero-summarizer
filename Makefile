.PHONY: help install run iphone dev test lint check scan scan-diff deadcode-update deadcode-refresh deadcode-gates
.DEFAULT_GOAL := help

# Prefer project-venv tools so targets work without activating it; fall back to PATH.
tool = $(if $(wildcard .venv/bin/$(1)),.venv/bin/$(1),$(1))
ZS := $(call tool,zotero-summarizer)
PY := $(call tool,python3)

# Project folder the app reads goals.yaml/.env/data from (Settings.load): ZOTERO_SUMMARIZER_HOME, else this checkout.
HOME_DIR = $(or $(ZOTERO_SUMMARIZER_HOME),$(CURDIR))

# Built UI served by `run`/`iphone`; rebuilt only when frontend sources change.
DIST := frontend/dist/index.html
UI_SOURCES := $(shell find frontend/src frontend/public -type f 2>/dev/null) \
  $(wildcard frontend/index.html frontend/package-lock.json frontend/*.config.js)

# --- code-health scan knobs (system-owned defaults; override only when needed) ---
# Base for the diff gates and `scan-diff` (what "changed" is measured against).
BASE ?= origin/main
DEADCODE := .venv/bin/deadcode

help: ## Show these commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  make %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install Python (with dev tools) and frontend dependencies
	uv sync --inexact --extra dev
	npm ci --prefix frontend
	@$(MAKE) --no-print-directory deadcode-refresh

run: $(DIST) ## Start the app on this Mac: http://127.0.0.1:8000
	@echo "Settings and data: $(HOME_DIR)"
	$(ZS) serve --host 127.0.0.1 --port 8000

iphone: $(DIST) ## Start the app for iPhone via Tailscale HTTPS (Ctrl+C stops)
	@echo "Settings and data: $(HOME_DIR)"
	@ZS="$(ZS)" PY="$(PY)" tools/serve-iphone.sh

dev: ## Develop: backend :8000 + hot-reload UI :5173 (Ctrl+C stops both)
	@echo "Starting backend (:8000) + frontend (:5173). Ctrl+C stops both."
	@trap 'kill 0' INT TERM EXIT; \
	  $(ZS) serve --host 127.0.0.1 --port 8000 & \
	  cd frontend && npm run dev; \
	  wait

$(DIST): $(UI_SOURCES)
	@test -d frontend/node_modules || { echo "Frontend dependencies missing: run make install" >&2; exit 2; }
	npm run build --prefix frontend

test: ## Python tests (forked; see tests/README.md)
	$(call tool,pytest) -q --forked

lint: deadcode-refresh ## Repository guardrails (pre-commit hooks + deadcode-audit diff gates vs BASE)
	$(call tool,pre-commit) run --all-files
	@if [ -x $(DEADCODE) ]; then $(MAKE) --no-print-directory deadcode-gates; \
	else echo "warning: $(DEADCODE) not installed (run make install); deadcode-audit gates skipped" >&2; fi

# deadcode-audit tracks its main branch (no pin). Refresh = re-lock the git dependency and sync.
# Offline or GitHub unreachable: warn and keep the installed commit; never fail.
deadcode-refresh:
	@if uv lock --upgrade-package deadcode-audit >/dev/null 2>&1 && uv sync --inexact --extra dev >/dev/null 2>&1; then :; \
	else echo "warning: deadcode-audit refresh failed (offline?); using the installed version" >&2; fi
	@$(PY) -c "import importlib.metadata as m,json;d=json.loads(m.distribution('deadcode-audit').read_text('direct_url.json'));print('deadcode-audit @', d['vcs_info']['commit_id'][:12], '(main)')" 2>/dev/null \
	  || echo "warning: deadcode-audit is not installed (run make install)" >&2

# The diff gates see committed changes only (staged/untracked files are invisible to them).
deadcode-gates: ## deadcode-audit diff gates vs BASE (blocking)
	DEADCODE_BASE=$(BASE) $(call tool,pre-commit) run --hook-stage pre-push --all-files deadcode-gates

deadcode-update: deadcode-refresh deadcode-gates ## Update deadcode-audit to latest main, then run its gates

# Recipe lines stay serial even with `make -j check`. The build goes to a disposable
# directory, leaving an existing frontend/dist untouched.
check: ## Full local check before a PR (lint, tests, frontend, smoke test)
	$(MAKE) lint
	KMP_DUPLICATE_LIB_OK=TRUE $(MAKE) test
	cd frontend && npm run lint
	cd frontend && npm test
	@build=$$(mktemp -d "$${TMPDIR:-/tmp}/zs-pr-build.XXXXXX") || exit $$?; \
	  trap 'rm -rf "$$build"' EXIT; \
	  cd frontend && npm run build -- --outDir "$$build"
	PYTHONPATH="$(CURDIR)$${PYTHONPATH:+:$$PYTHONPATH}" $(ZS) smoke-test --project-root .

# Advisory reports (always exit 0, never block); enforcement is `make lint`.
#   make scan        whole tree: deadcode-audit reachability/cycles/overlaps/vulture/redundancy + slop
#   make scan-diff   changed files vs BASE
scan: ## Advisory code-health report, whole tree
	@set +e; \
	echo "── Dead symbols (reachability) ──"; $(DEADCODE) reachability-scan | sed 's/^/  /'; \
	echo "── Import cycles ──"; $(DEADCODE) cycles --advisory | sed 's/^/  /'; \
	echo "── Function overlaps ──"; $(DEADCODE) overlaps --no-embed 2>&1 | sed 's/^/  /'; \
	echo "── Redundant transforms ──"; $(DEADCODE) redundancy $$(git ls-files 'zotero_summarizer/*.py') 2>&1 | sed 's/^/  /'; \
	echo "── Vulture (60%+) ──"; .venv/bin/vulture zotero_summarizer scripts/vulture_whitelist.py 2>&1 | sed 's/^/  /'; \
	echo "── AI-slop ──"; $(PY) tools/precommit/check_slop.py dump | sed 's/^/  /'; \
	true

scan-diff: ## Advisory code-health report, changes vs BASE
	@set +e; \
	$(DEADCODE) runtime-consumer-check --compare-branch $(BASE) 2>&1; \
	$(DEADCODE) vulture-changed --compare-branch $(BASE) 2>&1; \
	$(DEADCODE) redundancy --compare-branch $(BASE) 2>&1; \
	files="$$(git diff --name-only --diff-filter=d "$$(git merge-base $(BASE) HEAD)" -- 'zotero_summarizer/*.py')"; \
	[ -z "$$files" ] || $(PY) tools/precommit/check_slop.py slop-check $$files 2>&1; \
	true
