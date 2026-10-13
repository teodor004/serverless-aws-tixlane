# TixLane developer entry points. Every target that changes infrastructure targets the
# emulator root (infra/envs/local) only; real AWS changes happen exclusively through the
# approval-gated GitHub workflows (CLAUDE.md, ADR-0007).

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

-include .env
include versions.env
export

EMULATOR          ?= floci
EMULATOR_ENDPOINT ?= http://localhost:4566
PY_VERSION        ?= 3.13
LAMBDA_ARCH       ?= x86_64

LOCAL_DIR := infra/envs/local
BUILD_DIR := build
PACKAGE   := $(BUILD_DIR)/lambda.zip
OUTPUTS   := $(BUILD_DIR)/local-outputs.json
EVIDENCE  := $(BUILD_DIR)/evidence-local
TOFU      := tofu -chdir=$(LOCAL_DIR)
TOFU_VARS := -var=python_runtime=python$(PY_VERSION)
# Global e2e.py options must precede the subcommand (argparse).
E2E       := uv run python scripts/e2e.py --outputs $(OUTPUTS) --endpoint-url $(EMULATOR_ENDPOINT) \
             --evidence-dir $(EVIDENCE)

.PHONY: help install hooks fmt lint test build up down logs local-init local-apply \
        local-plan-check local-destroy itest smoke k6-smoke cleanup-users dlq-drill scan tflint trivy \
        checkov gitleaks verify infracost clean

help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Python --------------------------------------------------------------------------------

install: ## Create .venv with runtime + dev dependencies
	uv sync

hooks: install ## Install the pre-commit hooks
	uv run pre-commit install

fmt: ## Format Python and OpenTofu
	uv run ruff format .
	uv run ruff check --fix .
	tofu fmt -recursive

lint: ## ruff + mypy + tofu fmt check
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	tofu fmt -check -recursive

test: ## Unit tests (Moto) with the 90% coverage gate
	uv run pytest

build: ## Build the deterministic Lambda package
	uv run python scripts/build_lambda.py --python-version $(PY_VERSION) --arch $(LAMBDA_ARCH) --out $(PACKAGE)

# --- Emulator ------------------------------------------------------------------------------

up: ## Start the emulator (EMULATOR=floci|ministack) and wait until healthy
	@var="$$(echo '$(EMULATOR)' | tr '[:lower:]' '[:upper:]')_IMAGE"; \
	  if [ -z "$${!var:-}" ]; then echo "Set $$var in .env first (see .env.example)."; exit 1; fi
	docker compose --profile $(EMULATOR) up -d
	EMULATOR=$(EMULATOR) EMULATOR_ENDPOINT=$(EMULATOR_ENDPOINT) scripts/wait_for_emulator.sh

down: ## Stop and remove both emulators
	docker compose --profile floci --profile ministack down --volumes --remove-orphans

logs: ## Tail emulator logs
	docker compose --profile $(EMULATOR) logs -f

local-init:
	$(TOFU) init -input=false

local-apply: build local-init ## Apply infra/envs/local to the emulator, then require an empty second plan
	$(TOFU) apply -input=false -auto-approve $(TOFU_VARS)
	$(TOFU) output -json > $(OUTPUTS)
	$(MAKE) --no-print-directory local-plan-check

local-plan-check: ## Fail unless a fresh plan against the emulator shows no changes
	@set +e; $(TOFU) plan -input=false -no-color -detailed-exitcode $(TOFU_VARS) > $(BUILD_DIR)/second-plan.txt; rc=$$?; set -e; \
	  case $$rc in \
	    0) echo "Second plan: no changes." ;; \
	    2) echo "Second plan is NOT empty - see $(BUILD_DIR)/second-plan.txt and docs/EMULATOR_GAPS.md"; exit 1 ;; \
	    *) cat $(BUILD_DIR)/second-plan.txt; exit $$rc ;; \
	  esac

local-destroy: ## Destroy the emulator stack
	$(TOFU) destroy -input=false -auto-approve $(TOFU_VARS)

itest: ## Integration tests against the running emulator
	TIXLANE_OUTPUTS=$(OUTPUTS) TIXLANE_ENDPOINT_URL=$(EMULATOR_ENDPOINT) \
	  uv run pytest tests/integration -m integration --no-cov

smoke: ## End-to-end smoke against the emulator; writes $(EVIDENCE)
	$(E2E) smoke --k6-env-file $(BUILD_DIR)/k6-local.env

k6-smoke: smoke ## k6 smoke against the emulator
	set -a; source $(BUILD_DIR)/k6-local.env; set +a; \
	  k6 run --summary-export $(EVIDENCE)/k6-smoke-summary.json k6/smoke.js
	$(MAKE) --no-print-directory cleanup-users

cleanup-users: ## Delete the Cognito test users created by smoke
	$(E2E) cleanup --users-file $(EVIDENCE)/users.json

dlq-drill: ## Poison message -> DLQ -> alarm, against the emulator
	$(E2E) dlq-drill --purge-after

# --- Quality gates (CI runs the same commands with pinned versions) ------------------------

scan: tflint trivy checkov gitleaks ## Every static scanner

tflint: ## tflint across all roots and modules
	tflint --init --config "$(CURDIR)/.tflint.hcl"
	tflint --recursive --config "$(CURDIR)/.tflint.hcl"

trivy: ## Trivy misconfiguration scan
	trivy config --config trivy.yaml .

checkov: ## Checkov (terraform + github_actions)
	checkov --config-file .checkov.yaml

gitleaks: ## Secret scan of the git history
	gitleaks git --redact --config .gitleaks.toml .

verify: ## Fail on unpinned tool versions or unpinned GitHub Actions
	scripts/check_versions.sh
	scripts/check_action_pins.sh

infracost: ## Cost estimate for infra/envs/aws (needs INFRACOST_API_KEY)
	infracost breakdown --config-file infracost.yml --format table

clean: ## Remove build output and caches (does not touch emulator state)
	rm -rf $(BUILD_DIR) .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
