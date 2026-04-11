---
tags: [paperclip, wbs, planning]
---

# WBS: BarroAiTrade 시스템 구현 계획

> BAR-26 기반 | [[issue-board|Issue Board]] 참조

---

## 전체 WBS 구조

```mermaid
graph TD
    A[Phase 1: 설계] --> B[Phase 2: 핵심 구현]
    B --> C[Phase 3: API + 전략]
    C --> D[Phase 4: Frontend 확장]
    D --> E[Phase 5: 통합 + 배포]

    A --> A1[BAR-14 시스템 설계 ✅]
    A --> A2[BAR-15 아키텍처 ✅]

    B --> B1[BAR-17 Frontend ✅]
    B --> B2[BAR-19 KiwoomGateway ✅]
    B --> B3[BAR-22 RiskEngine ✅]

    C --> C1[BAR-20 Scanner ✅]
    C --> C2[BAR-21 REST API ✅]
    C --> C3[BAR-18 전략 고도화 ✅]
    C --> C4[BAR-24 백테스팅 ✅]

    D --> D1[BAR-23 FE 확장 ⏳]

    E --> E1[BAR-25 통합/배포 ✅]
```

---

## 크리티컬 패스

```
BAR-19(Gateway) → BAR-20(Scanner) → BAR-21(API) → BAR-23(FE) → 완료
     ✅               ✅               ✅           ⏳
```

---

## Sprint 진행 상황

### Sprint 1 — 핵심 인프라 ✅ 완료
| 이슈 | 담당 | 상태 |
|------|------|:----:|
| BAR-19 KiwoomGateway | Backend Engineer | ✅ |
| BAR-22 RiskEngine | Head of Risk | ✅ |

### Sprint 2 — API + 전략 ✅ 완료
| 이슈 | 담당 | 상태 |
|------|------|:----:|
| BAR-20 ScannerService | Backend Engineer | ✅ |
| BAR-21 REST API Routes | Backend Engineer | ✅ |
| BAR-24 백테스팅 엔진 | Head of Research | ✅ |

### Sprint 3 — Frontend 확장 ⏳ 진행 필요
| 이슈 | 담당 | 상태 |
|------|------|:----:|
| BAR-23 Watchlist/Reports/Settings | Frontend Engineer | ⏳ |

### Sprint 4 — 통합/배포 ✅ 완료
| 이슈 | 담당 | 상태 |
|------|------|:----:|
| BAR-25 시스템 통합 | CTO | ✅ |

---

## 남은 작업

- [ ] BAR-23: Frontend 3개 페이지 (Watchlist, Reports, Settings)
- [ ] BAR-28: 한국 주식 시장 분석 + 관심종목 리스트
- [ ] BAR-29: 백테스팅 전략 검증 리포트

---

*[[issue-board|← Issue Board]] | [[../00-index/status-dashboard|상태 대시보드]] | 최종 업데이트: 2026-04-11*
