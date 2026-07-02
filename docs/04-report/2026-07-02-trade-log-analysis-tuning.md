# BarroAiTrade 실측 매매로그 분석 · 전략 디테일 튜닝

- **작성일**: 2026-07-02
- **데이터**: `fill_audit.csv` 487 실현 라운드트립 + `order_audit.csv` 815 체결 타이밍 (2026-05-12~07-02)
- **방식**: 결정적 Python 집계(매수/매도 타점) → 멀티에이전트 전략별 튜닝 도출(7전략) → 적대적 검증(39건 중 CONFIRMED 15·PLAUSIBLE 12·REJECTED 12)
- **주의**: mock 시세 기반 — PnL 절대값보다 전략 간 상대거동·타이밍 패턴이 유효 신호

## 1. 실측 성과 (전략별 실현 라운드트립)

| 전략 | 거래 | 승률 | 평균손익 | 평균이익 | 평균손실 | PF | 누적손익 | 개장러시% |
|------|------|------|---------|---------|---------|-----|---------|----------|
| supertrend | 233 | 31.8% | -0.28% | +4.25% | -2.39% | 0.95 | -180,510 | 3% |
| gold_zone | 73 | 54.8% | -0.08% | +4.01% | -5.02% | 0.56 | -506,526 | 22% |
| f_zone | 47 | 57.4% | -0.11% | +3.66% | -5.2% | 0.63 | -272,687 | 19% |
| closing_bet | 43 | 58.1% | -2.86% | +4.12% | -12.55% | 0.75 | -149,858 | 0% |
| limit_up_chase | 41 | 26.8% | -2.5% | +0.95% | -3.77% | 0.01 | -665,108 | 14% |
| sf_zone | 16 | 43.8% | -1.9% | +1.45% | -4.51% | 0.13 | -305,643 | 44% |
| swing_38 | 13 | 23.1% | -3.94% | +20.78% | -11.36% | 0.48 | -474,327 | 80% |
| ? | 21 | 0.0% | -2.55% | +0% | -2.55% | 0.0 | -1,491,463 | 21% |
| **전체** | 487 | 38.4% | -0.9% | +4.06% | -3.99% | 0.57 | -4,046,122 | - |

### 매수 진입 타이밍 분포
- 09:30-11:00: 204 (45%)
- 09:10-09:30: 82 (18%)
- 09:00-09:10(개장러시): 78 (17%)
- 11:00-13:00: 44 (10%)
- 13:00-15:00: 29 (6%)
- 기타: 13 (3%)

### 최악 손실 라운드트립
- 20260626 010120(LS ELECTRIC) `swing_38` **-16.9%** (261500→219500)
- 20260626 010120(LS ELECTRIC) `swing_38` **-16.9%** (261500→219500)
- 20260626 006340(대원전선) `swing_38` **-16.4%** (11730→9900)
- 20260624 067290(JW신약) `closing_bet` **-14.7%** (3142→2705)
- 20260624 067290(JW신약) `closing_bet` **-14.7%** (3142→2707)
- 20260624 067290(JW신약) `closing_bet` **-14.7%** (3142→2707)

## 2. 전략별 진단 (실측 근거)

**supertrend**: Supertrend PF 0.95는 약간의 음수 기대값(-0.28%)을 보이나, 이는 이미 매우 보수적인 필터(min_adx=30, min_flip=1.5, max_atr_pct=0.05)로 거래수 감소(125→30거래/月)의 대가. 평균이익 4.25% vs 평균손실 2.39%의 1.78:1 비율과 트레일 청산 3.0의 검증된 효과(6/8 백테스트 +2.73%)를 고려하면 현재 설정이 이미 수렴점에 근접. 개선 여지는 (1) entry_lookback 100(과도)→20(중간값), (2) take_profit_pct 코드 기본값이 env 미지원으로 튜닝 기회 있음.

**gold_zone**: Gold_zone shows paradoxical performance: 54.8% win rate but -0.08% average return, -506k cumulative P&L. Root cause is asymmetric risk/reward (avg_loss=-5.02% vs avg_win=+4.01%), creating 0.56 profit factor that absorbs gains. Three specific issues detected: (1) SL fixed at -1.5% may not trigger before cascading losses (avg_loss 3.3× SL); (2) rush_pct=22% indicates 22% of entries occur 09:05-09:10 despite BARRO_OPEN_HOLD_HHMM=0915 suggesting gate not enforced in gold_zone code path; (3) trap_guard (ZNE007) all-zero suggests no defense against overshoots/false signals during June weakness. Fix priority: SL tightening (immediate, safe), entry window enforcement (code-gated), trap_guard shadow test (DRY_RUN mode).

