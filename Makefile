# BarroAiTrade Makefile
# Reference: docs/01-plan/MASTER-EXECUTION-PLAN-v1.md

.PHONY: help legacy-scalping

help: ## 사용 가능한 타겟 출력
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

PYTHON ?= python3

legacy-scalping: ## BAR-40 dry-run smoke test (legacy_scalping import 검증)
	@echo "[BAR-40] Running legacy_scalping dry-run..."
	@DRY_RUN=1 $(PYTHON) -m backend.legacy_scalping.main
	@echo "[BAR-40] dry-run OK"
