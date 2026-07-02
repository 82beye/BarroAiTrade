# BarroAiTrade 매매전략 전반 점검 리포트

- **작성일**: 2026-07-01
- **방식**: 멀티에이전트 전략 리뷰(7개 전략/영역 병렬 진단 → 적대적 검증 → 교차분석). 64 서브에이전트, 1491 tool-use.
- **대상**: BarroAiTrade (mock-live, 실금 아님)
- **결과**: 총 58건 — CONFIRMED 39 · PLAUSIBLE 11 · REJECTED 5 · positive 3
- **심각도**: 🔴 critical 5 · 🟠 high 15 · 🟡 medium 22 · 🔵 low 8
- **커버리지 노트**: 활성 핵심전략 supertrend는 초기 배치에서 StructuredOutput 실패로 누락됐으나 **단독 재분석 완료 → 아래 「Supertrend 단독 재분석」 섹션 참조**.

## 요약 (Executive Summary)

활성/준활성 매매전략 전반에서 58건이 적대적 검증을 통과했다(critical 5, high 15). 가장 시급한 것은 **종가베팅 데몬의 크래시 유발 버그 2건**(파일핸들 누수→FD 고갈로 수시간 후 데몬 추락, 날짜파싱 미가드→포지션 파일 오염 시 매도스캔 전체 중단)으로, 크래시 순간 **모든 전략의 보유 포지션이 SL/TP 미감시 상태**가 된다. 그밖에 리스크게이트 분절(일일주문한도가 데몬 300 + supertrend 50로 분리돼 350+ 가능), swing_38의 2차진입(add_on) 미구현, zone 전략군의 파라미터-주석 불일치·트랩가드 전면 OFF·과최적화, 백테스트가 실제 KRX가 아닌 GBM 합성데이터 기반이라는 근본 한계가 확인됐다. **현재 mock이라 실금 손실은 없으나, 실거래 전환 전 반드시 게이트 통합·백테스트 재검증이 선행돼야 한다.**

## 이번에 적용한 조치 (safe, 워크트리 브랜치 패치)

종베 데몬 크래시 버그 3곳을 워크트리 브랜치에 패치했다(정상 입력 동작 불변, 잘못된 입력에서만 방어). **라이브 반영은 main 머지 + `com.barroaitrade.closing-bet` 데몬 재시작 필요(사용자 승인 대상)**.

| 조치 | 위치 | 내용 |
|------|------|------|
| 파일핸들 누수 수정 (CB-001) | `scripts/closing_bet_alert_daemon.py:214,263` | `json.load(open(p))` → `with open(p) as f` context manager (FD 누수 제거) |
| 날짜파싱 방어 (CB-002) | `scripts/closing_bet_alert_daemon.py:199` | `date.fromisoformat()` try-except 래핑 — 오염 entry_date 시 해당 포지션 MORNING/D3만 skip, 전체 스캔 중단 방지 |

> `python3 -m py_compile` 구문 검증 통과. 라이브 데몬(main 체크아웃)은 미변경 — 재시작 전까지 효과 없음.

### [2026-07-01 추가 적용 · 라이브 반영 완료] supertrend 트레일 청산 복원

사용자 지시로 supertrend 트레일 청산(HIGH)을 **라이브에 즉시 반영**했다.

| 항목 | 내용 |
|------|------|
| 변경 | `.env.local`: `SUPERTREND_AUTO_TRAIL_ATR` (주석·기본 0) → **`=3`** |
| 근거값 | dataclass 기본 3.0 + 6/8 백테스트 검증(트레일 단독 -3.27%→+0.93%, TP5%·range0.90 결합 +2.73%). `take_profit_pct`(5.0)·`max_intraday_range_pos`(0.90)는 봇이 오버라이드 안 해 기본값 유지 → 트레일만 켜면 검증 조합 완성 |
| 배포 | `.env.local` 백업(`.env.local.bak.20260702_085504`) 후 편집 → 봇 재시작(`launchctl kickstart -k com.barroai.telegram-bot`) |
| 검증 | 신규 PID 14015(08:55:15 기동, .env 편집 08:55:04 이후) · 기동로그 `🛡 ATR 트레일링 청산 ON — 고점종가 −3×ATR 이탈 시 청산` · 단일 인스턴스(409 없음) · Telegram getMe 200 |
| 롤백 | `.env.local`에서 해당 라인 `=0` 또는 재주석 후 봇 재시작 |
| 부가 | 봇 err 로그의 `poll cycle failed`(ConnectTimeout)은 누적 23,427건의 **기존 만성 이슈**로 이번 변경과 무관(재시작 후 미증가·getMe 정상). supertrend 자동매매 루프는 telegram 폴링과 독립 |

### [2026-07-02 추가 적용 · 라이브 반영 완료] supertrend 일일주문 캡 복원

사용자 지시("남은 supertrend 권고도 적용")로 MAX_ORDERS 무한(HIGH)을 라이브 반영.

