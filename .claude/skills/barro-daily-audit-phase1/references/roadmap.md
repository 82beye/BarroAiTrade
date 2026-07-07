# Phase 2~7 로드맵 (Phase 1 머지 후 후속 세션)

매일 저녁 zip 전달 → 도구 실행 → 손실 drill-down → 1~2개 전략 파라미터 fix → 시뮬 검증 → commit·push 반복. 각 Phase 의 튜닝 대상(후속 세션 시작 시 파일·라인 재확인 필수 — 시간이 지나면 시그니처가 바뀔 수 있음).

## Phase 2 — f_zone 튜닝

- 파일: `backend/core/strategy/f_zone.py`
- 튜닝 대상:
  - `impulse_min_gain_pct` — 임펄스 강도 임계값
  - `_score_and_classify` (현재 L427 근처) — 진입 시그널 분류·점수
  - `min_atr_pct` — 변동성 필터
  - `ma_support_tolerance` — 이동평균 지지선 허용 오차
- 진입 조건: drill-down 결과 f_zone 손실 패턴 (예: 5/21 의 f_zone 0/1, −525k) 1건 이상

## Phase 3 — swing_38 튜닝

- 파일: `backend/core/strategy/swing_38.py`
- 튜닝 대상:
  - impulse 강도
  - `fib_tolerance` — 0.382 되돌림 허용폭
  - 진입 시간대 제한 (시초가 노이즈 회피)
- 진입 조건: drill-down 결과 swing_38 손실 패턴 (5/21 의 3/6, −389k)

## Phase 4 — gold_zone 튜닝

- 파일: `backend/core/strategy/gold_zone.py`
- 튜닝 대상:
  - `score_threshold`
  - 시그널 가중치
- 진입 조건: 5/21 의 3/3 익절은 좋은 양상 — 보수적으로 유지하되, 손실 발생 시 점검

## Phase 5 — sf_zone 튜닝

- 파일: `backend/core/strategy/sf_zone.py` + `backend/core/strategy/f_zone.py` 의 `sf_*` 파라미터
- 진입 조건: 5/21 sf_zone 발동 0 — 발동 조건이 너무 빡빡할 가능성

## Phase 6 — 청산 정교화

- 파일: `backend/core/backtester/intraday_simulator.py`
- 튜닝 대상:
  - `_exit_plan_for_strategy(strategy_id)` — 전략별 표준 청산
  - `_scaled_exit_plan(...)` — 단계별 분할 청산 (TP1, TP2, TP3)
  - `_sfzone_atr_exit_plan(...)` — sf_zone ATR 기반 trailing
- 원안의 `holding_evaluator.STRATEGY_EXIT_PROFILES` 는 미존재 — 실제 위치는 위 3 함수
- 진입 조건: drill-down §3 (보유 구간) 에서 "청산 늦음" 또는 "peak 대비 −X%" 태그 반복 발생

## Phase 7 — 종합 회고 보고서

- 출력: `docs/04-report/features/2026-XX-XX-daily-pipeline-retrospective.md`
- 내용:
  - 5/18~ 누적 net 추이 (`analysis/strategy_perf.csv` + 그래프)
  - 전략별 변화 (Phase 2~6 의 before/after)
  - 다음 사이클 진입 조건 (도구 자체의 한계·개선점)

## 완성 기준

사용자 판단 — 매일 진행 후 도달 시 종료. 일반적으로:
- 누적 net 이 안정적 양수 구간 진입
- 전략별 승률 50% 이상 유지
- 손실 종목의 drill-down 결론이 패턴화되지 않음 (단일 원인 미검출 비중 ↑)

## 후속 세션 시작 시 체크리스트

각 Phase 진입 전:

1. `git fetch origin && git rebase origin/main` (BAR-OPS-09 가 살아있다면 main 동기화)
2. `pytest -q` 전체 통과 확인 — Phase 1 회귀 없음
3. `python scripts/_strategy_perf_track.py` 로 현재 누적 성과 확인
4. drill-down 으로 손실 패턴 1~2개 파악 → 그 패턴에 맞춰 Phase N 진입
5. Phase N 의 튜닝 대상 파일에서 시그니처 변화 확인 (시간 경과로 함수명 등이 바뀔 수 있음)
6. PRD 작성 → 별도 스킬 또는 inline 진행
