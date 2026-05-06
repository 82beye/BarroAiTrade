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
	@echo "[BAR-53] Running pytest backend/tests/gateway/test_nxt.py..."
	@$(PYTHON) -m pytest backend/tests/gateway/test_nxt.py -v \
		--cov=backend.core.gateway.nxt \
		--cov-report=term-missing
	@echo "[BAR-53] tests OK"

test-composite-orderbook: ## BAR-54 CompositeOrderBookService 단위 테스트
	@echo "[BAR-54] Running pytest backend/tests/gateway/test_composite_orderbook.py..."
	@$(PYTHON) -m pytest backend/tests/gateway/test_composite_orderbook.py -v \
		--cov=backend.core.gateway.composite_orderbook \
		--cov-report=term-missing
	@echo "[BAR-54] tests OK"

test-router: ## BAR-55 SmartOrderRouter (SOR v1) 단위 테스트
	@echo "[BAR-55] Running pytest backend/tests/execution/..."
	@$(PYTHON) -m pytest backend/tests/execution/ -v \
		--cov=backend.core.execution.router \
		--cov=backend.models.order \
		--cov-report=term-missing
	@echo "[BAR-55] tests OK"

test-db: ## BAR-56 DB 어댑터 + Alembic + 마이그레이션 단위 테스트
	@echo "[BAR-56] Running pytest backend/tests/db/..."
	@$(PYTHON) -m pytest backend/tests/db/ -v \
		--cov=backend.db.database \
		--cov=backend.db._type_map \
		--cov=backend.db.repositories.audit_repo \
		--cov-report=term-missing
	@echo "[BAR-56] tests OK"

test-news: ## BAR-57 뉴스/공시 수집 파이프라인 단위 테스트
	@echo "[BAR-57] Running pytest backend/tests/news/ + db/test_news_repo + db/test_alembic_0002..."
	@$(PYTHON) -m pytest backend/tests/news/ backend/tests/db/test_news_repo.py backend/tests/db/test_alembic_0002.py -v \
		--cov=backend.core.news \
		--cov=backend.db.repositories.news_repo \
		--cov=backend.models.news \
		--cov-fail-under=70 \
		--cov-report=term-missing
	@echo "[BAR-57] tests OK"

test-themes: ## BAR-59 테마 분류기 단위 테스트
	@echo "[BAR-59] Running pytest backend/tests/themes/ + db/test_alembic_0004..."
	@$(PYTHON) -m pytest backend/tests/themes/ backend/tests/db/test_alembic_0004.py -v \
		--cov=backend.core.themes \
		--cov=backend.db.repositories.theme_repo \
		--cov=backend.models.theme \
		--cov-fail-under=70 \
		--cov-report=term-missing
	@echo "[BAR-59] tests OK"

test-embeddings: ## BAR-58 임베딩 인프라 단위 테스트
	@echo "[BAR-58] Running pytest backend/tests/embeddings/ + db/test_alembic_0003 + news/test_news_id_round_trip..."
	@$(PYTHON) -m pytest backend/tests/embeddings/ backend/tests/db/test_alembic_0003.py backend/tests/news/test_news_id_round_trip.py -v \
		--cov=backend.core.embeddings \
		--cov=backend.db.repositories.embedding_repo \
		--cov=backend.models.embedding \
		--cov-fail-under=70 \
		--cov-report=term-missing
	@echo "[BAR-58] tests OK"

# === Team Agent tmux 병렬 (BAR-META-001) ============================
team-help: ## Team Agent wrapper 도움말
	@./scripts/team_agent.sh help

team-start: ## Team Agent 시작 (BAR=<id> STAGE=<stage>) — 5 pane 병렬 dispatch
	@./scripts/team_agent.sh start "$(BAR)" "$(STAGE)" --no-attach

team-status: ## Team Agent 상태 (BAR=<id> STAGE=<stage>)
	@./scripts/team_agent.sh status "$(BAR)" "$(STAGE)"

team-watch: ## Team Agent 완료 대기 + COMBINED.md (BAR=<id> STAGE=<stage>)
	@./scripts/team_agent.sh watch "$(BAR)" "$(STAGE)"

team-attach: ## tmux attach -t team-<BAR>-<STAGE>
	@tmux attach -t "team-$(BAR)-$(STAGE)"

team-kill: ## Team Agent 세션 종료 (BAR=<id> STAGE=<stage>)
	@./scripts/team_agent.sh kill "$(BAR)" "$(STAGE)"

team-clean: ## Team Agent 세션+산출물 삭제 (BAR=<id> STAGE=<stage>)
	@./scripts/team_agent.sh clean "$(BAR)" "$(STAGE)"

team-ls: ## Team Agent 활성/저장 세션 목록
	@./scripts/team_agent.sh ls

baseline: ## BAR-44 베이스라인 측정 실행 (4 전략 합성 데이터)
	@echo "[BAR-44] Running scripts/run_baseline.py..."
	@$(PYTHON) -c "import sys; sys.path.insert(0, '.'); exec(open('scripts/run_baseline.py').read())"
	@echo "[BAR-44] baseline OK"

test: ## 전체 backend 단위 테스트 (legacy + config)
	@$(PYTHON) -m pytest backend/tests/ -v
