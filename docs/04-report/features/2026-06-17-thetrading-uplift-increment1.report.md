---
tags: [report, feature/thetrading-uplift, increment/1, status/complete]
---

# 더트레이딩 고도화 Increment 1 — 구현 결과 리포트

> **연관 설계**: [[../../02-design/features/2026-06-17-thetrading-methodology-uplift.design|매매전략 분석·설계 문서]]
> **연관 분석**: [[../../03-analysis/2026-06-17-thetrading-methodology-extract|방법론 추출 부록]]
>
> **Project**: BarroAiTrade
> **Date**: 2026-06-17
> **Branch**: `feat/thetrading-uplift-increment1`
> **Status**: Complete — 테스트 1446 passed
> **분류**: 전부 **(c) 안전**(신규/기본OFF/inert) — **라이브 매매 동작 무변경**

---

## 1. 요약

설계 문서의 고도화 로드맵 중 **자동 구현이 안전한 (c)분류 항목만** 1차 구현했다. 핵심은 **종가베팅(종베) 전용 전략 모듈 신설**(기본 비활성)과 **갭가드 전략집합의 env화**(기본값 동일)다. 모든 변경은 추가(additive)·기본 OFF·inert라 **현재 라이브 매매 흐름에 영향이 없으며**, 전체 회귀 테스트 1446건이 통과했다.

실거래 동작을 바꾸는 **(d)분류**(종베 라이브 ON, sf_zone 갭가드 실제 차단, 비용모델 정정, top5 hard-cut, swing_38 보유한도 단축)는 **자동 진행하지 않았다** — 측정·스캐폴딩까지만 준비하고 HITL 승인 단계로 남겼다.

---

## 2. 구현 단계 (7단계 자동 진행)

| 단계 | 내용 | 분류 | 결과 |
|------|------|------|------|
| **S1** | `closing_bet.py` 신규 — `ClosingBetParams` + `ClosingBetStrategy` | c (신규) | ✅ 302줄 |
| **S2** | `signal.py` `EntrySignal.signal_type` Literal += `"closing_bet"` | c (additive) | ✅ |
| **S3** | `holding_evaluator.py` inert 종베 청산 프로파일(min 1/max 3, SL −5%) | c (inert) | ✅ |
| **S4** | `signal_scanner.py` 종베 `False` 등록 + 우선순위(8) + 일봉 dispatch(OFF guard) | c (OFF) | ✅ |
| **S5** | 데몬 `_GAP_GUARD_STRATEGIES` → `BARRO_GAP_GUARD_STRATEGIES` env화(**기본값 동일**) | c (불변) | ✅ |
| **S6** | `test_closing_bet.py` 단위테스트 14건 | c | ✅ 14 passed |
| **S7** | 전체 회귀 테스트 | - | ✅ **1446 passed, 0 failed** |

---

## 3. 종베 전략(`ClosingBetStrategy`) 구현 범위

**구현(일봉 스캐폴드, 자기완결·테스트가능)**:
- **진입 시간창 게이트**: `ctx.timestamp`(KST)가 15:00~15:20 밖이면 진입 거부 — 종베의 핵심 차별점.
- **신고가 돌파**(직전 60봉 고점 초과) + **기준봉 장대양봉**(몸통 ≥5%, 윗꼬리 제한).
- **청산(`exit_plan`)**: 익일 슈팅 익절(+4.5%/대형주 +2%), 0.618 이탈 가격기반 SL, 익일 10:00 시간청산, D1~D3 보유한도. 오버나잇 의미론(진입일 당일 time_exit skip 필요)은 docstring에 명시.

**기본 비활성 옵션(라이브 활성 단계에서 분봉/선정 컨텍스트 주입 후 ON)**:
- 분봉 자금유입(오전/오후) 게이트, 존(골드존 0.5~0.618) 진입가, 거래대금 rank/시총 hard-cut.

> 이유: 자금유입·존 진입가는 intraday 데이터, 선정컷은 leader 메타가 필요해 일봉만으로 근사 불가. 활성화는 데이터 플러밍을 동반하는 별도 (d) 단계.

**스캐너 등록**: `_DEFAULT_ENABLED["closing_bet"]=False`. 활성화는 `SignalScanner(..., enabled_strategies={"closing_bet": True})`. OFF인 동안 dispatch가 실행되지 않아 라이브 무영향(테스트 `test_scanner_default_off`로 검증).

