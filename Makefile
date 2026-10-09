.PHONY: dev api runner frontend install test lint build-demo runner-podman dev-podman

# Rootless BuildKit daemon socket for the real podman pipeline
# (ops/RUNBOOK.md §2a). An exported BUILDKIT_ADDR wins.
BUILDKIT_ADDR ?= unix:///run/user/$(shell id -u)/buildkit/buildkitd.sock

# Boot the whole product in dev mode (api + runner + frontend watcher).
dev:
	@echo "Starting Portal in dev mode. Press Ctrl-C to stop everything."
	@trap 'kill 0' EXIT; (uvicorn api.server:app --app-dir . --host 127.0.0.1 --port 8000 --reload) & (python -m runner.main) & (cd frontend && npm run dev) & wait

# Control plane only.
api:
	uvicorn api.server:app --app-dir . --host 127.0.0.1 --port 8000 --reload

# Trusted executor only.
runner:
	python -m runner.main

# Real pipeline runner: Railpack → BuildKit → rootless Podman
# (PORTAL_RUNNER_EXECUTOR=podman + the RUNBOOK §2a BuildKit socket).
runner-podman:
	PORTAL_RUNNER_EXECUTOR=podman BUILDKIT_ADDR=$(BUILDKIT_ADDR) python -m runner.main

# dev, but with the real podman pipeline for the runner.
dev-podman:
	@echo "Portal dev on the real podman pipeline — rootless BuildKit + Railpack (ops/RUNBOOK.md §2a). Press Ctrl-C to stop everything."
	@trap 'kill 0' EXIT; (uvicorn api.server:app --app-dir . --host 127.0.0.1 --port 8000 --reload) & (PORTAL_RUNNER_EXECUTOR=podman BUILDKIT_ADDR=$(BUILDKIT_ADDR) python -m runner.main) & (cd frontend && npm run dev) & wait

# SPA only.
frontend:
	cd frontend && npm run dev

install:
	pip install -r requirements.txt
	cd frontend && npm install

test:
	python -m pytest -q

lint:
	python -m compileall -q core adapters api runner
	cd frontend && npx tsc --noEmit

# Built once as the offline demo repo used by dev mode's "demo" shortcut.
build-demo:
	cd dev/demo-repo && npm run build && cd ../..