**f_zone**: F존은 기본 체크아웃(기준봉/눌림목/반등)는 견고하나, 현재 파라미터 조합이 약한 신호를 과도하게 허용합니다. PF=0.63(< 1.0)은 SL -2.0%이 너무 넓어 개별 손실이 평균 -5.2%까지 깊어지는 반면, TP +3%/+5% 구조는 이익은 +3.66% 평균으로 제한되는 불균형입니다. 또한 BARRO_OPEN_HOLD_HHMM=0915로도 개장러시(rush_pct=19%)가 억제되지 않으며, min_atr_pct=0.0으로 저변동성 종목(고가주/LG류)의 SL 노이즈 손실이 누적됩니다.

**closing_bet**: **avg_loss(-12.55%) ÷ avg_loss_설정(-5%) = 2.5배**: 자동매도 미구현으로 SL 신호가 발생해도 실제 청산되지 않음. 43 거래 중 손실 거래(승률 반대)의 최악이 -14.74%까지 도달한 것이 증거. 높은 승률(58.1%)에도 평균 손익 음수(-2.86%)인 이유는 손실 폭주(avg_loss/avg_win 비율 3:1)에 있음.

**limit_up_chase**: 상한가 추격(상따) 전략은 호가벽 조건(wall_min_top_value=100M원)이 진입 신호 70% 이상을 차단하고, 일일 매수 한도 6건으로 추가 60% 차단되어 실제 거래 기회가 극히 제한됩니다. 결과적으로 상한가 락 → 당일 강제청산(daily mode) 구조에서 상방차단/하방개방 위험에 노출되어 평균손실이 평균이익의 4배가 되었습니다(avg_loss -3.77% vs avg_win +0.95%, PF 0.01).

**sf_zone**: SF Zone이 PF=0.13(매우 약함), 평균손익=-1.9%로 거짓 신호 과다 발생 중. 개장러시(09:00-09:10) 진입 44%에서 보듯이, 개장 혼란 시간대의 휩쏘(sweep)에 의한 false entry가 주요 손실 원인. SL=-1.5%는 평균손실=-4.51%에 비해 약해서 손절 미발동이 손실 확대(손실/이익 3.1배).

**swing_38**: swing_38 라이브 성과(n=13, 승률23%, 평균손익률-3.94%, PF=0.48)가 OOS 시뮬(승률42-44%, +2.36~+2.78%)에서 극적으로 악화. 근본 원인은 (1) 개장러시 80% 집중 진입으로 인한 신호품질 악화, (2) -8.0%의 과도히 타이트한 손절로 인한 슬리피지 손실 누적, (3) 약한 진입 필터(min_score=3.0)로 인한 거짓신호 통과, (4) 사이즈 과다로 인한 손실 쏠림. 데이터가 이미 6/23 개장 게이트 추가 후를 포함하므로 게이트가 미작동이거나 선택적 적용 중인 것 추정. 손절 역설(-8% > 평균손실-11.36%)은 진입 후 초기 급락 + 슬리피지 구조를 시사.

## 3. 이번에 적용한 조치

| 조치 | 변경 | 근거 | 되돌리기 |
|------|------|------|----------|
| **limit_up_chase 중단** | `.env.local` `LIMIT_UP_CHASE_ENABLED` 1→0 (봇 재시작) | 실측 41거래 PF **0.01**, 승률 26.8%, 평균이익 +0.95% vs 평균손실 -3.77%(4배), 누적 -665K — 수학적 회복 불가. 상한가 락 구조로 상방차단/하방개방 | `=1` 후 봇 재시작 |

> 백업 `.env.local.bak.20260702_103122`. mock 환경·즉시 되돌림 가능. 6/22 '전 전략 관찰' 상태에서 관찰 결과 실패가 확정된 1종을 내린 것.

## 4. 검증 통과 튜닝 권고 (미적용 — 확인/작업 필요)

