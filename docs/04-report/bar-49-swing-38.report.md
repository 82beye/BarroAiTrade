---
tags: [report, feature/bar-49, status/done, phase/1, area/strategy]
template: report
version: 1.0
---

# BAR-49 PDCA Completion Report

> **관련 문서**: [[../01-plan/features/bar-49-swing-38.plan|Plan]] | [[../02-design/features/bar-49-swing-38.design|Design]] | [[../03-analysis/bar-49-swing-38.analysis|Analysis]]

> **Feature**: BAR-49 38스윙 신규 포팅
> **Phase**: 1 — 다섯 번째 티켓
> **Date**: 2026-05-06 / **Status**: ✅ Completed / **Match**: 96% / **Iterations**: 0

---

## 1. Summary

38스윙 (Swing-38) 전략 신규 — 임펄스 후 Fib 0.382 되돌림 매수.

진입 3 단계:
1. **임펄스 탐지**: gain ≥ 5% + 거래량 평균 2x 양봉
2. **Fib 0.382 ± 7.5%** 되돌림 zone
3. **반등 캔들**: 마감 > 시가 양봉

가중합: `score = impulse*0.4 + fib*0.4 + bounce*0.2`. 진입 임계 0.3.

ExitPlan: TP1=+2.5% (50%) / TP2=+5% (50%) / SL=-1.5% / breakeven=+1.2%.
position_size: 28%/18%/8%.

74 테스트 통과 (이전 64 + 신규 10), 라인 커버리지 94%, BAR-44 베이스라인 100% 일치.

---

## 2. PDCA Cycle

| Phase | PR |
|---|---|
| Plan #52 / Design #53 / Do #54 / Analyze #55 / Report (this) | ✅ |

---

## 3. Phase 1 진척도

| BAR | 상태 |
|-----|------|
| BAR-45 Strategy v2 | ✅ |
| BAR-46 F존 v2 | ✅ |
| BAR-47 SF존 분리 | ✅ |
| BAR-48 골드존 신규 | ✅ |
| **BAR-49 38스윙 신규** | ✅ (본 PR) |
| BAR-50 ScalpingConsensus | 🔓 진입 — Phase 1 마지막 |

→ Phase 1 **5/6 (83%)**, 잔여 1 티켓

---

## 4. Statistics

| 지표 | 값 |
|---|---|
| 신규 파일 | 2 (swing_38.py + test) |
| LOC 추가 | +390 (코드 220 + 테스트 170) |
| 테스트 | 74 (이전 64 + 신규 10) |
| Match | 96% |
| 베이스라인 변동 | 0건 |

---

## 5. 후속

- BAR-50 ScalpingConsensus — Phase 1 마지막 (12 legacy_scalping 에이전트 가중합)
- Phase 1 종료 시 종합 회고

---

## 6. Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-06 | 초기 — 38스윙 임펄스+Fib+반등 |
