# BAR-OPS-35 — 2026-06-08 매매복기 권고 구현 (재진입·하드손절·회로차단기 latch)

_작성: 2026-06-08 / 브랜치: `feat/BAR-OPS-35-recap-0608-guards` / 근거: `reports/2026-06-08/2026-06-08_매매복기.md`_

## 배경

6/8 당일 종합 -555,299원(-1.11%). **459550 2차 재진입 1건(-509,481원)이 당일손실의 91.7%**.
진입 알파(+799K)는 흑자였고 청산·재진입 규율이 손실을 만듦. 통제 가능 실수 2건(459550 2차 +
두산 09:43 재진입) 제거 시 -555K → +273K 흑자전환.

## 설계 원칙

- **전부 default OFF(no-op)** — 라이브 동작 불변. shadow 운영데이터 확보 후 사용자가 enforce.
  프로젝트 컨벤션(#6 `--entry-revalidate`, #7/#8 게이트와 동일 패턴).
- **전 코드베이스 회귀 0** — 1317 passed / 10 skipped (변경 전후 동일).
- **전략 파라미터(이미 OOS 백테스트 최적화)는 블라인드 변경 금지** — 메모리 규율
  "OOS 검증 선행 필수, HITL 승인 전 코드 적용 금지" 준수.

## 권고 ↔ 구현 매핑

### A. 코드 구현 완료 (default OFF, 테스트 포함)

| 권고 | 우선 | 구현 위치 | 플래그 (default) | 효과 |
|------|:---:|----------|------------------|------|
| 동일종목 당일 재진입 금지 | P0#1 | `supertrend_auto_trader.py` | `max_entries_per_symbol_day=0` | =1 시 459550 2차 차단 |
| 청산 후 재진입 cooldown | P0#1 | 〃 | `reentry_cooldown_min=0` | 매도 N분 내 재진입 차단 |
| 손절 종목 재진입 금지 | P0#1 | 〃 | `block_reentry_after_loss=False` | 당일 손절 종목 추격 차단 |
| catastrophic 하드 손절 | P0#3 | 〃 | `hard_stop_pct=0.0` | 예 -6.0 → 459550 -12.63% 방치 차단 |
| 고변동 테마 진입 억제 | P1 | 〃 | `max_atr_pct_for_entry=0.0` | ATR/price 과대 종목 스킵 |
| 변동성 조정 사이징 | P1 | 〃 | `vol_halve_atr_pct=0.0` | 고변동주 비중 절반 |
| 승자 보유(고정익절→트레일) | P1 | 〃 | `take_profit_trail_only=False` | 고정 +5% 익절 비활성 |
| 추격 매수 가드 | P2 | 〃 | `max_entry_gap_pct=0.0` | 직전봉 대비 급등봉 진입 스킵 |
| DCA sync-loss 방지 | P1 | `active_positions.py` / 〃 | `single_tranche=False` | 전량 단일 filled tranche (보유=tracker 일치) |
| 회로차단기 sticky latch | P0#2 | `live_order_gate.py` | `daily_loss_latch=False` | 한도 도달 시 당일 잠금(회복해도 재개 금지) |
| 주문 retry(매도 우선) | P0#5 | 〃 | `order_retry_count=0`, `retry_sell_only=True` | 매도 HTTPStatusError 재시도(청산지연 방지) |
| audit 요청/체결 분리 | P0#4 | 〃 | (스키마 상시) `filled_qty`·`avg_fill_price` 컬럼 + 자동 migration | 296 vs 178 sync-loss 가시화 |

신규 테스트: supertrend 8건 + active_positions 2건 + live_order_gate 6건 = **16건** (전부 통과).

> **핵심**: 회로차단기 latch(P0#2)는 supertrend 트레이더와 intraday_buy_daemon 이 **동일
> `LiveOrderGate` 를 공유**하므로, `daily_loss_latch=True` 한 번으로 **양 경로 모두** 차단된다.
> 12:30/12:35 차단 후 12:55 회복 재무장 → 14:12 재진입(-509K) 경로가 원천 봉쇄된다.

### B. OOS 백테스트 게이트 후보 (코드 변경 보류 — 검증 선행)

| 권고 | 우선 | 사유 | 검증 경로 |
|------|:---:|------|-----------|
| gold_zone 청산룰(고정 -2% + BB중심선 익절) | P1 | `holding_evaluator.STRATEGY_EXIT_PROFILES.gold_zone` 는 이미 Phase D2.5/S6·S7 그리드서치 최적화 값(stop -4%, tp +4%, partial +2%). 블라인드 변경은 회귀 위험. 6/8 표본 1건(미래에셋)으로 일반화 불가. | `scripts/_oos_validation.py` 로 대안 프로파일 A/B → PASS 시 STRATEGY_EXIT_PROFILES 갱신 |
| f_zone '진짜 눌림(직전고점 N봉 미경신)' 조건 | P1 | `f_zone.py` 진입 정의 변경은 OOS 검증 필요. 현 정의도 백테스트 기반. | `scripts/_oos_validation.py` f_zone 대안 진입 검증 |

### C. 다른 수단으로 충족 / 의도적 설계

| 권고 | 처리 |
|------|------|
| daemon 회로차단기 realized 기반 (P0#2) | **게이트 latch(A)가 daemon 경로도 커버**. realized-basis 는 주석(intraday_buy_daemon.py:763-770)대로 EOD 스냅샷 이슈로 의도적 배제. latch 가 "회복 후 재진입"을 입력값 무관하게 차단하므로 realized 전환 없이도 핵심 문제 해소. |
| daemon cooldown 매도시각 기준 (P0#1) | 6/8 재진입은 supertrend 경로(A에서 해결). daemon(gold/f/sf) 경로는 당일 미발생. 후속 후보. |
| 체결통보 trades.log 기록 (P2) | audit `filled_qty`/`avg_fill_price` 컬럼 도입(A)으로 스키마 준비 완료. executor 체결통보 연동은 후속(키움 MKT 는 접수만 반환). |

## shadow → enforce 운영 가이드

권장 활성화 순서 (각 단계 1~3 거래일 shadow 관측 후 다음 단계):

1. **`daily_loss_latch=True`** (GatePolicy) — 가장 안전·고효과. 회복 후 재진입 원천 차단.
2. **`hard_stop_pct=-6.0`** (SupertrendAutoConfig) — 꼬리리스크 캡. 트레일과 OR.
3. **`max_entries_per_symbol_day=1`** + **`order_retry_count=2`** — 재진입 차단 + 매도 재시도.
4. **`single_tranche=True`** — supertrend sync-loss 해소 (audit/보유 일치).
5. (선택) `max_atr_pct_for_entry` / `vol_halve_atr_pct` / `max_entry_gap_pct` — shadow 진입건수 관측 후.

> 활성화 후 `scripts/_daily_strategy_audit.py --date <D>` 로 진입건수·고점매수율·재진입·승률
> 변화를 측정. 매수 0건화 시 임계 재튜닝.

## 검증

- 전체: `pytest backend/tests/` → **1317 passed, 10 skipped** (변경 전후 동일, 회귀 0).
- 영역: supertrend_auto_trader 51 / active_positions 13 / live_order_gate 17 (risk 전체 84) 통과.
- 모든 신규 플래그 default 값에서 기존 동작 불변(회귀 테스트가 보장).