| 항목 | 내용 |
|------|------|
| 변경 | `.env.local`: `SUPERTREND_AUTO_MAX_ORDERS` **0(무한) → 100** |
| 값 근거 | 권고는 "50~100 캡". ★6/26에 사용자가 의도적으로 0으로 해제한 설정을 되돌리는 것★이나, 당시 문제는 *낮은* 캡(15)이 정상주문을 차단한 것이었고, 100은 넉넉(supertrend max_pos=10 → 10회 로테이션분)해 정상매매는 안 막으면서 429 폭주(6/15式)만 백스톱 |
| 배포 | 백업(`.env.local.bak.20260702_100937`) 후 편집 → 봇 재시작. **장중(10:09 KST) 적용** — 오늘 매수 6건(≪100)이라 즉시차단 없음, ~75s 다운타임 |
| 검증 | 신규 PID 14775(10:09:47 기동, .env 편집 10:09:37 직후) · 트레일 ON 유지 · 단일 인스턴스 · getMe 200 |
| 롤백 | `.env.local` 해당 라인 `=0` 후 봇 재시작 |
| 잔여 | 게이트 분절(#3)은 미해소 — 데몬 일반전략은 여전히 policy.json=300, supertrend=100로 별도 카운터. 근본 통합은 코드변경(아래 보류 항목) |

### [보류] 나머지 supertrend 권고 — 자동적용 부적절, 사용자 확인 필요

| 권고 | 왜 보류했나 | 필요 조치 |
|------|------------|-----------|
| **entry_lookback=100 → 5~10 축소** (risky_hitl) | 권고 자체가 "**OOS 재검증 필요**"를 명시. 진입빈도가 급변하는 라이브 로직 변경을 검증 없이 적용하면 매매행태가 예측불가로 바뀜 | 실 KRX 데이터 백테스트로 5/10/20 후보 비교 후 값 확정 → 그 다음 env 반영 |
| **게이트 분절 통합** (risky_hitl) | 봇/데몬이 `order_audit.csv` 전역 카운트를 공유하나 캡은 분리(supertrend 100 / 데몬 300). 통합은 `live_order_gate.py`/`GatePolicy` **코드·구조 변경** + 회귀테스트 필요 | 브랜치에서 통합 글로벌 캡 구현 + 테스트 후 머지(HITL) |

## 교차전략 위험 (Cross-Strategy)

# BarroAiTrade 멀티 전략 결함 분석 — Cross-Strategy 위험 & Top3 조치

## (1) 전략 간 상호작용 위험 (Top 5)

### **① 파일핸들 누수(CB-001) → 모든 전략 cascade 무인화**
- **문제**: daemon scan loop에서 파일 512+ 누적 미해제 → 1~2시간 운영 후 ulimit 도달 → OSError → 모니터링 daemon 추락
- **영향**: 크래시 순간 supertrend, swing_38, f_zone, sf_zone, gold_zone의 모든 활성 포지션이 SL/TP/TIME_EXIT 미체크. 15분 재스캔 루프도 다시 512+ 누적 가능 → 악순환
- **심각도**: Critical (모든 전략 공통 위험)

### **② Daily order budget 불명확 (RG-002, RG-013)**
- **문제**: policy.json=300 vs supertrend env='50', 그리고 DCA가 daily_max_orders에 암묵 포함
- **시나리오**: 급락장 6/10처럼 20개 포지션이 trough PnL 진입 시 DCA 20개 주문 → budget 280으로 축소 → 신규진입 예산 부족. supertrend와 daemon의 우선순위 정책 명시 부재
- **영향**: margin stress 기간 order pool 소진 → risk gate 무효화

### **③ Overnight gap 비대칭 (RG-005, RG-006)**
- **문제**: supertrend는 carry_gap_stop_pct=-3.0 자동 exit / daemon(f_zone, gold_zone) 미보호
- **사건**: 6/10 gap -12.63% 시 supertrend는 ATR trail로 생존, daemon은 손실 누적
- **영향**: 차기 gap event 시 daemon 포지션만 강제 청산, 설계 비대칭

### **④ Reentry 정책 비대칭 (RG-006)**
- daemon: 30min hard cooldown / supertrend: unlimited (max_entries_per_symbol_day=0, 기본값)
- 459550 6/8: 1st exit +58K, re-entry -509K (supertrend만 허용) → design inconsistency

### **⑤ Concurrent position gate 오버플로우 (CB-006)**
- policy.json max_concurrent_positions=10 vs CB 설계 2-3 positions
- CB auto-buy + 다른 전략 합산 → 10 positions × 3일 × 5% SL = -150% 누적 risk
- daily_loss_limit -3%와 충돌 가능

---

## (2) 미검토 전략/경로 (주요 6가지)

| 전략 | 미검토 사항 | 영향 |
|------|-----------|------|
| **limit_up_chase** | 백테스트 성과지표 전무, dry-run 1.5주만 (신호부족 구간 포함) | Real money 성공 미보장 |
| **swing_38: add_on_signal()** | daemon 코드 미구현 | backtest PF 7.36 → 실제 -30~50% 손실 (Day2+ reentry 미실행) |
| **swing_38: synthetic GBM** | 실KRX 패턴(volume clustering, theme reversion, overnight gap) 미포함 | CAGR +1.8% 재현 보증 불가 |
| **zones_fgs (비활성)** | 복구 시 RSI 35~38 과도, Fib 범위 inconsistency, Trap Guard OFF | 복구 후 손실 사례(5/21 LG전자 -626k 등) 재발 위험 |
| **supertrend daily_max_orders** | 50 vs policy 300 불일치, 의도 불명 ('[0609 임시해제]') | policy.json과 동기화 필요 |
| **round_figure_stop: supertrend 미통합** | RF_STOP_ENABLED=1 시 다른 4전략은 RF widening, supertrend만 기본 SL | 2-tier SL inconsistency |

---

## (3) Top 3 시급 조치

### **1️⃣ [Critical] 파일핸들 누수 수정 (CB-001)**
- **조치**: daemon scan loop 파일 open/close 확인, context manager 도입
- **근거**: 모든 포지션의 unguarded 상태 → 총손실 확대 위험
- **일정**: 즉시 (다음 배포)

### **2️⃣ [Critical] Entry date 파싱 강화 + 현금버퍼 gate 검증 (CB-002, CB-003)**
- **조치**:
  - CB-002: try-except 강화, ISO 형식 정규화, fallback 처리
  - CB-003: balance_history.json 존재 여부 명시 체크 + 기본값 설정 (또는 startup error)
- **근거**: CB-003 미해제 시 실금 전환 → margin stress 미감지 → forced liquidation. CB-002 crash → 3일 후 강제EXIT 놓침
- **일정**: 즉시 (다음 배포)

### **3️⃣ [High] Daily order budget + Overnight gap 비대칭 해소 (RG-002, RG-005, RG-013)**
- **조치**:
  - policy.json: DCA 별도 pool 명시 또는 daily_max_orders 상향 + 우선순위 정책 문서화
  - daemon: carry_gap_stop_pct=-3.0 적용 (f_zone, gold_zone carry-over)
  - supertrend: daily_max_orders를 policy.json과 동기화, '[0609 임시해제]' 의도 명문화
- **근거**: risk gate 일관성 부재 → margin stress 미보호 → 총손실 확대
- **일정**: 1주 이내 (정책 검토 후 반영)

---

## 보충 경고
- **swing_38 add_on_signal() 미구현**: 현재 설계와 백테스트 가정 불일치 → 즉시 수정 또는 전략 비활성화 with warning
- **zones_fgs**: 복구 전 파라미터 재검증 필수 (6월 데이터 overfit 우려)
- **limit_up_chase**: forward test 진행 중 → 1개월 추가 관찰 후 판단

## 전략별 상태 요약

- **limit_up_chase**: Real trading vulnerability identified: Orderbook wall gate auto-passes when sell orders=0 (line 319), creating limit-up lock risk. Daily loss limit integration unconfirmed (daily_pnl_pct passed but LiveOrderGate enforcement unknown). Backtesting validation absent—only dry-run observation from 2026-06-12 incident. Mock-live environment confirmed (mockapi), so no actual fund risk current, but transition to real API requires gate verification.
- **closing_bet**: The CB strategy core logic is sound and currently inactive (signal-only via alerts). However, 3 critical production bugs prevent safe activation: unclosed file handles in repeated scans cause FD exhaustion after hours; unguarded date parsing crashes sell monitoring; cash buffer gate silently fails when balance_history.json missing. Additionally, overnight time_exit evaluation bug and incomplete auto-sell wiring require design fixes before live deployment.
- **swing_38**: Swing_38 strategy contains 10 verified findings: 1 critical (add_on_signal unused), 3 high (min_hold gate, base_candle_low fallback, no revalidate), 6 medium (param drift, Fib tolerance, synthetic backtest, etc.). When live: expect actual return 30-50% below simulation; min_hold_days forced 3-day holds amplify losses on bad entries; add_on_signal 2nd tranche never executes. Backtest run on GBM synthetic, not KRX real data — microstructure mismatch. Requires code fixes (needs_restart) + HITL ops approval before 7-figure daily deployment.
- **zones_fgs**: 세 zone 전략에서 파라미터 불일치(gold_zone RSI 35→38 vs docstring 30→40), 과최적화 위험(min_score=5.0 6월만 검증), 룩어헤드 바이어스(bounce 캔들 진행 중 가능성), 리스크 게이트 누락(ATR 필터 gold_zone 미적용, 트랩가드 모두 OFF)이 발견됨. 현재 모든 zone 전략이 비활성(supertrend 검증 모드) 상태이나, 복구 시 이슈 발생 가능성 높음. 실거래 전환 시 손실 위험 상당.
- **round_figure_stop**: Found 8 findings across logic bugs, risk gates, parameter risks, and operational readiness. Most are low-to-medium severity with 2 medium/high concerns around SL edge cases and supertrend RF integration gap. Currently running in mock mode (DRY_RUN=1, RF_STOP_DRY_RUN=1) which masks actual impact. Risk gates are layered but have intentional gaps that require careful monitoring. Backtesting on synthetic data introduces overfitting risk.
- **risk_integration**: High-severity fragmentation of daily_max_orders gate discovered: daemon general strategies (300 limit) + supertrend (50 limit) = separate gate instances, allowing potential 350+ orders/day vs intended 300. Daily PnL input diverged between paths until recent fix. Overnight gap-stop protection implemented in supertrend but missing in daemon. ADX/FLIP whipsaw params optimized on 4-6mo sample with out-of-sample still negative. Reentry cooldown policy asymmetric. Overall: mock-live safe but production conversion requires gate unification audit.

## 권고 우선순위 (미적용 — 재시작/HITL)

fix_safety 분류: safe_auto 27 · needs_restart 14 · risky_hitl 9

1. **[CRITICAL] CB-003 현금버퍼 fail-open** — `balance_history.json` 없으면 현금버퍼 게이트가 조용히 무력화(주문가능액 무시하고 매수). 파일 부재 시 에러로그+게이트 유지로 변경. (needs_restart)
2. **[HIGH] 리스크게이트 통합 (RG-002/005/006)** — 일일주문한도 데몬(300)+supertrend(50) 분절, 오버나잇 갭스탑 supertrend만 적용, 재진입 쿨다운 비대칭. 게이트 단일화 감사 필요. (needs_restart)
3. **[HIGH] swing_38 add_on 2차진입 미구현 (SWING38-001)** — 백테스트는 분할진입(PF 7.36) 가정이나 데몬은 1차만 실행 → 실성과 30~50%↓ 예상. (needs_restart)
4. **[HIGH] limit_up_chase 호가벽 게이트 zero-ask auto-pass (P-WALL-1)** — 매도호가 0일 때 게이트 통과 → 상한가 락 청산불가 위험. (safe_auto 코드수정)
5. **[HIGH] zone 전략군 (ZNE001~007)** — RSI/Fib 파라미터-주석 불일치, min_score 6월 과최적화, ATR필터 gold_zone 누락, 트랩가드 전면 OFF. 복구 전 정비 필요. (needs_restart/HITL)
6. **[근본] 백테스트 실데이터 재검증** — swing_38 등 GBM 합성데이터 검증 → 실 KRX 데이터로 재검증 없이 실금 전환 금지. (HITL)

## 전체 발견사항

| # | 심각도 | 판정 | ID | 전략 | 분류 | 제목 | fix_safety |
|---|--------|------|----|----|----|------|-----------|
| 1 | 🔴 critical | CONFIRMED | CB-001-FILEHANDLE-LEAK | closing_bet | logic_bug | File handle leak in repeated scan loops | safe_auto |
| 2 | 🔴 critical | CONFIRMED | CB-002-ENTRY-DATE-PARSE-UNGUARDED | closing_bet | edge_case | Unguarded date.fromisoformat() can crash sell scan | safe_auto |
| 3 | 🔴 critical | CONFIRMED | CB-003-BALANCE-HISTORY-FAILOPEN | closing_bet | risk_gate_gap | Cash buffer gate silently disabled when balance_history.json missing | needs_restart |
| 4 | 🔴 critical | CONFIRMED | SWING38-001 | swing_38 | logic_bug | add_on_signal() second-entry completely non-functional in daemon — no call code | needs_restart |
| 5 | 🔴 critical | CONFIRMED | ZNE002 | zones_fgs | edge_case | Lookahead Bias — bounce 캔들이 진행 중일 수 있음 (f_zone) | risky_hitl |
| 6 | 🟠 high | CONFIRMED | CB-004-AUTO-SELL-UNIMPLEMENTED | closing_bet | logic_bug | Auto-sell logic not wired; sell_signals() generates alerts only | risky_hitl |
| 7 | 🟠 high | CONFIRMED | CB-005-OVERNIGHT-TIME-EXIT-BUG | closing_bet | lookahead_bias | time_exit evaluated on entry day without overnight flag → same-day liquidation | needs_restart |
| 8 | 🟠 high | CONFIRMED | P-WALL-1 | limit_up_chase | logic_bug | Orderbook wall gate auto-pass on zero asks—limit-up lock risk | safe_auto |
| 9 | 🟠 high | CONFIRMED | P-TEST-1 | limit_up_chase | edge_case | Backtesting validation absent—only dry-run observation from 2026-06-12 incident | needs_restart |
| 10 | 🟠 high | CONFIRMED | RG-003 | risk_integration | logic_bug | Daily PnL gate input formerly broken (6/10 realized), now fixed in daemon but supertrend may lag | safe_auto |
| 11 | 🟠 high | CONFIRMED | RG-004 | risk_integration | param_risk | Whipsaw filter parameters (ADX≥30, FLIP≥1.5) optimized in-sample on 4-6mo backtest; out-of-sample still negative | risky_hitl |
| 12 | 🟠 high | CONFIRMED | RG-005 | risk_integration | logic_bug | Overnight position gap-stop protection only in supertrend; daemon has none for non-swing_38 carry-overs | safe_auto |
| 13 | 🟠 high | CONFIRMED | RG-006 | risk_integration | edge_case | Reentry cooldown policy asymmetric: daemon 30min hard cap vs supertrend max_entries_per_symbol_day (default OFF) | safe_auto |
| 14 | 🟠 high | CONFIRMED | SWING38-006 | swing_38 | param_risk | BARRO_SWING38_SL_PCT env overrides backtest assumption (-15% → -8% possible) | needs_restart |
| 15 | 🟠 high | PLAUSIBLE | SWING38-009 | swing_38 | lookahead_bias | Backtest used GBM synthetic data, not KRX historical — real pattern mismatch risk | needs_restart |
| 16 | 🟠 high | CONFIRMED | ZNE001 | zones_fgs | param_risk | gold_zone RSI 파라미터 vs docstring 불일치 — 의도 불명확 | risky_hitl |
| 17 | 🟠 high | CONFIRMED | ZNE003 | zones_fgs | param_risk | gold_zone min_score=5.0 과최적화 — 6월 데이터만 out-sample 검증 | needs_restart |
| 18 | 🟠 high | CONFIRMED | ZNE004 | zones_fgs | risk_gate_gap | ATR% 필터 불일치 — gold_zone에만 orchestrator override 미적용 | needs_restart |
| 19 | 🟠 high | CONFIRMED | ZNE005 | zones_fgs | logic_bug | Fib 레벨 범위 vs docstring 불일치 — 0.382~0.618 vs 0.236~0.786 | needs_restart |
| 20 | 🟠 high | CONFIRMED | ZNE007 | zones_fgs | risk_gate_gap | Trap Guard(6월 트랩 방어) 모든 파라미터 0 — 가짜 상승 방어 미활성 | risky_hitl |
| 21 | 🟡 medium | CONFIRMED | CB-006-POLICY-JSON-MISMATCH | closing_bet | param_risk | policy.json concurrent_positions=10 exceeds CB design intent of 2-3 positions | safe_auto |
| 22 | 🟡 medium | CONFIRMED | CB-007-JSON-PARTIAL-WRITE-RISK | closing_bet | edge_case | Non-atomic JSON save can corrupt closing_bet_positions.json on daemon crash | safe_auto |
| 23 | 🟡 medium | CONFIRMED | CB-008-CASH-BUFFER-SIZING-GAP | closing_bet | edge_case | Integer division qty sizing can result in zero position when cash tight | safe_auto |
| 24 | 🟡 medium | CONFIRMED | P-PARAM-1 | limit_up_chase | param_risk | Bid/ask ratio threshold (3.0) optimized?—no sensitivity analysis | safe_auto |
| 25 | 🟡 medium | CONFIRMED | P-LOGIC-2 | limit_up_chase | logic_bug | Lookahead bias in orderbook fetch—lag between ka10004 and place_buy order | safe_auto |
| 26 | 🟡 medium | CONFIRMED | P-CONFIG-1 | limit_up_chase | param_risk | Parent config inheritance missing exclude_etf, exclude_leverage, max_total_position_ratio | safe_auto |
| 27 | 🟡 medium | PLAUSIBLE | P-LOGIC-3 | limit_up_chase | edge_case | Reentry guard max_entries_per_symbol_day=1 may be too strict for intraday churn | needs_restart |
| 28 | 🟡 medium | CONFIRMED | RG-002 | risk_integration | risk_gate_gap | DCA (averaging down) subsumed under daily_max_orders without explicit pooling; no separate DCA budget | needs_restart |
| 29 | 🟡 medium | CONFIRMED | RG-007 | risk_integration | edge_case | Penny stock filtering inconsistency: supertrend hard min_price=1000won, daemon BARRO_MIN_ENTRY_PRICE env-gated (default OFF) | safe_auto |
| 30 | 🟡 medium | CONFIRMED | RG-009 | risk_integration | logic_bug | DCA calculation may clip to 0 qty (invalid_qty) when small position + low fill % triggers phantom tranche inflation | safe_auto |
| 31 | 🟡 medium | CONFIRMED | RG-010 | risk_integration | param_risk | Take-profit and ATR-trail exit thresholds (take_profit_pct=5%, trail_atr_mult=3.0) may not survive market regime changes post-optimization | risky_hitl |
| 32 | 🟡 medium | CONFIRMED | RG-012 | risk_integration | edge_case | Intraday position reconcile (_reconcile_position_qty) checks broker qty but doesn't account for pending DCA tranches vs actual filled tranches | risky_hitl |
| 33 | 🟡 medium | CONFIRMED | RG-013 | risk_integration | logic_bug | SupertrendAutoTrader daily_max_orders env default '50' does NOT match policy.json '300'; unclear if intentional for supertrend throttling | safe_auto |
| 34 | 🟡 medium | CONFIRMED | RF-002 | round_figure_stop | risk_gate_gap | Supertrend strategy missing RF stop loss integration — inconsistent with other 4 strategies | needs_restart |
| 35 | 🟡 medium | CONFIRMED | RF-003 | round_figure_stop | param_risk | Implicit unit assumption in swing_38 resolve_sl_pct call — brittle contract | safe_auto |
| 36 | 🟡 medium | CONFIRMED | LB-001 | round_figure_stop | lookahead_bias | Strategy entry signals use current candle close before bar completion — potential lookahead bias in live execution | risky_hitl |
| 37 | 🟡 medium | PLAUSIBLE | RF-004 | round_figure_stop | risk_gate_gap | 2-tier SL system creates intentional gap between strategy-level and holding-level risk gates | safe_auto |
| 38 | 🟡 medium | PLAUSIBLE | BT-001 | round_figure_stop | param_risk | Backtest-derived parameters tuned on synthetic GBM data — significant overfitting risk to real market microstructure | needs_restart |
| 39 | 🟡 medium | CONFIRMED | SWING38-004 | swing_38 | edge_case | entry_revalidate() does not support swing_38 — intraday signal mismatch | risky_hitl |
| 40 | 🟡 medium | PLAUSIBLE | SWING38-005 | swing_38 | logic_bug | require_daily_candles timestamp check may fail on UTC/KST boundary | safe_auto |
| 41 | 🟡 medium | PLAUSIBLE | SWING38-008 | swing_38 | edge_case | penny stock / high-beta filters missing — trap_guard default-OFF | safe_auto |
| 42 | 🟡 medium | CONFIRMED | ZNE008 | zones_fgs | logic_bug | sf_zone entry_time_cutoff 파라미터 전파 불명확 — 위임 패턴 부작용 | safe_auto |
| 43 | 🔵 low | CONFIRMED | P-TIME-1 | limit_up_chase | edge_case | Entry cutoff time (14:00) prevents late-day positions but daily mode forces EOD close anyway | safe_auto |
| 44 | 🔵 low | PLAUSIBLE | P-LOGIC-1 | limit_up_chase | logic_bug | Gap partial fill rounding loss—int(held × 0.5) may silently lose shares | safe_auto |
| 45 | 🔵 low | CONFIRMED | CC-001 | round_figure_stop | perf_concern | Round-figure support/resistance calculations scan all tier boundaries — O(n) for each strategy call | safe_auto |
| 46 | 🔵 low | PLAUSIBLE | RF-001 | round_figure_stop | edge_case | Round-figure SL calculation fallback logic creates unexpected behavior on edge cases | safe_auto |
| 47 | 🔵 low | PLAUSIBLE | CB-001 | round_figure_stop | logic_bug | Closing_bet metadata fib_stop calculation may be None, causing max() comparison to fail silently | safe_auto |
| 48 | 🔵 low | CONFIRMED | SWING38-010 | swing_38 | edge_case | max_hold_days=20 forced exit may lock-in losses near deadline — TIME_EXIT at -10% vs SL -15% | needs_restart |
| 49 | 🔵 low | PLAUSIBLE | SWING38-003 | swing_38 | param_risk | base_candle_low missing when add_on_signal triggered — fallback to avg_price*0.99 unreliable | safe_auto |
| 50 | 🔵 low | PLAUSIBLE | SWING38-007 | swing_38 | edge_case | Fib retrace score harsh on close-to-high positions — misses 0.382+eps | safe_auto |

## 상세 (critical · high)

### 🔴 [CRITICAL] CB-001-FILEHANDLE-LEAK (closing_bet) — File handle leak in repeated scan loops
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`safe_auto`
- **위치**: `scripts/closing_bet_alert_daemon.py:210-215,258-267`
- **근거**: L210-215 _cache_price() uses json.load(open(p)) without closing. L258-267 _load_daily() same pattern. Called repeatedly in daemon loop (L330-334: BUY_WINDOW scan, L333-334: SELL scan). Each daily scan loads multiple symbols × unclosed file descriptors → cumulative FD exhaustion.
- **영향**: Mock daemon runs for hours continuously. After 512-1024 unclosed files, system reaches ulimit → OSError 'too many open files' → entire monitoring daemon crashes. Positions unmanned during critical windows (morning exit @ 10:00, SL/TP checks).
- **수정안**: Change json.load(open(p)) to json.loads(Path(p).read_text()) or use context manager: with open(p) as f: json.load(f). Also in L214 _cache_price().

### 🔴 [CRITICAL] CB-002-ENTRY-DATE-PARSE-UNGUARDED (closing_bet) — Unguarded date.fromisoformat() can crash sell scan
- **분류/판정**: edge_case / CONFIRMED · fix_safety=`safe_auto`
- **위치**: `scripts/closing_bet_alert_daemon.py:199`
- **근거**: L199: ed = date.fromisoformat(pos['entry_date']) — no try-except. If closing_bet_positions.json is manually edited or corrupted (entry_date not ISO 8601 format), ValueError halts entire scan_sell() loop. L227-254: positions iteration breaks mid-way → remaining positions unevaluated.
- **영향**: Production scenario: user edits position file with 'entry_date': '2026/06/22' (US format) or typo → daemon crashes at first position → all other positions' SL/TP/TIME_EXIT alerts never fire. Risk of holding past D3, missing TP, or breaching SL undetected.
- **수정안**: Wrap in try-except: try: ed = date.fromisoformat(...) except ValueError: skip position with warning. Or validate entry_date at --add time.

### 🔴 [CRITICAL] CB-003-BALANCE-HISTORY-FAILOPEN (closing_bet) — Cash buffer gate silently disabled when balance_history.json missing
- **분류/판정**: risk_gate_gap / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `scripts/closing_bet_alert_daemon.py:117-127,148-152`
- **근거**: L117-127: _cb_equity_estimate() catches ALL exceptions (L125: 'except Exception') and returns 0.0. If balance_history.json doesn't exist or is corrupted, function returns 0. L148-152: if _eq > 0 check → when _eq=0, gate condition skips entirely. BARRO_CB_MIN_CASH_PCT policy is never enforced.
- **영향**: Dev environment: balance_history.json may not exist. Mock-live daemon auto-buys without cash buffer check. Real money transition: orders placed during margin stress (bearish market, cash drying up) → gap overnight → forced liquidation at open. Design intent (2026-06-24 discussion) was to block new CB entries when cash <30% of equity; this completely fails.
- **수정안**: Log error when balance_history.json missing/unreadable (alert operator). Raise exception instead of fail-open. Or pre-populate balance_history.json at daemon startup from account snapshot.

### 🔴 [CRITICAL] SWING38-001 (swing_38) — add_on_signal() second-entry completely non-functional in daemon — no call code
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/core/strategy/swing_38.py:307-399, scripts/intraday_buy_daemon.py:695-702`
- **근거**: swing_38.add_on_signal() fully implemented but NEVER called in daemon._evaluate_and_sell() or _scan_and_buy() loops. Daemon comment L696 mentions 'self-split-entry' but only explanation, no actual code. _NO_DCA_STRATEGIES includes swing_38 → daemon DCA also disabled. Second tranche never triggered.
- **영향**: Design promises 'Day1 entry + Day2+ reentry if support held' yielding separate P&L components, but only Day1 executes. Backtest assumed split-entry (55% win rate, 7.36 PF, +5.34 edge): real execution loses ~30-50% of expected profit. When real-money: actual return far below simulation.
- **수정안**: Call add_on_signal() in _evaluate_and_sell() for each swing_38 position daily. Pass base_candle_low from position.metadata['base_candle_low'] or fetch Day1-entry candle low from broker. Execute second tranche with size_ratio metadata. Remove swing_38 from _NO_DCA_STRATEGIES (self-split bypasses daemon DCA already).

### 🔴 [CRITICAL] ZNE002 (zones_fgs) — Lookahead Bias — bounce 캔들이 진행 중일 수 있음 (f_zone)
- **분류/판정**: edge_case / CONFIRMED · fix_safety=`risky_hitl`
- **위치**: `backend/core/strategy/f_zone.py:508-542`
- **근거**: _detect_bounce()에서 current=df.iloc[-1](마지막 봉)의 close를 이용해 bounce_gain_pct 계산. 코드 주석 '(오래된→최신 순, API 반환 순서)'라 했으나, 실시간 mkt 데이터 수신 시 마지막 봉이 아직 진행 중이면 close가 미확정 상태. 또한 bounce_volume_ratio 계산도 현재 봉의 volume을 사용하므로, tick-by-tick 수신 환경에선 값이 변함.
- **영향**: 실거래 환경(1분봉/5분봉 진입)에서 마지막 봉이 진행 중일 때 신호 발생 시, 백테스트와 다른 결과 발생. 특히 진입가(current_price=candles[-1].close)가 최종이 아니면 슬리피지 발생. 또한 시장 고꿈(gap up) 다음날 첫 1분봉이 아직 진행 중에 진입 신호 발생 → 실제 진입 시 예상과 다른 가격 체결 가능.
- **수정안**: candles 리스트가 '종료된 봉'만 포함하도록 API 수신 단계에서 명확화. 또는 마지막 봉을 제외하고 분석(df.iloc[:-1] 사용). 진입가도 이전 봉의 close 사용 고려. 또는 실시간 환경에서는 bounce 캔들이 완전히 종료된 후(다음 봉 시작 후) 신호 반환 구조 필요.

### 🟠 [HIGH] CB-004-AUTO-SELL-UNIMPLEMENTED (closing_bet) — Auto-sell logic not wired; sell_signals() generates alerts only
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`risky_hitl`
- **위치**: `scripts/closing_bet_alert_daemon.py:10,244-250; backend/core/strategy/closing_bet.py:348-391`
- **근거**: Daemon docstring L10: 'sell alerts sent, no auto-sell'. L244-250: scan_sell() computes exit conditions (TP/SL/MORNING/D3) → generates notify text → awaits notify() only. No order executor invoked. closing_bet.py L348-391 exit_plan() returns ExitPlan object (conditions) but caller must invoke executor separately.
- **영향**: By design, sell is alert-only. But inconsistent with auto-buy (L130-177: _cb_auto_buy() executes place_buy immediately). Live transition will require manual sell on every exit signal. Risk: trader misses alert window → positions age past D3 (forced EXIT) or miss TP in morning spike.
- **수정안**: Implement _cb_auto_sell() mirror of _cb_auto_buy(). Gate with BARRO_CB_SELL_AUTOEXEC env var. Or maintain alert-only but document requirement for trader monitoring.

### 🟠 [HIGH] CB-005-OVERNIGHT-TIME-EXIT-BUG (closing_bet) — time_exit evaluated on entry day without overnight flag → same-day liquidation
- **분류/판정**: lookahead_bias / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/core/strategy/closing_bet.py:353-355`
- **근거**: closing_bet.py L348-356 comments warn: 'ExitEngine evaluating time_exit as same-day time would cause entry-day forced-close bug'. exit_plan() returns time_exit=dtime(10,0) unconditionally (L387). If ExitEngine does not skip same-day time_exit evaluation, position entered at 15:00 would liquidate at 10:00 next iteration—or incorrectly at 10:00 same day. limit_up_chase pattern shows: _maybe_gap_partial() checks entry_date < today (L305 in grep output) to exclude same-day entries.
- **영향**: Currently strategy is inactive (non-trading). If activated, overnight positions entering 15:00-15:20 KST need next-day 10:00 exit, not same-day 10:00. Without ExitEngine checking overnight flag, morning_exit_time gate is broken → entry and exit in same session → overnight strategy becomes day-trade → thesis invalidated.
- **수정안**: ExitEngine must check position.metadata.get('overnight') flag or entry_date < today before evaluating time_exit. Alternatively, closing_bet exit_plan() should NOT return time_exit for same-day, only for next-day+.

### 🟠 [HIGH] P-WALL-1 (limit_up_chase) — Orderbook wall gate auto-pass on zero asks—limit-up lock risk
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`safe_auto`
- **위치**: `backend/core/limit_up_chase_trader.py:319-320`
- **근거**: if aq <= 0: return True # asks=0 → auto-pass. Kills bid/ask ratio check, permitting entry into limit-locked stocks (near 1% of high-flu universe) with zero sell orders.
- **영향**: 진입 후 상한가 락으로 청산 불가. mock-live에선 미체결로 처리되지만, 실거래 전환 시 자기 주문만 남음. 1일 오버나잇 강제.
- **수정안**: ask≥threshold 조건 추가. 예: if aq < top_bid_value * 0.1: return False (매도 잔량 최소 매수잔량금액의 10% 이상).

### 🟠 [HIGH] P-TEST-1 (limit_up_chase) — Backtesting validation absent—only dry-run observation from 2026-06-12 incident
- **분류/판정**: edge_case / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/tests/test_limit_up_chase_trader.py`
- **근거**: Test coverage: momentum band pass, wall gate, entry full path, gap partial, time gate, EOD force. Zero tests for: insufficient cash, mass concurrent entry (10 pos @ 3% each = 30% of deposit), limit-lock reentry, overnight gap 유동성 (morning spike then fade pattern, 상따 진입 후 당일 -5~10% 추격락 등).
- **영향**: strategy_id='limit_up_chase' LIVE이나 백테스트 성과 지표(PF, 승률, MDD, CAGR) 공식 문서 전무. 2026-06-12 원익IPS 사례(호가벽 게이트 제거)는 ad-hoc 수정. live dry-run 검증 1.5주 데이터만 (신호부족으로 진입 0건 구간도 포함).
- **수정안**: 상따 전용 백테스트 시나리오: (1) 20~27% 등락률 생성·상한가 모방 (2) 호가벽 시뮬 (3) 당일/익일 갭하락·페이드 패턴 (4) 4~6월 실제 tick data 재검증. Backtest-Validation-Report.md 갱신 with 상따 성과.

### 🟠 [HIGH] RG-003 (risk_integration) — Daily PnL gate input formerly broken (6/10 realized), now fixed in daemon but supertrend may lag
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`safe_auto`
- **위치**: `backend/core/risk/daily_gate_input.py:109, scripts/intraday_buy_daemon.py:1303, backend/core/supertrend_auto_trader.py:315-319`
- **근거**: compute_daily_gate_input (daily_gate_input.py) uses (ka10074 realized + kt00018 eval) / estimated_deposit — brighter input. Daemon uses this at 1303. SupertrendAutoTrader._account_pnl_pct (estimated at line 315-319 comments) is custom implementation. grep supertrend_auto_trader.py for 'pnl_pct' method shows definition at line ~950. Must verify if ST uses same ka10074+eval formula or still balance.total_pnl_rate (the broken 6/10 input that reset on zero holdings).
- **영향**: If supertrend still uses old balance.total_pnl_rate: (1) during 09:05 liquidation of carry-overs, holdings drop to 0→total_pnl_rate resets to 0%→false unlock of buy gate despite realized losses. (2) daemon and supertrend see different gate inputs, inconsistent reject/allow decisions on same minute.
- **수정안**: Verify supertrend._account_pnl_pct() implementation. If it does not use compute_daily_gate_input (shared), refactor to call compute_daily_gate_input(self._account, balance) for consistency.

### 🟠 [HIGH] RG-004 (risk_integration) — Whipsaw filter parameters (ADX≥30, FLIP≥1.5) optimized in-sample on 4-6mo backtest; out-of-sample still negative
- **분류/판정**: param_risk / CONFIRMED · fix_safety=`risky_hitl`
- **위치**: `backend/core/supertrend_auto_trader.py:96-102`
- **근거**: Comments at lines 96-102: 'ADX≥25/FLIP≥1.0 is PF 1.76→2.00 in backtest. 6/2 real trading is unfiltered (0) so lossy. On 4~6mo constraint sweep: adx≥30·flip≥1.5 yield trades 125→30, MDD -41→-22, pnl -0.08→+0.20. out-of-sample is still negative (weak strategy)'. Default config: min_adx=30.0, min_flip_atr_mult=1.5. Dates suggest optimization curve-fit on overlapping period (4-6 months) with test data.
- **영향**: Parameters tuned to local backtest sample. Phrase 'out-of-sample is still negative' signals params did NOT work on hold-out data. Real 2026 live trade: (1) parameter decay likely as market regime shifts post-optimization window. (2) min_adx=30 is aggressively high — may suppress >80% of true signals, creating rare high-confidence entries that lack sufficient volume (MDD -22% still scary). (3) live usage with BAR-OPS-33 priority='lowest' tries to mitigate drag but doesn't cure negative expectancy.
- **수정안**: out-of-sample validation impossible post-hoc. Options: (1) reduce min_adx to 20-25 to capture more signals (accept more whipsaw if base PF>1.3). (2) disable supertrend in production ('enabled=False') and rely on daemon general strat. (3) run shadow (logging only) on live for 2-4 weeks to measure actual OOS PF; compare logs vs actual profit.

### 🟠 [HIGH] RG-005 (risk_integration) — Overnight position gap-stop protection only in supertrend; daemon has none for non-swing_38 carry-overs
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`safe_auto`
- **위치**: `backend/core/supertrend_auto_trader.py:232, scripts/intraday_buy_daemon.py:1809-1819`
- **근거**: Supertrend has carry_gap_stop_pct=-3.0% (line 232): if overnight position gaps down ≥3% from prior close, exit immediately regardless of SELL signal. Daemon has only EOD carry-limit (1809-1819): trims positions if overnight qty exceeds 20% of assets. Daemon lacks intraday gap-stop for overnight carries. If 6/9 SELL signal missed + position enters 6/10 with -3% gap, supertrend exits but daemon holds until explicit signal or EOD check.
- **영향**: Asymmetric protection: supertrend protected vs overnight gap, daemon exposed. Historical event (reports 6/10 entry): supertrend 459550 re-entry at 14:12 after 1st exit, carries to 6/10 gap down -12.63%, held via ATR trail (too loose at -6~13% range per comment line 155). carry_gap_stop_pct=-3.0 would have auto-exited at gap onset. Daemon portfolio holds carry-over positions from f_zone/gold_zone without this protective floor — if 6/10-style event repeats on daemon carry-overs, only slow EOD liquidation saves capital.
- **수정안**: Add intraday overnight gap-stop to daemon: at each sell-eval cycle (340L), check carry-over positions (entry_time < current UTC date), apply carry_gap_stop_pct=-3.0% gate (use _prev_close from minute bars). Mirror supertrend._carry_gap_stop_hit logic.

### 🟠 [HIGH] RG-006 (risk_integration) — Reentry cooldown policy asymmetric: daemon 30min hard cap vs supertrend max_entries_per_symbol_day (default OFF)
- **분류/판정**: edge_case / CONFIRMED · fix_safety=`safe_auto`
- **위치**: `scripts/intraday_buy_daemon.py:76, 978-991, backend/core/supertrend_auto_trader.py:159-162`
- **근거**: Daemon enforces BUY_REENTRY_COOLDOWN_MIN=30min via cooldown_buys set (978L) and audit_buys fallback (996L). Supertrend allows max_entries_per_symbol_day reentry count (160) but default=0 (OFF). Line 447 checks _reentry_blocked(symbol) which uses max_entries_per_symbol_day>0; if 0 (default), no block. Daemon never sold same-session symbol can't be re-bought for 30min; supertrend can buy same symbol in consecutive cycles if signal triggers.
- **영향**: Reentry pattern discrepancy: (1) daemon prevents same-session high-frequency chasing (P6 2026-05-20). (2) supertrend allows unlimited same-day re-entries by default, only gated if env-configured. Historical case (459550 6/8): 1st exit +58K, re-entry 14:12 -509K. If supertrend config had max_entries_per_symbol_day=1, 2nd entry blocked; current default allows it. Daemon audit_buys fallback is safety net but requires working order_audit.csv.
- **수정안**: Set supertrend max_entries_per_symbol_day=1 default (block re-entries same day). OR enforce via env SUPERTREND_AUTO_MAX_ENTRIES_PER_SYMBOL_DAY=1. Currently too permissive.

### 🟠 [HIGH] SWING38-006 (swing_38) — BARRO_SWING38_SL_PCT env overrides backtest assumption (-15% → -8% possible)
- **분류/판정**: param_risk / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/core/strategy/swing_38.py:300, backend/core/risk/holding_evaluator.py:141`
- **근거**: exit_plan() reads env('BARRO_SWING38_SL_PCT', '-15.0'). Comment L300 says '[6/23 backtest] env tunable; .env.local to -8% tightening(instant rollback OK)'. STRATEGY_EXIT_PROFILES['swing_38'] also reads same env. Backtest used -15%, but ops memo suggests -8% tight test possible. If ops sets -8%, running with SL that's 7% tighter than validated backtest.
- **영향**: SL%=param drift. Backtest: -15% hit 1.8% CAGR. If real ops runs -8%, false tightening increases SL triggers, worse Profit Factor (shakeouts at -8% noise level). On large positions, 7% SL tightness diff can flip win/loss on volatile days.
- **수정안**: Either keep -15% fixed (remove env override), or re-backtest / validate -8% before activation. If tuning in live, log entry and require explicit sign-off (not just .env.local silent change).

### 🟠 [HIGH] SWING38-009 (swing_38) — Backtest used GBM synthetic data, not KRX historical — real pattern mismatch risk
- **분류/판정**: lookahead_bias / PLAUSIBLE · fix_safety=`needs_restart`
- **위치**: `docs/01-plan/analysis/Backtest-Validation-Report.md:6-46`
- **근거**: Backtest-Validation-Report L6-46 states: 'Data: GBM 600-day synthetic (15 Monte Carlo) ... Limitation: synthetic data does not reproduce KRX microstructure (volume surges, gaps, theme continuity).' L48-49: 'Real-market validation required before live apply.' L152-154: 'Real-data retesting needed post-backtest.' swing_38 backtest never run on actual KRX data 2023-2025.
- **영향**: Swing_38 validated on smoothed synthetic returns only. Real KRX data shows: (a) volume clustering (2x avg can happen mid-day, hard to detect); (b) theme-driven reversions; (c) overnight gaps. Simulations assume linear retraces; real retraces are choppy, partial. Historical CAGR +1.8% may not repeat.
- **수정안**: Re-backtest on KRX daily OHLCV 2023-2025 before live expansion. Or keep current as Phase Beta with daily loss-cap (BARRO_SWING38_MAX_DAILY_LOSS=50k) and % cap (BARRO_SWING38_MAX_DAILY_PCT=2%).

### 🟠 [HIGH] ZNE001 (zones_fgs) — gold_zone RSI 파라미터 vs docstring 불일치 — 의도 불명확
- **분류/판정**: param_risk / CONFIRMED · fix_safety=`risky_hitl`
- **위치**: `backend/core/strategy/gold_zone.py:46-47,204`
- **근거**: 파라미터: rsi_oversold=35.0, rsi_recovery=38.0 설정. 그러나 docstring(line 3-7)에서 '30 이하 후 40 돌파 회복'이라 명시. 실제 로직(_rsi_score line 224-229)은 파라미터값 35→38 기준으로 작동하므로, 매우 좁은 복구 범위(3%)에서만 신호 발생. comment에서 '(oversold→neutral)'이라 했으나, 35는 일반적 기준(30)보다 높음.
- **영향**: RSI 35~38 범위가 과도히 까다로움. 일반적 oversold(RSI≤30) 패턴을 놓칠 가능성 높음. 백테스트가 이 파라미터로 수행되면, out-of-sample 검증 불충분. 향후 파라미터 변경 시 코드-주석 불일치로 버그 재발 위험.
- **수정안**: 주석 또는 파라미터 통일. 선택지: (1) 주석 수정 '35 이하 후 38 돌파 회복'으로 명확화 또는 (2) 파라미터를 docstring 기준(30/40)으로 변경 후 re-backtest. 의도 문서화 필수.

### 🟠 [HIGH] ZNE003 (zones_fgs) — gold_zone min_score=5.0 과최적화 — 6월 데이터만 out-sample 검증
- **분류/판정**: param_risk / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/core/strategy/gold_zone.py:57-60`
- **근거**: Phase D2.3에서 4.0→5.0 변경. 근거: '4~6월 sweep(거래대금150, in 4~5월/out 6월): score≥5.0 기대값+4.32 승률57%'. Out-of-sample이 6월 단 1개월뿐. Comment '6월 약세 해소·과최적 아님'이라 했으나, 7월 이상 데이터 검증 부재. 또한 '거래대금150'이 무엇인지 불명확(아마도 일 거래대금 150억 필터 추정).
- **영향**: mock-live 운영 중 7월부터 성과 악화 가능성. 5.0 임계값이 6월 약세 특성에 overfitting된 우려. real 전환 시 손실 누적 가능. 또한 min_conditions=2(3조건 중 2개만 필요)와 함께 하면 실제 신호 품질 낮을 수 있음.
- **수정안**: 7월 이상 최신 데이터로 re-backtest. min_score 4.0/4.5/5.0/5.5 grid sweep 필수. 거래대금 필터 의도 명시 및 코드 문서화. Out-sample 기간 최소 3개월 이상 확보 후 파라미터 확정.

### 🟠 [HIGH] ZNE004 (zones_fgs) — ATR% 필터 불일치 — gold_zone에만 orchestrator override 미적용
- **분류/판정**: risk_gate_gap / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/core/orchestrator.py ~line 420 + backend/core/strategy/gold_zone.py:66`
- **근거**: orchestrator._rescan_loop에서 f_zone은 FZoneParams(min_atr_pct=0.035) override되나, gold_zone_params override 전혀 없음. gold_zone은 GoldZoneParams() default(min_atr_pct=0.0) 유지. Comment '2026-05-29: gold_zone 1m+0.035 일관화(제안1) 원복 — 격자 백테스트상 근거 없음(1m+0.035=신호 전멸)'. 즉, ATR% 필터가 gold_zone 신호를 완전히 죽인다고 판단 → 필터 해제 결정. 그러나 문서화 부족 → 저변동 고가주(LG전자 등) 가짜 신호 방지 미흡.
- **영향**: 저변동·고가주 진입 가능. Comment 예시: '5/21 LG전자 -626k (43 trades, win 41%)', '5/14 LG씨엔에스 -190k' 등 손실 사례 있음. 현재 비활성(enabled_strategies=False)이지만 복구 시 문제 재발 가능. ATR 필터가 필요한지/불필요한지 명확한 근거 필요.
- **수정안**: gold_zone min_atr_pct=0.0 의도를 명확히 문서화 또는, 저변동 필터를 다른 방식(rsi_period 강화, bb_std 조정 등)으로 대체. 또는 min_atr_pct=0.02 정도로 완화 후 re-backtest. LG계 손실 케이스 분석 필수.

### 🟠 [HIGH] ZNE005 (zones_fgs) — Fib 레벨 범위 vs docstring 불일치 — 0.382~0.618 vs 0.236~0.786
- **분류/판정**: logic_bug / CONFIRMED · fix_safety=`needs_restart`
- **위치**: `backend/core/strategy/gold_zone.py:43-44,6`
- **근거**: docstring: 'Fib 0.382~0.618 zone 안'. 파라미터: fib_min=0.236, fib_max=0.786. 실제 로직은 0.236~0.786 범위에서만 신호 발생(line 196). 더 넓은 범위가 설정되어 있으나, docstring과 불일치. 의도 불명확.
- **영향**: docstring에 의존한 유지보수자가 잘못된 범위로 파라미터 조정할 수 있음. 또한 0.236~0.786은 거의 전체 피보나치 범위이므로, 실제 '골드존' 개념(강한 되돌림)이 아닌 일반적 되돌림(약한 되돌림도 포함)이 됨.
- **수정안**: docstring 수정 '0.236~0.786 zone' 또는 파라미터를 '0.382~0.618'로 변경 후 re-backtest. 의도 명확화 필수. 만약 0.236~0.786이 의도라면 스코어링에서 0.5 중심 근처를 더 높이 평가(line 199 로직 이미 적용 — centeredness 고려).

### 🟠 [HIGH] ZNE007 (zones_fgs) — Trap Guard(6월 트랩 방어) 모든 파라미터 0 — 가짜 상승 방어 미활성
- **분류/판정**: risk_gate_gap / CONFIRMED · fix_safety=`risky_hitl`
- **위치**: `backend/core/strategy/f_zone.py:113-118, gold_zone.py:77-82, sf_zone.py (상속)`
- **근거**: 모든 zone에서 trap_*=0.0 (default-OFF). Comment '모든 임계 0 → 기존 진입 경로 byte-identical'. 6월 가짜 상승(개미 꼬시기) 패턴 방어 설계(backend/core/strategy/trap_guard.py)가 있으나, 운영에서 활성화 안 됨. orchestrator.py나 signal_scanner.py에서도 TrapGuardConfig override 없음.
- **영향**: 개미 꼬시기 패턴(높은 wick, 큰 gap up 직후 하락) 진입 가능. 특히 6월 한 달간 이런 패턴이 많았다면, 향후 비슷한 시기에 손실 발생 가능. 데이터 검증: trap_guard.py 백테스트 근거 있는지 확인 필요.
- **수정안**: trap_* 파라미터를 orchestrator에서 override하거나, 또는 명시적으로 off 유지하되 주석에 '6월 트랩 빈도 분석 미완료' 등 이유 기록. 또는 trap_guard.py 백테스트 검증 후 기본값 활성화.

## 양호 확인 (positive)

- **limit_up_chase/P-POSITIVE-1**: Strategy isolation (limit_up_chase vs supertrend)—correct partition by strategy_id
- **limit_up_chase/P-POSITIVE-2**: Hard stop (-4.0%) gate active—catastrophic loss capped
- **risk_integration/RG-011**: Daily loss latch persistence implemented correctly with atomic write and UTC date rollover

## 기각 (REJECTED — 오탐)

- **limit_up_chase/P-GATE-1**: Daily loss limit enforcement unconfirmed—daily_pnl_pct passed but LiveOrderGate logic opaque — The finding claims "no visible enforcement code" and that "policy.json daily_loss_limit not explicitly linked", but verification confirms: (1) enforce
- **zones_fgs/ZNE006**: 금지된 현시점에서 중복 zone 진입 위험 — 포지션 충돌 관리 부재 — Code inspection reveals SignalScanner._analyze_symbol() returns immediately upon the first detected signal, preventing multiple zone signals from bein
- **round_figure_stop/PS-001**: Position sizing minimum of 1 share could force over-allocation on extreme high-priced stocks — The finding misrepresents both (1) Decimal.quantize() behavior - tested and confirmed it rounds 0.16 to 0, not 1 - and (2) production code usage - pos
- **risk_integration/RG-001**: Daily order limit fragmented across execution paths — supertrend and daemon each have independent gate — The finding correctly identifies that two separate LiveOrderGate instances exist with different daily_max_orders values configured from separate sourc
- **risk_integration/RG-008**: ETF/leverage filter code duplicated in daemon and supertrend; _is_etf_or_etn impl diverges slightly — The finding claims implementation divergence between daemon (40 lines, comprehensive) and supertrend (~9 lines, minimal). Verification shows both impl

---
*멀티에이전트 전략 리뷰 워크플로우(run `wf_734b31ea-f11`)의 적대적 검증 통과 결과. mock 환경 현실 영향 보정. 실거래 주문 미호출. supertrend 단독 재분석 결과는 별도 추가 예정.*

## Supertrend 단독 재분석 (활성 핵심전략, 보완)

> 초기 배치 누락분 보완. 4개 파일(supertrend.py, supertrend_auto_trader.py, intraday_buy_daemon.py, run_telegram_bot.py) + 게이트·정책·env 교차 확인. 읽기 전용, 수정안은 미적용 제안.

### 🟠 [HIGH] MAX_ORDERS=0 = 매수 무한 → ✅ **[2026-07-02 해결·라이브 반영 완료]**
- **경로**: `.env.local:122` → 봇 `run_telegram_bot.py:597`(daily_max_orders=0) → `live_order_gate.py:198` 체크 skip.
- **영향**: 휩쏘장에서 진입/청산 폭주 → Kiwoom 429 플러드(6/15 인시던트) 재현 위험이었음. 현 완화책은 MAX_ENTRIES=1+cooldown30+maxpos10뿐이라 종목 로테이션 시 총주문 무제한.
- **수정**: `SUPERTREND_AUTO_MAX_ORDERS=100` 캡 복원 → **적용 완료(봇 PID 14775)**. 상세·6/26 되돌림 유의사항은 위 「일일주문 캡 복원」 절.

### 🟠 [HIGH] 트레일 청산 미작동 → ✅ **[2026-07-01 해결·라이브 반영 완료]**
- **경로**: `run_telegram_bot.py:640` env default "0" → trail_atr_mult=0 (dataclass default 3.0을 env가 덮어씀).
- **영향**: 6/8 백테스트에서 -3.27%→+2.73% 흑자전환의 핵심이던 트레일 청산 개선이 **라이브에 미적용**이었음. 현재 hard_stop -6%·TP +5%만 방어.
- **수정**: `SUPERTREND_AUTO_TRAIL_ATR=3` 설정 → **적용 완료(봇 PID 14015 트레일 ON 확인)**. 상세는 위 「트레일 청산 복원」 절.

### 🟠 [HIGH] entry_lookback=100 과확장 (risky_hitl)
- **경로**: `.env.local:57` → `supertrend_auto_trader.py:470`.
- **영향**: 설계상 flip 이벤트(N=2) 진입이 "상승추세면 늦게라도 진입"으로 변질. FLIP 강도게이트(`:671`)가 최대 100봉 전 flip 기준으로 측정돼 사실상 무력화 → 만료된 추세의 고점 진입.
- **수정**: 5~10으로 축소 + out-of-sample 재검증. (진입빈도 급변 → HITL)

### 🟡 [MEDIUM] 게이트 분절/교차오염 (risky_hitl)
- **경로**: 봇·데몬이 동일 `order_audit.csv` 공유, `_count_today_buys`(`:355`)는 전역 집계.
- **영향**: supertrend(cap0)는 무한이나 그 매수가 데몬 budget(policy.json=300) 카운트를 잠식 → 일반전략 조기차단 유발. 반대로 supertrend는 어떤 캡에도 안 걸림(비대칭).
- **수정**: 전략별 count 분리 또는 통합 글로벌 캡.

### 🔵 [LOW] 마지막 5분봉 repaint (needs_restart)
- 폴링이 봉경계와 미동기 → forming bar로 trend/buy_signal이 intrabar 흔들림. 단 `compute_supertrend`/`compute_adx`(`supertrend.py:174-205`) 자체는 완전 인과적(룩어헤드 없음). 완화: 확정봉 기준 평가.

### ⚪ 정상 확인 (positive)
지표는 Pine 충실·인과적(룩어헤드 없음). `min_price=1000` + `_cap_qty`(5000주/5M원)로 동전주 수량폭주(6/2 252670 사고) 차단. exclude_etf/leverage·MAX_ATR 0.05·MAX_OPEN_GAP 15·MAX_ENTRY_GAP 3·재진입가드·daily_loss -3%(sticky latch, `daily_gate_input`=실현net+평가/예탁 보수적) 모두 배선·활성 확인. 데몬은 SUPERTREND_AUTO_ENABLED=1이면 봇에 양보(`_supertrend_yield_to_bot`) → 이중주문 없음.

### Supertrend 종합
현재 mock 안전. 지표·동전주가드·손실래치 등 방어의 기본 골격은 견고하나, **라이브에서 트레일 청산이 꺼져있고(HIGH) 일일주문한도가 해제(HIGH)돼 있어 6/15式 주문폭주 재발 소지**가 있다. entry_lookback=100은 진입 타이밍을 늦춰 고점진입 위험을 키운다. 세 HIGH 모두 env/param 조정(재시작·HITL)이며 코드 로직 자체 버그는 아님.
