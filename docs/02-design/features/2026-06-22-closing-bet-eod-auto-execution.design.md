---
tags: [design, strategy/closing_bet, feature/eod-auto-execution, governance/d-hitl, status/draft]
---

# 종가베팅 자동매매 EOD dispatch 배선 — 설계

> **연관**: [[2026-06-17-thetrading-methodology-uplift.design|더트레이딩 §6 ClosingBet]] · [[../../04-report/features/2026-06-22-closing-bet-dryrun-disparity.report|종베 dry-run]] · [[../../operations/uplift-deploy-runbook|배포 런북]]
>
> **Summary**: 현재 종베(closing_bet)는 **알림/페이퍼 전용**(`closing_bet_alert_daemon.py`, 주문 0). 이를 **config-gated 자동매매로 승격**하는 EOD dispatch 배선을 설계한다. 핵심 결정: 인트라데이 데몬에 새 EOD 블록을 넣지 않고 **이미 종베 전용 스캔·매도모니터·포지션관리를 가진 alert daemon에 executor를 부착**(격리·재사용·사고면). **결정적 리스크 = 인트라데이 데몬 `_eod_carry_limit`(15:10~15:19 오버나잇 축소)이 종베(의도적 오버나잇)를 청산해버리는 충돌** → 종베 포지션 제외가 필수. 전 항목 default-OFF(알림 전용 유지) → 활성은 dry-run→sim정합→라이브 HITL.
>
> **Date**: 2026-06-22 · **Status**: Draft(설계 — 코드 변경 없음). 구현/활성은 별도 HITL.
> **Scope note**: 본 문서는 청사진. dry-run([[../../04-report/features/2026-06-22-closing-bet-dryrun-disparity.report|이격도 게이트 ON]]) 1~2주 통과가 구현 착수 전제.

---

## 0. 배경
- 종베 진입창 15:00~15:20(`ClosingBetParams.entry_window_start/end`), 청산 익일 10:00 / D1~D3, 오버나잇 필수.
- 현 상태: `_DEFAULT_ENABLED["closing_bet"]=False` + **2026-06-18 사용자 결정 "종베=수동관리 전용"**. 인트라데이 데몬 진입 cutoff 14:30이라 종베창(15:00~) 자동진입은 별도 dispatch 필요(설계 §6.5).
- 자산: `closing_bet_alert_daemon.py`(scan_buy 15:00~15:20 리더스캔 + scan_sell TP/SL/MORNING/D3 + 포지션 store), `closing_bet.py`(이격도 게이트 dry-run ON), `STRATEGY_EXIT_PROFILES["closing_bet"]`(이미 존재).

## 1. 설계 결정 — 어디에 배선하나

| 안 | 내용 | 평가 |
|----|------|------|
| A. 인트라데이 데몬 EOD 블록(설계 §6.5 원안) | `intraday_buy_daemon.py`에 15:00~15:20 종베 dispatch 추가 + `_CUTOFF_EXEMPT_STRATEGIES`+=closing_bet | 데몬 비대·기존 zone/carry 로직과 결합 위험↑ |
| **B. alert daemon 승격(채택)** | `closing_bet_alert_daemon.py`에 **executor 부착**(scan_buy/scan_sell가 알림 대신/과 함께 주문) | 이미 종베 전용·격리. 재사용 최대, 사고면 최소 |

→ **B 채택.** alert daemon은 이미 정확한 창·리더스캔·money_flow 게이트·매도조건(TP/SL/MORNING/D3)·포지션 store를 가짐. executor만 config-gated로 추가. 인트라데이 데몬은 **carry 충돌 해소(§3.1)만** 손댄다.

## 2. 아키텍처

### 2.1 BUY (15:00~15:20, `scan_buy` 확장)
```
leaders = KiwoomNativeLeaderPicker(min_flu_rate=1.0).pick(top_n)
for lc in leaders:
    sig = closing_bet.analyze(ctx[일봉+5분봉+leader_meta])   # 이격도 게이트 ON(env)
    if sig is None: continue
    if not _autoexec_enabled: notify(매수신호); continue       # default: 알림만(현행)
    if not _cb_entry_guard(account): notify(차단사유); continue # §3.2 동시종목·비중·carry
    qty = _size(position_value, sig.price)                     # 단일 트랜치(§3.3)
    r = await gate.place_buy(symbol, qty, price=sig.price, strategy="closing_bet")
    if r.ok:
        add_position(symbol, sig.price, qty, tp=4.5, sl=5.0,    # 즉시 등록(보호 §3.1)
                     stop_fib=sig.metadata["stop_fib_price"])
        notify(매수 체결/주문)
```
- `gate.place_buy`는 `KiwoomNativeOrderExecutor(oauth, dry_run)` + `LiveOrderGate`(인트라데이 데몬 패턴 재사용). dry_run=True면 미체결.
- **체결 즉시 `closing_bet_positions.json` 등록** → 다른 클리어러/§3.1 carry가 즉시 인지(보호).

