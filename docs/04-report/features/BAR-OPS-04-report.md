# BAR-OPS-04 — auth_v2 + OrderExecutors + RUNBOOK Report

## 핵심 통합
- **auth_v2 라우트**: register / login / refresh / mfa-verify — UserRepository + bcrypt 통합 (OPS-01 의 _USER_DB stub 정식 대체)
- **OrderExecutors**: Paper / Kiwoom / IBKR / Upbit Protocol 구현체 (mock 모드 — 운영 진입 시 live 활성화)
- **RUNBOOK.md**: 8 섹션 운영 문서 (KillSwitch / Gateway / Embedding lag / DB pool / 캐시 / 키 회전 / 실거래 / 재해 복구) — alerts.yaml 매핑

## 흡수 b 트랙
- BAR-67b 후속 — auth ↔ UserRepository 정식 통합
- BAR-63b 부분 — OrderExecutor 어댑터 stub (실 API 통합은 BAR-OPS-05+)

## Tests
- 18 신규 / 회귀 605 (587→605, +18)

## OPS 누적 (4 BAR)
| BAR | 흡수 | tests |
|:---:|------|:----:|
| OPS-01 | 67b/68b/71b/73b | 12 |
| OPS-02 | 67b/69b | 18 |
| OPS-03 | 63b/64b/66b | 10 |
| OPS-04 | 67b/63b + RUNBOOK | 18 |
| **합계** | – | **58** |

## 다음 b 트랙
- BAR-OPS-05: Kiwoom OpenAPI 실 통합 (HTTP/WS, OAuth2)
- BAR-OPS-06: 실거래 진입 절차 (자산 5% / 1주 라이브 검증)
- BAR-OPS-07: 모의 침투 테스트 (Semgrep custom rules + 외부팀 검증)
