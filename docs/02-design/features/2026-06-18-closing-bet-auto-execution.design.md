---
tags: [design, feature/closing-bet-auto, strategy/closing_bet, status/draft]
---

# 자동 종가베팅(종베) 실행 전략 — 최적 타이밍 매수/매도 상세 설계

> **연관**: [[2026-06-17-thetrading-methodology-uplift.design|방법론 설계]] · [[../../04-report/features/2026-06-18-closing-bet-validation.report|검증 리포트(OOS)]]
> **Project**: BarroAiTrade · **Date**: 2026-06-18 · **Status**: Draft (설계만 — **라이브 미구현**)
>
> **★활성화 게이트(절대 조건)**: 이 자동 실행은 **수동 테스트로 종가진입 종베의 net 수익(비용 0.9% 차감 후 양(+))이 1~2주 확인된 뒤에만** 구현·활성화한다. 현재 백테스트는 브레이크이븐 추정 — 미확인 상태로 실자본 자동매매 금지.

---

## 1. 목표 & 현재 가진 것

**목표**: 종베를 "15:00~15:20 최적 진입 → 익일 아침 트레일링 청산"으로 **자동 실행**. 사용자가 다른 전략을 모두 중지했으므로 종베 전용 운영.

**이미 구현됨(재사용)**:
- `closing_bet_alert_daemon.scan_buy`: 주도주 선정 + 일봉(신고가·장대양봉) + **5분봉+money_flow 게이트** + ctx 주입 → 종베 매수 시그널 산출. **(알림만, 주문 X)**
- `evaluate_holdings.py:90`: 종베 보유분 **강제청산/평가 제외**(수동관리 전용) → 자동 종베는 **전용 매도 모니터**가 필요(일반 청산엔 안 걸림).
- `_scan_and_buy`의 주문 실행부: `LiveOrderGate.place_buy`(dry_run·일일손실·재시도) — 매수 주문 재사용 대상.
- `STRATEGY_EXIT_PROFILES["closing_bet"]`(min1/max3), `closing_bet_positions.json`(포지션 추적), 타 전략 재진입 차단(완료).

**신규 구현(활성화 단계)**: ①매수 시그널 → **실주문 경로**(현재 알림→주문) ②**익일 아침 트레일링 매도 모니터**(전용).

---

## 2. 매수 — 최적 진입 타이밍

### 2.1 진입 조건 (검증된 코어, 이미 scan_buy에 존재)
- **시간창**: 15:00~15:20 KST.
- **선정**: 거래대금 주도주(top N) 중 **신고가 돌파 + 장대양봉**(몸통 ≥5%, 윗꼬리/몸통 ≤1.0).
- **분봉 자금유입 게이트(ON)**: 오전(09:00~11:30)+오후(13:00~15:20) 거래대금 → BOTH/PM_ONLY 통과, **오전유입후死 차단**.
- (zone 게이트는 검증서 악화 확인 → **OFF 유지**.)

### 2.2 ★최적 진입 미세타이밍 (신규)
종가 페이드(15:00 강했다가 종가에 무너짐)를 피하려고 **창 안에서 늦게 확정 진입**:
- `entry_confirm_time = 15:15` (기본): **15:15 이후 첫 적격 봉**에서만 진입. 15:00~15:15 동안 신고가·장대양봉·money_flow가 **유지되는지 확인** 후 진입(오후 지지 확인 = 방법론 "오후에 돈 들어오면 산다").
- 진입가 ≈ 그 시점 현재가(종가 근접).
- **분할 vs 단일**: 동시호가 부분체결 평단왜곡(sync-loss) 회피 위해 **기본 단일 시장가**(limit_up_chase single_tranche 교훈). 옵션으로 15:12+15:18 2분할.

### 2.3 사이징 (소액 시작)
- `max_per_position = 0.02~0.03`(2~3%), **동시 1~2종**, 종베 전용 carry 한도 10%.
- `LiveOrderGate` 재사용(일일손실 한도·재시도·dry_run).

---

## 3. 매도 — 트레일링 (아침 고점 추종) ★사용자 선택

익일 아침(09:00~10:00) 전용 모니터가 1~3분 폴링하며 다음 상태머신을 돈다:

```
상태: armed(트레일 가동 여부), peak(arm 이후 최고가)
매 폴링(현재가 cur):
  ① 하드 손절:  cur ≤ entry×(1−sl_pct)        → 즉시 매도("손절")
  ② 갭하락 가드: 시초 갭 ≤ −gap_down_pct        → 즉시 매도("갭하락")
  ③ arm:  not armed AND cur ≥ entry×(1+tp_arm) → armed=True, peak=cur   (트레일 가동)
  ④ trail: armed → peak=max(peak,cur);
           cur ≤ peak×(1−trail_offset)          → 매도("트레일 청산")
  ⑤ 정산: 시각 ≥ morning_force_time(10:00)      →
           armed면 시장가 매도(이익 잠금) / 미armed면 시장가 매도("10시 정산")
```

**파라미터(대형주 기본)**:
| 항목 | 기본값 | 의미 |
|------|--------|------|
| `tp_arm_pct` | 2.0% | +2% 도달 시 트레일 가동(대형주 정석) |
| `trail_offset_pct` | 1.0% | arm 후 고점 대비 −1% 하락 시 청산 |
| `sl_pct` | 3.0% | 하드 손절 (또는 0.618 이탈 중 보수적) |
| `gap_down_pct` | 3.0% | 시초 갭하락 즉시 손절 |
| `morning_window` | 09:00~10:00 | 트레일 모니터 구간 |
| `morning_force_time` | 10:00 | 미청산 시 시장가 정산("10시까지 정산") |
| `max_hold_days` | 1 (옵션 3) | 기본 1박. D1~D3 모드 시 매일 아침 트레일 반복 |

