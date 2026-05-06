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

test-baseline: ## BAR-44 베이스라인 재현성 테스트
	@echo "[BAR-44] Running pytest backend/tests/strategy/test_baseline.py..."
	@$(PYTHON) -m pytest backend/tests/strategy/test_baseline.py -v
	@echo "[BAR-44] tests OK"

test-strategy: ## BAR-45 Strategy v2 ABC + 모델 단위 테스트
	@echo "[BAR-45] Running pytest backend/tests/strategy/..."
	@$(PYTHON) -m pytest backend/tests/strategy/ -v \
		--cov=backend.core.strategy.base \
		--cov=backend.models.strategy \
		--cov-report=term-missing
	@echo "[BAR-45] tests OK"

test-market-session: ## BAR-52 MarketSessionService 단위 테스트
	@echo "[BAR-52] Running pytest backend/tests/market_session/..."
	@$(PYTHON) -m pytest backend/tests/market_session/ -v \
		--cov=backend.core.market_session.service \
		--cov-report=term-missing
	@echo "[BAR-52] tests OK"

test-nxt-gateway: ## BAR-53 NxtGateway 1차 단위 테스트
	@echo "[BAR-53] Running pytest backend/tests/gateway/..."
	@$(PYTHON) -m pytest backend/tests/gateway/ -v \
		--cov=backend.core.gateway.nxt \
		--cov-report=term-missing
	@echo "[BAR-53] tests OK"

baseline: ## BAR-44 베이스라인 측정 실행 (4 전략 합성 데이터)
	@echo "[BAR-44] Running scripts/run_baseline.py..."
	@$(PYTHON) -c "import sys; sys.path.insert(0, '.'); exec(open('scripts/run_baseline.py').read())"
	@echo "[BAR-44] baseline OK"

test: ## 전체 backend 단위 테스트 (legacy + config)
	@$(PYTHON) -m pytest backend/tests/ -v
