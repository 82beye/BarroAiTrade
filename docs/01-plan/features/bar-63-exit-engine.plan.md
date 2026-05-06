# BAR-63 — ExitPlan 분할 익절/손절 엔진 (Phase 4 첫 BAR)

**선행**: BAR-45 (ExitPlan 모델 정착) ✅ / Phase 1 5 전략 모두 ExitPlan 사용 ✅

## §0 분리 정책

- **BAR-63a (worktree)**: ExitEngine 신규 + 단위 테스트 + 백테스트 fixture
- **BAR-63b (운영)**: 실 OrderExecutor 통합 + 모의 1주 무사고 검증

## §1 목적

5 전략의 ExitPlan 을 통합 평가하는 단일 엔진. 가격 tick 마다 TP / SL / breakeven / time_exit 발동 여부 판단 → ExitOrder 시퀀스 반환. SOR(BAR-55)로 실제 라우팅.

## §2 FR

- `ExitEngine.evaluate(position, current_price, now) -> list[ExitOrder]`
  - TP1/TP2/TP3 단계별 qty 비율 부분 익절
  - SL 단일 손절
  - breakeven_trigger: TP1 도달 후 SL 을 entry_price 로 이동
  - time_exit: now > entry_time + duration → 강제 청산
- `ExitOrder` 모델 (Pydantic v2 frozen + Decimal): symbol / side / qty / reason / target_price
- `Position` 확장: tp_filled (어느 TP 까지 발동됐는지), sl_at (현재 SL — breakeven 시 갱신)
- 단위 테스트 ≥ 15 (5 전략 × 시나리오)

## §3 NFR
- 회귀 ≥ 411 passed (396 + 15)
- coverage ≥ 70%
- Decimal 정확도

## §4 OOS
- 실 OrderExecutor 통합 (BAR-63b)
- 모의 1주 (BAR-63b)
- ExitPlan 동적 갱신 UI (BAR-65 매매일지)

## §5 DoD
- 15+ tests, 회귀 0 fail