**트레일 효과**: +2%만 먹고 끝나는 고정TP와 달리, 아침 슈팅이 +4%·+5%로 이어지면 고점−1%까지 따라가 **업사이드를 더 포착**. 단 슈팅이 +2%서 꺾이면 +1% 근처 청산(고정TP보다 약간 적음) — 평균적으로 아침 연속성(OOS 엣지 원천)을 더 먹는 설계.

> 주의: 종베는 `evaluate_holdings`에서 제외돼 있으므로 이 **전용 모니터가 유일한 자동 매도 경로**. 일반 청산과 충돌 없음.

---

## 4. 자동 실행 배선 (활성화 단계 구현)

| 단계 | 무엇 | 재사용 | 신규 |
|------|------|--------|------|
| **매수** | 15:00~15:20 → scan_buy 시그널 → **실주문** | scan_buy(시그널)·`LiveOrderGate.place_buy`·`closing_bet_positions.json` 등록 | 시그널→주문 연결 함수(`_closing_bet_buy`), 진입확정 15:15 게이트 |
| **매도** | 09:00~10:00 전용 트레일 모니터 → 실주문 | `_live_price`·`LiveOrderGate`(매도)·포지션파일 | 트레일 상태머신(`_closing_bet_sell_monitor`) |

- **cron**(기존 4건 형식): 매수 `0,5,10,15,18 15 * * 1-5`(15:15 확정 진입), 매도 `*/2 9 * * 1-5`(09:00~09:59 2분폴링) + `0 10 * * 1-5`(10:00 정산).
- **격리**: 다른 전략 미가동(사용자 중지)이라 충돌 없음. 종베 전용 데몬/스크립트로 운영.
- **dry_run 기본**: `--no-dry-run` 일 때만 실주문(알림 데몬 → 주문 데몬 승격은 명시 플래그).

---

## 5. 안전 게이트 & 활성화 절차

1. **수동 수익 확인(전제)**: 알림 데몬으로 1~2주 → 진입/청산 기록 → **net(비용 0.9% 차감) 양(+)** 확인. 음(−)/브레이크이븐이면 자동화 보류(수수료 협의·진입방식 재검토로 회귀).
2. **소액 자동(1차)**: `max_per_position` 2%, 동시 1종, `--no-dry-run`. 1~2주 실측 모니터(`_daily_strategy_audit`·`verify_eod_data`).
3. **비중 확대(2차)**: 실측 net 양(+)·체결 슬리피지 허용범위 확인 후 비중 단계 상향.
4. **롤백**: env/플래그로 즉시 dry_run 복귀. 실측 승률·트레일 슬립이 OOS와 괴리되면 중단.

---

## 6. config-gating & 신규 파라미터

`ClosingBetParams`/신규 `ClosingBetAutoConfig`(dataclass)에 **기본 보수값**:
- 진입: `entry_confirm_time=15:15`, `single_tranche=True`.
- 트레일: `tp_arm_pct=2.0`, `trail_offset_pct=1.0`, `sl_pct=3.0`, `gap_down_pct=3.0`, `morning_force_time=10:00`, `max_hold_days=1`.
- 사이징: `max_per_position=0.02`, `max_concurrent=1`, `carry_limit_ratio=0.10`.
- 실행: `dry_run=True`(기본), 활성 시 `--no-dry-run`.

모두 env override(기존 `BARRO_*` 패턴). 실거래 숫자 변경은 `barrotrade-code-surgeon` 위임(HITL).

---

## 7. 구현 단계 (수동 수익 확인 후)

1. `ClosingBetAutoConfig` + 진입확정(15:15)·트레일 파라미터.
2. `_closing_bet_buy()` — scan_buy 시그널 → `LiveOrderGate.place_buy` + 포지션 등록(entry_date·entry_price). default dry_run.
3. `_closing_bet_sell_monitor()` — 트레일 상태머신(§3) → `LiveOrderGate` 매도. default dry_run.
4. cron 3줄(매수 확정·아침 트레일·10시 정산).
5. 테스트: 상태머신 단위테스트(arm/trail/SL/gap/force 경로), dry_run 통합.
6. 활성화: 소액 `--no-dry-run` → 모니터 → 확대.

---

## 8. 리스크
- **종가진입 엣지 미확인**: OOS PASS는 익일시초 진입 변형 → 종가진입은 브레이크이븐 추정. **수동 수익 확인이 절대 전제.**
- **오버나잇 갭**: 익일 시초 갭하락(가드 −3%로 1차 방어, 그래도 갭은 종베 최대 리스크).
- **트레일 휩쏘**: 변동성 큰 아침에 trail_offset 1%가 너무 타이트하면 조기청산 → 파라미터 튜닝(측정 후).
- **단일 시장가 슬리피지**: 종가 동시호가/아침 시장가 체결 슬립 → 실측으로 OOS 가정 재현 여부 확인.
- **데이터 의존**: 이브닝 파이프라인 회귀(6/15·6/18) 지속 시 실측 모니터 불가 → P0 운영 복구 선행.