---

## 4. 갭가드 env화 (설계 §4.3 — sf_zone "진짜 구멍" 대응)

`scripts/intraday_buy_daemon.py`:
```python
_GAP_GUARD_STRATEGIES = _parse_strategy_set(
    "BARRO_GAP_GUARD_STRATEGIES", _MEANREV_STRATEGIES | {"f_zone"})  # 기본 {gold_zone, f_zone}
```
- **미설정 시 기존과 100% 동일** → 동작 불변((c) 안전). 회귀 테스트 `test_default_set_unchanged` 통과.
- 운영이 **코드배포 없이 env로 sf_zone 편입** 가능: `BARRO_GAP_GUARD_STRATEGIES="gold_zone,f_zone,sf_zone"`.
- ⚠️ 실제 env 설정으로 차단을 켜는 것은 **선정 동작 변경 = (d) HITL** (env 설정 자체가 인간 승인 게이트). 측정축(시초 open-gap vs 장중 flu)·임계는 gap_records 누적 후 데이터 기반 결정.

---

## 5. 변경 파일

| 파일 | 변경 | 성격 |
|------|------|------|
| `backend/core/strategy/closing_bet.py` | +302 (신규) | 종베 전략 모듈 |
| `backend/tests/strategy/test_closing_bet.py` | +161 (신규) | 단위테스트 14건 |
| `backend/core/scanner/signal_scanner.py` | +43/−13 | 종베 OFF 등록 + 일봉 dispatch |
| `backend/core/risk/holding_evaluator.py` | +17 | inert 종베 청산 프로파일 |
| `scripts/intraday_buy_daemon.py` | +18 | 갭가드 env화(기본 동일) |
| `backend/models/signal.py` | +1/−1 | signal_type Literal += closing_bet |

합계: **6 files, +530/−13**.

---

## 6. 검증

- **신규 테스트 14건**: 상속·캔들부족·진입창 게이트(밖→None)·신고가/장대양봉 게이트·진입신호·ExitPlan 오버나잇 의미론·대형주 2% TP·inert 프로파일·resolve_policy 버전매핑·스캐너 기본 OFF·갭가드 env 기본값 불변/sf_zone 편입.
- **전체 회귀**: `backend/tests/` **1446 passed, 10 skipped, 0 failed** (6.3s).
- **핵심 불변 단언**: `test_daemon_fill_reconcile`(gold/f_zone ∈ 갭가드 유지), `test_signal_scanner_phase_c`(스캔결과에 closing_bet 미출현=OFF 확인) 모두 통과.

---

## 7. 자동 진행하지 않은 것 — (d) HITL 잔여

| 항목 | 상태 | 활성 방법 |
|------|------|-----------|
| 종베 라이브 ON | 스캐폴딩 완료(OFF) | 분봉/선정 컨텍스트 주입 + `enabled_strategies` + OOS PASS 후 |
| sf_zone 갭가드 실제 차단 | env 훅 준비 | `BARRO_GAP_GUARD_STRATEGIES` 설정(인간 승인) |
| 비용모델 정정(0.00175→0.0035) | 미착수 | `code-surgeon` 위임 + AskUserQuestion |
| 대장주 top5 hard-cut + 신고가 선정 | 미착수 | LeaderPicker 인자 + 선정 시뮬 검증 |
| swing_38 보유한도 20→3 | 미착수 | 다단계(25%한도) + 각 단계 OOS |
| 진입가 위치 결합 게이트(P0-1) | 미착수 | 데몬 shadow → 캘리브 → (d) |

---

## 8. 다음 단계 (권고)

1. **종베 백테스트**: `IntradaySimulator` 다일 윈도우로 종베 오버나잇 청산 시뮬 → OOS(`_oos_validation.py`) PASS 게이트 통과 전 라이브 자본 편입 금지.
2. **P0-1 shadow**: 진입가 위치 측정을 데몬에 shadow(차단 X)로 추가 → `gap_records`+위치 분포 N일 누적 → 임계 캘리브 → (d) 활성.
3. **비용모델 정정**: `code-surgeon` 위임(단일 상수, 선정영향 → HITL).

> 본 Increment는 `feat/thetrading-uplift-increment1` 브랜치에 격리. 커밋/머지는 사용자 검토 후.