### 4-1. 라이브 활성 전략 (사용자 확인 후 적용)
- **closing_bet [CONFIRMED·safe_auto]** `BARRO_CB_DISPARITY_YELLOW` 0 (OFF, byte-identical default) → 1
  - 근거: 5일선 이격 +14.25% 게이트 활성화 → closing_bet.py line 186-189의 require_disparity_yellow 게이트 켬. shadow 측정(line 89-93 주석): net@0.90 baseline +0.107% → disparity ON +0.405%
- **closing_bet [CONFIRMED·safe_auto]** `BARRO_CB_MIN_CASH_PCT (현금버퍼 게이트, 현 정책 유지)` 0.30 (30%, 2026-06-24 설정) → 0.30
  - 근거: 주문가능액 < 총자산×30% 면 종베 신규 SKIP. 이미 과도 영포지 차단 효과 있음(closing_bet_alert_daemon.py line 146-152 _CB_MIN_CASH_PCT 게이트). 43거래가 rush_pct=0인 이유도 개장러시 보류(line 201 BARRO_OP
- **supertrend [CONFIRMED·safe_auto]** `entry_lookback (SupertrendParams.entry_lookback)` 100 봉 (약 500분, ~8시간 이전 신호도 포함) → 20
  - 근거: 현재 100은 코드 기본값 2의 50배로 과도함. 진입 신호가 발생한 시점으로부터 너무 오래된 신호(100개 봉 이전)를 포함하면 false signal 증가, 신호 신뢰도 저하. 실측 평균손익률 -0.28% 중 일부가 지각된 진입 시점과 관련. 백테스트 기본값 2는 폴링 타이밍 변동 
- **closing_bet [CONFIRMED·risky_hitl]** `closing_bet_auto_sell() 배선 (코드 구현)` 미구현 — sell_signals() 신호 생성만, gate.place_sell() 호출 X → async def _cb_auto_sell(sym, name, qty, sell_reason, oauth, dry_run, dry_print) 
  - 근거: avg_loss -12.55% vs -5% SL 설정의 근본 원인. 현재 코드: sell_signals(pos, cur, now)는 (key, reason) 튜플 list 반환 → scan_sell() line 245-250에서 텔레그램 알림만 발송 후 alerted[] 기록. 실제 시
- **closing_bet [PLAUSIBLE·needs_restart]** `BARRO_CB_MORNING_EXIT_STRICT` 미설정 (구현 없음) = 익일 10:00 자동청산 신호만 생성, 매도 미집행 → 제안의 fix_safety를 "safe_auto"에서 "needs_restart"로 수정 필요. Rollout 단계 1("데몬 sell_sign
  - 근거: closing_bet.py line 100의 morning_exit_time=dtime(10,0)은 오버나잇 설계의 핵심이지만, 현재 exit_plan만 반환하고 실제 청산은 데몬의 매도 신호 처리(line 200-251)에만 의존. sell_signals()에서 MORNING 신호 발
- **supertrend [PLAUSIBLE·needs_restart]** `take_profit_pct (SupertrendAutoConfig.take_profit_pct)` 5.0% (고정 익절, 진입가 대비 +5% 도달 시 전량청산) → Recommend KEEPING 5.0% (backtest-validated, 2026-06-08 baseline) OR INCREASING t
  - 근거: 실측 평균이익 4.25% vs 현재 TP 5.0%. 고정 익절이 일부 거래의 이익 실현 기회를 놓침. 예: 진입 후 +4.5%에 도달 후 트레일 청산된 거래들을 TP 4.0%로 낮추면 조기 익절 → PF 개선. 트레일 3.0(ATR 기반)과의 이중화는 OR 조건이므로 더 적극적 수익 보

### 4-2. limit_up_chase 추가 옵션 (중단과 배타적 — 재활성화 시에만)
- [CONFIRMED] `entry_end_time` 14:00 → 14:30 — rush_pct=14%는 개장러시(09:00~09:10) 진입이 14%만 의미. 상따는 개장 직후 급등 추격이지만 현 14:00 컷오프는 상한가 기회를 너무 일찍 잠금. 14:00
- [CONFIRMED] `LIMIT_UP_MAX_ORDERS` 6 (기본값, 미설정) → 12 — order_audit 분석: limit_up_chase 35개 오더 중 21개(60%)가 '일일 매수 한도 초과' 차단(6 ≥ 6). 현재 한도가 진입 신호 발생량에 대비 심각한 
- [CONFIRMED] `overnight_mode` daily → overnight — 상한가 락 시 당일 강제청산(eod_close_time=15:15) 불가 → 상한가 깨진 후 손실청산 강제. overnight 모드로 전환 시 익일 갭 부분익절(runner_gap
- [PLAUSIBLE] `LIMIT_UP_CHASE_ENABLED` 1 → 0 — 실측 통계(n=41): PF=0.01(전패), avg=-2.5%, win%=26.8%, avg_loss=-3.77% >> avg_win=+0.95%, sum=-665,108원. 평
- [PLAUSIBLE] `wall_min_top_value` 100000000.0 (100M원) → Parameter: wall_min_top_value | Location: backend/core/limit_up_chase_ — order_audit 분석: 매수벽 필터로 진입 신호의 70% 이상 탈락. order_audit 로그 '상따 호가벽 탈락(잔량금액 부족)' 반복 발생. 상한가 추격은 고유동성 종목

### 4-3. zone 전략군 (현재 orchestrator에서 비활성 — 재활성화 시 적용)
- **f_zone** [CONFIRMED·safe_auto] `position_sizing (포지션 사이징)`: even_position_size (균등 8%, max_total 80%) → 현상 유지 (변경 없음)
- **gold_zone** [CONFIRMED·safe_auto] `trap_over_ext_k_atr, trap_upper_wick_max, trap_gap_atr_mult (GoldZoneParams)`: All trap_* = 0.0 (fully disabled); env BARRO_TRAP_* all unset or 0 → BARRO_TRAP_OVER_EXT_K_ATR=1.0 / BARRO_TRAP_UPPER_WICK_MAX=0.5 / BARRO_
- **f_zone** [CONFIRMED·needs_restart] `BARRO_OPEN_HOLD_HHMM (개장러시 진입 차단 시간)`: 0915 → 0930
- **f_zone** [CONFIRMED·risky_hitl] `trap_guard 활성화 (6월 트랩 방어)`: trap_over_ext_k_atr=0.0, trap_upper_wick_max=0.0, 등 모두 비활성 → trap_over_ext_k_atr=2.0, trap_over_ext_baseline="ma", trap_upper_wick_
- **sf_zone** [CONFIRMED·needs_restart] `sf_volume_ratio (FZoneParams)`: 3.0 (300% 거래량 배율) → 4.0 (preferred) or 3.5 (conservative) — no change needed
- **f_zone** [PLAUSIBLE·needs_restart] `bounce_min_gain_pct (반등 최소 상승률)`: 0.005 (0.5%) → 제안값 0.010은 안전하고 합리적이나, 구현 방식 수정 필요: (1) fix_safety = needs_restart로 정정
- **gold_zone** [PLAUSIBLE·needs_restart] `GoldZoneParams.entry_time_cutoff (init) or BARRO_OPEN_HOLD_HHMM enforcement`: BARRO_OPEN_HOLD_HHMM=0915 set in .env.local, but not explicitly enforced in gold_zone.analyze() → 1) QUICK FIX: Change BARRO_OPEN_HOLD_HHMM=0915 → =0930 in .env.local. 
- **sf_zone** [PLAUSIBLE·needs_restart] `sf_impulse_min_gain_pct (FZoneParams)`: 0.05 (5%) → **CORRECTED ROLLOUT:**
(1) Add to .env.local:
    BARRO_SF_IMPULSE_MIN
- **sf_zone** [PLAUSIBLE·risky_hitl] `stop_loss fixed_pct (SFZoneStrategy.exit_plan)`: -0.015 (-1.5%) → -0.012 (-1.2%) via direct sf_zone.py line 80 modification: Decimal("-0
- **sf_zone** [PLAUSIBLE·needs_restart] `min_atr_pct (FZoneParams)`: 0.0 (비활성) → 
Current state remains: min_atr_pct=0.035 override STORED in orchestra
- **sf_zone** [PLAUSIBLE·needs_restart] `enabled_strategies (orchestrator.py) / .env.local BARRO_SF_ZONE_ENABLED`: enabled_strategies={"sf_zone": False, "f_zone": False, "gold_zone": False} (비활성 override) → Remove or comment out line 341 in orchestrator.py: "enabled_strategies

### 4-4. swing_38
- [CONFIRMED·needs_restart] `min_score (Swing38Params)`: 3.0 (swing_38.py:65 code default) / 시뮬은 5.0 override → SWING38_MIN_SCORE env var implementation approach: (1) Add __post_init
- [CONFIRMED·needs_restart] `min_atr_pct (Swing38Params)`: 0.03 (3%, Phase D2 활성화 기준) → 0.035
- [CONFIRMED·needs_restart] `second_entry_enabled / second_entry_min_days / second_entry_max_days`: enabled=True, min_days=1(당일 추가 진입 차단, D+1부터), max_days=5, support_tolerance=0.5%, size_ratio=0.5 → Proposal values are technically sound. No correction needed:
- Option 
- [PLAUSIBLE·needs_restart] `BARRO_SWING38_SL_PCT`: -8.0% (2026-06-23 타이트닝 설정) → -12.0 (value acceptable, but requires restart not safe_auto)
- [PLAUSIBLE·risky_hitl] `entry_time_cutoff (Swing38Params)`: None (비활성, 모든 시간 진입 허용) / 시뮬은 14:00 override → To make this properly safe_auto, two options:

OPTION 1 (Safe via env 

## 5. 핵심 결론

1. **limit_up_chase**: 중단(적용). 데이터상 회복 불가.
2. **closing_bet**: 자동매도 미구현이 평균손실 -12.55%의 근본 — auto-sell 배선(코드) 필요. 이격도 게이트(`BARRO_CB_DISPARITY_YELLOW=1`)로 진입품질 개선 가능(관찰 롤아웃).
3. **zone군(gold/f/sf)**: 승률은 높으나 SL이 헐거워 손실이 이익 잠식 — 현재 비활성이므로 재활성화 전 SL 타이트닝·트랩가드·개장러시 억제 선반영 필요.
4. **supertrend**: 활성전략 중 최선(PF 0.95, 트레일 복원 효과). entry_lookback 100→20은 관찰 롤아웃 후 판단.
5. **개장러시 진입**(sf_zone 44%·swing_38 80%)이 손실 집중 시간대 — entry window 게이트 강화가 공통 개선축.

---
*멀티에이전트 튜닝 워크플로우(run `wf_63a3f2df-b54`) 적대적 검증 결과. REJECTED 12건은 파라미터명 불일치·이미구현·오해 등으로 정확히 배제됨. 실거래 주문 미호출.*

## 6. [2026-07-02 실행] 중단 전략 재활성화 + 튜닝 이슈 해결 + closing_bet auto-sell (라이브 반영 완료)

사용자 지시: "매매중단된 전략 전체 재활성화(튜닝 이슈 해결 후) + closing_bet auto-sell·이격필터 적용".
테스트 85 passed, main 배포(8decc3b), 3개 서비스 재시작 검증 완료.

### 적용 항목

| # | 변경 | 위치 | 라이브 반영 |
|---|------|------|-------------|
| 1 | **closing_bet 자동매도 배선** | `scripts/closing_bet_alert_daemon.py` `_cb_auto_sell()` + `BARRO_CB_AUTOEXEC_SELL=1` | 종베데몬 PID 37431 재시작 — SL/TP/MORNING/D3 시 place_sell 실청산+전량 포지션제거 (평균손실-12.55% 폭주 근본대응) |
| 2 | **종베 이격필터** | `.env.local` `BARRO_CB_DISPARITY_YELLOW=1` | 5일선 이격게이트 ON — 진입품질↑(shadow +298bps·승률58→62%) |
| 3 | **zone 백엔드스캔 재활성화** | `backend/core/orchestrator.py:341` override 주석해제 | 백엔드 PID 37439 재시작 — zone 알림스캔 복원(on_daily_scan_result 알림전용·주문경로 아님) |
| 4 | **limit_up_chase 재활성화 + overnight** | `.env.local` `LIMIT_UP_CHASE_ENABLED=1`·`LIMIT_UP_OVERNIGHT_MODE=overnight` | 봇 PID 37434 재시작 — 상따 ON(overnight). 상한가락→익일 갭 부분익절(carry_gap_stop-3% 보호)로 강제 당일손절청산 회피 |
| 5 | **zone 개장러시 차단 강화** | `.env.local` `BARRO_OPEN_HOLD_HHMM` 0915→0930 | ★내일 08:58 cron 적용★(intraday_buy_daemon은 cron관리 — 중간종료 시 미복구라 미재시작) |
| 6 | **trap_guard SHADOW 관찰** | `.env.local` `BARRO_TRAP_*`+`BARRO_TRAP_SHADOW=1` | ★내일 cron 적용★ — 6월 트랩손실 방어 로그측정(매매 무영향) |

> `.env.local` 백업 `.env.local.bak.20260702_105416`. 전 항목 되돌리기 쉬움(env 원복/재주석 + 재시작).

### 안전장치·유의
- **auto-sell byte-identical 가드**: `BARRO_CB_AUTOEXEC_SELL=0`이면 기존 알림전용 100% 동일. place_sell 시그니처 코드확인(live_order_gate.py:217), from_cache 테스트모드 미집행.
- **재활성화 시 검증**: on_daily_scan_result 주문경로 아님(이중매수 없음), place_sell은 손실한도·거래수 게이트 미적용(청산 우선).
- **★limit_up_chase 잔여 고위험★**: PF 0.01은 구조적 문제로 overnight/재활성만으로 흑자보장 불가. entry·wall 확대 튜닝은 손실 증폭 우려로 의도적 미적용. 반드시 밀착 관찰, 재악화 시 재중단 권고.
- **미적용(추가 검증 필요)**: sf_zone SL 타이트닝(-1.5→-1.2)은 '손실>SL'의 근본이 SL 미발화인지 불명확해 blind 변경 보류. supertrend entry_lookback 100→20도 관찰 롤아웃 대기.
- **daemon env(0930·trap)**: 장중 데몬 종료 시 내일까지 미복구 위험이라 미재시작 — 내일 개장 cron에 자동 적용.

## 7. [2026-07-02 최종판단] supertrend entry_lookback — 적용하지 않음 (의도적)

권고: entry_lookback 100→20 (코드 default 2의 50배 과확장, 만료추세 고점진입 우려). **결론: 미적용 유지.**

- **OOS 검증 불가**: supertrend는 5분봉 기반인데 `data/ohlcv_cache_5m`에 2종목만 캐시 → 대표성 백테스트 불가. 소표본 검증은 오도. (5m 대량수집은 429 리스크 별도작업)
- **"고장나지 않은 걸 고치지 마라"**: supertrend는 활성전략 중 유일 최선(PF 0.95·평균손실 -2.39% 타이트, 트레일3 복원 효과). entry_lookback=100이 이 전략을 명백히 훼손한 증거 없음. 검증 없이 잘 도는 전략의 진입로직 변경은 유일 작동전략 훼손 위험.
- 권고는 risky_hitl·"OOS 선행 필요"로 분류된 **가설**(검증된 개선 아님).
- **적용 조건**: (1) 5m 히스토리를 대표 유니버스로 수집 → entry_lookback {2,5,10,20,100} sweep 백테스트, (2) 우위 확인 시에만 DRY_RUN 관찰 후 반영. 그 전까지 100 유지.

## 8. [2026-07-02] daytrading-quant 전문가 검토 + 보정 조치

퀀트 전문가(daytrading-quant) 서브에이전트가 §6 수정 전체를 읽기전용 검토. 실제 결함 3건 확인 → 보정.

### 검토가 짚은 결함 & 보정
| 지적 | 판정 | 조치 |
|------|------|------|
| **overnight "carry_gap_stop -3% 보호"가 미작동** — `limit_up_chase_trader.py:180-238` `_run_exit_cycle` override가 부모의 `_carry_gap_stop_hit` 미호출. 오버나잇 갭 무보호(hard_stop-4%만, 갭다운 시가체결 -10~20% 가능) | 🔴 제 판단오류 확인 | **overnight→daily 되돌림**(.env.local) + 오칭 주석 정정. 봇 재시작 |
| **auto-sell 제출-즉시제거 → mock 미접수 시 orphan** | 🔴 타당 | `_cb_auto_sell` 접수확인(order_no+rc 성공)된 실주문만 포지션제거, 거부시 유지·재시도(main ab85e7a). ※접수≠체결 완전대응은 후속 |
| **포트폴리오 서킷브레이커 부재(-100 OFF)** | ⚠️ 부분오판정 | ★실제 -100은 주석(6/10 이력)이고 활성값은 `SUPERTREND_AUTO_DAILY_LOSS_LIMIT=-3.0`+`policy.json -3.0`★ — 차단기 켜져있음. |

### daily_loss_limit 과대계상 버그 확인 (사용자 조건부 요청)
- 봇 경로 `daily_gate_input.py:152`: `(realized+eval_pnl)/estimated_deposit` = **계좌대비(수정 완료)**.
- 데몬 경로 `intraday_buy_daemon.py:808`: 여전히 invested-relative(`total_pnl_rate`) = **과대계상 잔존** — 단 손실%를 과대계상 → **조기차단(과보수)** 방향이라 안전측 오류(구멍 아님).
- **결론**: daily_loss_limit은 이미 -3% 작동 중이고 데몬은 과보수적 → **완화/변경 불필요·미적용**(데몬 계산 정정은 게이트를 느슨하게 만들어 안전상 보류).

### 포렌식 (전문가 우선순위 지적)
- **"?" 미귀속 -1,491,463원 = 수동매매**: 전량 예스티(122640, 9건 -1.39M)·다스코(058730, 12건 -96K), 둘 다 order_audit 매수기록 없음 = 사용자 수동/외부 진입. **자동전략 버그 아님**(태깅 정상, auto-SL 제외 대상). 자동전략들은 이 손실과 무관.
- **전략간 교차진입**: 87 매수종목 중 **28종(32%)을 2+ 전략이 매수**(예: 080220=closing_bet+gold_zone+supertrend). 종목당 전략횡단 집중 캡 부재 → 손실 증폭 가능. **후속(코드): per-symbol cross-strategy 캡 검토.**

### 잔여 후속(HITL/코드)
1. limit_up_chase: overnight 재도입하려면 `_run_exit_cycle`에 carry_gap_stop 실배선 필요(현재 daily).
2. CB auto-sell 체결확인(접수정체 대응) — ka10075/잔고재조회.
3. 전략횡단 종목 집중도 캡.
4. zone avg_loss>SL(3.3배) 근본원인 포렌식(SL미발화/갭/DCA).

## 9. [2026-07-02 자율조사] zone SL 괴리 근본원인 규명 (퀀트 후속 #4)

퀀트가 "avg_loss -5% vs SL -1.5%(3.3배)" 근본원인 규명을 우선순위로 지목 → 조사 완료. **"SL 미발화"가 아니라 아래 3중 구조.**

### (1) 실제 enforced SL은 -4% (게이트 분절)
- 튜닝에서 본 SL -1.5%는 **전략 exit_plan**(sf_zone.py:80) 값. 그러나 **데몬 holding_evaluator 프로파일이 f/sf/gold 전부 `stop_loss_pct=-4.0`으로 덮음**(holding_evaluator.py:108/118/128). 데몬 장중 `evaluate_all`(L447)이 이 -4%로 SL 판정.
- ∴ 평균손실 -5% ≈ SL -4% + 슬리피지/갭. "SL이 안 걸린다"는 오해 — 실제 -4%에서 걸림.

### (2) ★DCA 물타기가 손실 증폭 + 일일손실한도 우회 (핵심 리스크 홀)★
- 데몬은 **의도적 DCA 분할매수** 기능 보유(intraday_buy_daemon.py:458~493). zone 매수일 **69일 중 46일(67%)이 DCA**(동일종목 당일 복수매수). swing_38만 `_NO_DCA_STRATEGIES` 제외.
- **`:469` "BAR-166: DCA는 방어적 매수 — 일일 손실 한도 적용 불필요"** → DCA 매수가 -3% 일일손실 게이트를 **건너뜀**. 나쁜 날 손실종목에 무제한 물타기 가능 = 퀀트가 우려한 "포트폴리오 차단기 부재"의 정확한 실체(차단기는 -3%로 있으나 DCA가 우회).
- 물타기로 평단 기저 확대 → -4% SL이라도 실현손실 절대액이 단일진입 SL 초과. **zone 손실구조의 진짜 증폭기.**
- 오늘(07-02) `042660` gold_zone: DCA 2회 ORDERED + **2건 UNFILLED(접수정체, 6/8 이슈 재현)**.

### (3) swing_38 min_hold_days=3 → 당일 SL 청산 차단
- holding_evaluator.py:152 swing_38 `min_hold_days=3` → 진입 후 3일 미만 청산평가 차단. 당일 급락해도 못 자름 → swing avg_loss -11.36% 설명. (closing_bet은 min_hold 1/max 3.)

### 권고 (HITL — DCA는 의도적 설계라 신중)
1. ★**DCA를 일일손실한도에 편입**★(`:469` 예외 제거) 또는 DCA 전용 일일 상한 — 최우선. 나쁜 날 물타기 폭주 차단.
2. zone 당일 DCA 횟수 캡(예: max 1~2회) — supertrend `MAX_ENTRIES=1` 대칭.
3. mock 접수정체(UNFILLED) 체결확인 로직(§8 auto-sell 후속과 공통).
4. SL 정의 이원화(전략 -1.5% vs 데몬 -4%) 정합 — 게이트 분절 해소의 일부.

## 10. [2026-07-02 자율] DCA 서킷브레이커 우회 — 검증된 수정 스펙 (구현 대기, HITL)

§9 최우선 리스크(DCA가 -3% 일일손실 게이트 우회)의 정확한 수정 스펙. 코드 확인 결과 **우회는 이중 구조** — 스펙도 그에 맞춰 3부분.

### 우회 메커니즘 (확인)
- `intraday_buy_daemon.py:465` DCA 게이트가 `daily_loss_limit_pct=Decimal("-100.0")`로 명시적 무력화.
- `:511-513` DCA `place_buy`가 **`daily_pnl_pct`를 미전달** → 게이트가 현재손익을 default 0.0으로 봄. ∴ 한도값만 -3으로 바꿔도 `0 > -3`이라 영원히 미발동. **한도값+일일손익 이중 우회.**

### 정확한 수정 (default-OFF, byte-identical)
정상 매수경로 패턴 미러: `:1294 daily_pnl_pct = await compute_daily_gate_input(account, balance)` + `:1324 place_buy(..., daily_pnl_pct=daily_pnl_pct)`.

1. **env 플래그**: `BARRO_DCA_RESPECT_DAILY_LOSS`(default 0=현행 -100 exempt, byte-identical).
2. **DCA 함수(~447-527)에서 daily_pnl_pct 계산**: `account` 스코프 확보 후 `dca_pnl = await compute_daily_gate_input(account, balance)`. (`balance`는 :476에 있음; `account`가 해당 함수 스코프에 없으면 호출부에서 인자 전달 필요 — 구현 시 확인.)
3. **게이트 한도 조건부**: `:465` `daily_loss_limit_pct` 를 `_dca_respect ? (정책값 예 SUPERTREND_AUTO_DAILY_LOSS_LIMIT/-3.0) : Decimal("-100.0")`.
4. **place_buy에 전달**: `:511` `gate.place_buy(symbol=h.symbol, qty=tranche.qty, strategy_id=pos.strategy, daily_pnl_pct=dca_pnl if _dca_respect else Decimal("0.0"))`.

### 왜 HITL인가
- DCA는 "BAR-166 방어적 매수"로 **의도적 설계**. 일일손실 편입은 나쁜 날 물타기 폭주를 막지만, 반대로 반등 직전 저점 물타기 기회를 놓칠 수 있음(설계 트레이드오프).
- 대안: 완전 편입 대신 **DCA 전용 별도 상한**(예: -6%)이나 **당일 DCA 횟수 캡**(supertrend MAX_ENTRIES=1 대칭)이 더 균형적일 수 있음.
- ∴ 접근법 선택은 사용자 결정. 위 스펙은 "완전 편입" 버전이며 env default-OFF라 배선해도 무영향, 승인 시 `=1`로 활성.

### [2026-07-02 구현 완료] DCA 서킷브레이커 편입 적용
사용자 승인("스펙대로 구현") → 구현·배포·활성.
- **코드**(main `cfe6e50`, 워크트리 `1fde864`): `intraday_buy_daemon.py` — import에 `DailyLossLimitExceeded` 추가, `_evaluate_and_sell` DCA 게이트에 `BARRO_DCA_RESPECT_DAILY_LOSS` 분기(=1시 `daily_loss_limit_pct=cfg.daily_loss_limit` + `daily_pnl_pct=compute_daily_gate_input(account,balance)` 전달), place_buy에 daily_pnl_pct 전달, DailyLossLimitExceeded 캐치(DCA 중단).
- **검증**: py_compile OK · byte-identical(미설정=-100·0.0 동일) · 476 passed.
- **활성**: `.env.local BARRO_DCA_RESPECT_DAILY_LOSS=1` (백업 .bak.20260702_125947). ★데몬 cron 관리라 **내일 08:58 크론 재기동 시 반영**(장중 데몬 미종료)★. 되돌리기=0.
- **효과**: 일일손실 -3%(계좌대비, 버그없는 compute_daily_gate_input) 도달 시 DCA 물타기 중단 → 나쁜날 물타기 폭주 차단.
