# BarroAiTrade Makefile
# Reference: docs/01-plan/MASTER-EXECUTION-PLAN-v1.md

.PHONY: help legacy-scalping test-legacy test-config test

help: ## 사용 가능한 타겟 출력
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

PYTHON ?= python3

legacy-scalping: ## BAR-40 dry-run smoke test (legacy_scalping import 검증)
	@echo "[BAR-40] Running legacy_scalping dry-run..."
	@DRY_RUN=1 $(PYTHON) -m backend.legacy_scalping.main
	@echo "[BAR-40] dry-run OK"

test-legacy: ## BAR-41 어댑터 + legacy_scalping 단위 테스트
	@echo "[BAR-41] Running pytest backend/tests/legacy_scalping/..."
	@$(PYTHON) -m pytest backend/tests/legacy_scalping/ -v \
		--cov=backend.legacy_scalping._adapter --cov-report=term-missing
	@echo "[BAR-41] tests OK"

test-config: ## BAR-42 통합 환경변수 스키마 단위 테스트
	@echo "[BAR-42] Running pytest backend/tests/config/..."
	@$(PYTHON) -m pytest backend/tests/config/ -v \
		--cov=backend.config.settings --cov-report=term-missing
	@echo "[BAR-42] tests OK"

test-monitoring: ## BAR-43 모니터링 인프라 단위 테스트
	@echo "[BAR-43] Running pytest backend/tests/monitoring/..."
	@$(PYTHON) -m pytest backend/tests/monitoring/ -v \
		--cov=backend.core.monitoring.metrics \
		--cov=backend.api.routes.metrics \
		--cov-report=term-missing
	@echo "[BAR-43] tests OK"

test: ## 전체 backend 단위 테스트 (legacy + config)
	@$(PYTHON) -m pytest backend/tests/ -v
