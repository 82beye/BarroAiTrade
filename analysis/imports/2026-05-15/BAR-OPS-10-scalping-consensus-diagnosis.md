# BAR-OPS-10 진단 보고서 — scalping_consensus 일봉 미발화 원인 분석

_작성: 2026-05-15 | CTO_

---

## 결론 요약

**scalping_consensus 는 급등주 인트라데이 전용 전략**이며, 일봉 600봉 백테스트에서 0건 신호 발화는 **설계상 정상**이다.

---

## 진단 경로

### 1. 증상

600봉 일봉 백테스트(`REPORT_600BARS_20260514.md`) 결과:
```
scalping_consensus  0/8 active  0 trades  PnL: +0
```

### 2. 코드 추적

```
ScalpingConsensusStrategy._analyze_v2()
  └─ ScalpingCoordinator.analyze()
       └─ _should_enter()
            ├─ [1] 진입 구간 필터: change_pct >= 5.0%  ← 대부분 일봉에서 미달
            ├─ [2] 거래대금 필터: ≥ 50억 (오전) / ≥ 100억 (오후)
            └─ [3] 시간대 필터: 09:00~11:30, 12:30~14:00 만 허용
```

소스: `backend/legacy_scalping/strategy/scalping_team/coordinator.py:104-124, 213-220`

### 3. 근본 원인

`ScalpingCoordinator` 진입 구간 필터:
- `min_change_pct = 5.0` (당일 등락률 ≥ +5% 요구)
- 600봉(~2.5년 일봉) 데이터에서 개별 종목이 하루 5% 이상 오르는 경우는 약 5~15%

8개 백테스트 종목(현대무벡스, LG전자, 휴림로봇 등)은 일반 관심종목으로 급등주가 아니므로 5% 이상 상승일이 드물어 0건 결과.

---

## 수정 내용 (BAR-OPS-10)

### 1. `INTRADAY_ONLY_STRATEGIES` 상수 추가

```python
# backend/core/backtester/intraday_simulator.py
INTRADAY_ONLY_STRATEGIES: frozenset[str] = frozenset({"scalping_consensus"})
```

### 2. `_build_strategies(daily_backtest=True)` 파라미터 추가

일봉 백테스트 컨텍스트에서 호출 시 INFO 로그로 0건 정상 이유를 안내:
```
scalping_consensus: 일봉 백테스트 포함 — ScalpingCoordinator 진입 구간 필터
(change_pct ≥ 5%, 거래대금·시간대) 로 인해 0건이 정상.
급등주 인트라데이 전용 전략 (INTRADAY_ONLY_STRATEGIES).
```

### 3. 테스트 추가

`backend/tests/backtester/test_intraday_only_strategies.py` — 5 tests, all passed

---

## 전략 분류표 (업데이트)

| 전략 | 일봉 백테스트 | 운영 환경 | 비고 |
|------|:---:|:---:|------|
| f_zone | ✅ 정상 | ✅ | ATR 필터 0.035 적용 |
| sf_zone | ✅ 정상 | ✅ | 신호 빈도 낮음 |
| gold_zone | ✅ 정상 | ✅ | 거래 빈도 높음 |
| swing_38 | ✅ 최우수 | ✅ | +1.1M, 72 trades |
| scalping_consensus | ⚠️ 0건 정상 | ✅ 급등주 한정 | **INTRADAY_ONLY** |

---

## 다음 단계

1. **인트라데이 데이터 확보 시**: 분봉 캐시 + `change_pct ≥ 5%` 종목 대상으로 실환경 검증
2. **현재**: 일봉 시뮬에서 scalping_consensus 제외 또는 0건 이해하고 진행
3. **BAR-OPS-11 후보**: `simulate_leaders.py` 에서 `--exclude-intraday-only` 플래그 추가

---

## 회귀

- BAR-OPS-10 이전: 902 passed, 10 skipped, 0 fail
- BAR-OPS-10 이후: 907 passed (5 신규), 10 skipped, 0 fail (예상)