### 2.2 SELL (loop, `scan_sell` 확장)
```
for p in load_positions():
    cur = live_price(p.symbol)
    for (sig, reason) in sell_signals(p, cur, now):   # TP/SL/MORNING/D3 (기존 로직)
        if not _autoexec_enabled: notify(매도신호); continue
        r = await gate.place_sell(symbol, qty=p.qty, strategy="closing_bet")
        if r.ok: mark_closed(p); notify(매도 체결)
```
- `sell_signals` 임계는 `STRATEGY_EXIT_PROFILES["closing_bet"]`(SL−5/TP+4.5/partial2.7/trailing3.5/min1·max3)과 **통일**(현재 alert daemon은 add시 tp/sl 인자 — 프로파일에서 끌어오도록 정합).

## 3. 리스크 가드 (필수 신규)

### 3.1 ★carry-limit 충돌 해소 (최우선)
- 문제: `intraday_buy_daemon._eod_carry_limit`(15:10~15:19, `BARRO_CARRY_LIMIT_RATIO=0.20`)이 **전체 holdings를 축소** → 종베(15:00~15:20 진입, 오버나잇)를 **즉시 청산**.
- 해법: `_eod_carry_limit`의 holdings에서 **`_closing_bet_held()`(closing_bet_positions.json) 심볼 제외**. (이미 다른 클리어러/DCA가 쓰는 동일 제외 패턴 재사용 — `intraday_buy_daemon.py:402`.)
- 추가: 종베 자체 carry는 §3.2로 별도 관리.

### 3.2 종베 전용 진입 가드 `_cb_entry_guard`
- **동시 보유 ≤ 1~2종목**(설계 §6.6, `BARRO_CB_MAX_POS`).
- **종목당 비중 ≤ 계좌 10%**(`BARRO_CB_MAX_PCT`, position_value 산정).
- 이미 보유/당일 진입분 중복 차단(closing_bet_positions.json + 브로커 잔고).

### 3.3 분할매수 미체결 회피
- 종가 동시호가 분할은 회차별 부분체결로 평단 왜곡 → **단일 트랜치**(limit_up_chase `single_tranche` 선례). `_NO_DCA_STRATEGIES`에 closing_bet 추가(DCA 금지).

### 3.4 오버나잇 갭
- `STRATEGY_EXIT_PROFILES["closing_bet"]` SL−5%(0.618 이탈+갭 흡수) + 익일 10:00 시간청산(MORNING) 2차망. overnight_gap_stop는 sell_signals MORNING/SL로 커버.

## 4. config-gating (default-OFF parity)
| 키 | default | 효과 |
|----|---------|------|
| `BARRO_CB_AUTOEXEC` | 0(OFF) | 0=알림만(현행 byte-identical), 1=주문 |
| `--dry-run`(데몬) | ON | 주문 미체결(dry-run 관찰) |
| `BARRO_CB_DISPARITY_YELLOW` | 0 | 이격도 게이트(이미 구현) |
| `BARRO_CB_MAX_POS` / `BARRO_CB_MAX_PCT` | 2 / 0.10 | 동시종목·비중 |
| `BARRO_CARRY_LIMIT_RATIO` 종베 제외 | — | §3.1(코드 제외, 토글 아님) |

- `BARRO_CB_AUTOEXEC=0` → executor 미부착 → 알림 전용(현행). 활성해도 `--dry-run`이면 미체결.

## 5. 구현 단계 (별도 PR, dry-run 통과 후)
1. `closing_bet_alert_daemon.py` — executor/gate 부착(scan_buy place_buy + 즉시 등록 / scan_sell place_sell), `_autoexec_enabled`·`_cb_entry_guard`·단일트랜치. **default 알림 전용.**
2. `intraday_buy_daemon.py::_eod_carry_limit` — holdings에서 `_closing_bet_held()` 제외(§3.1). `_NO_DCA_STRATEGIES`+=closing_bet.
3. sell_signals 임계를 `STRATEGY_EXIT_PROFILES["closing_bet"]`과 정합.
4. 테스트: `_cb_entry_guard`(동시·비중 한도), carry 제외 parity, dry-run시 주문 0, default-OFF=알림전용 parity.

## 6. 검증 & HITL
- **순서**: ① dry-run(이격도 ON, 알림/페이퍼) 1~2주 → ② `BARRO_CB_AUTOEXEC=1 --dry-run`(주문 로직만 검증, 미체결) 1주 → ③ sim-live 정합(종가체결·익일갭 슬리피지) → ④ 라이브(`--no-dry-run`) ★HITL, 소액 1종목부터.
- **OOS caveat**: 종베 OOS PASS는 '익일시초 진입' 변형, '종가진입'은 브레이크이븐 → ②③에서 실신호 직접 측정 필수.
- **HITL 게이트**: `BARRO_CB_AUTOEXEC=1` + `--no-dry-run` 동시 = 실거래 → 명시 승인. carry 한도·동시종목 변경도 HITL.
- 롤백: `BARRO_CB_AUTOEXEC=0`(알림 전용 복귀) 즉시.

## 7. 한계/주의
- 2026-06-18 "수동관리 전용" 결정 반전 — 자동매매 전환은 사용자 명시 승인 후.
- 종가 동시호가 체결가 불확실(단일가) → sim-live 괴리, 소액 검증 필수.
- 오버나잇 갭은 구조적 리스크(6/10 −845K 사례) — 동시 1~2종·비중 10% 상한 엄수.
- §3.1 미적용 시 종베가 carry 한도에 즉시 청산됨 → **구현 시 §3.1 최우선**.
