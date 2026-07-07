# 권고 제안 및 구현 정책 (Phase 5)

매매복기 후 도출한 권고를 **제안하고 안전한 것은 구현**한다. 핵심: 실거래 동작을 바꾸는 변경은 HITL(사용자) 승인 전에 default 를 바꾸지 않는다. 이 정책은 2026-06-15→06-16 실제 흐름(AskUserQuestion → verify_eod_data 구현)을 정식화한 것이다.

## 분류 기준 — 모든 권고를 4가지 중 하나로

| 분류 | 정의 | 처리 |
|---|---|---|
| **(a) 운영/데이터** | 운영 머신 배포·수수료 요율 협의·이브닝 파이프라인 재실행·ka10073 재수집 등. 코드 변경 아님. | 리포트와 응답에 **사용자 액션**으로 명시. 실행 불가(원격/권한). |
| **(b) 이미 구현됨** | 권고 메커니즘이 이미 코드에 존재(미배포/미설정일 뿐). | 신규코드 금지. 배포 안내 또는 env 토글 제시. 아래 "기존 메커니즘" 표 참조. |
| **(c) 안전 개선** | 실거래 동작 무영향: 관측성·자가검증·로깅·테스트·문서, 또는 config-gated **default-OFF** 신규기능. | **즉시 구현** + 테스트 + 커밋(푸시는 승인). |
| **(d) 실거래 파라미터** | 매매 동작을 바꿈: 진입/청산 임계, 전략 가중, 갭가드 범위, 재시도 정책 등. | **AskUserQuestion 으로 값/방향 확정 후** 구현. 기본값은 현행 유지(또는 config-gated). 데이터 부족 시 "측정 후 결정" 권고. |

## 처리 절차

1. **제안(항상)**: 권고를 P0/P1/P2 우선순위로 리포트에 싣고, 각 권고에 분류 (a)~(d) 를 태깅.
2. **(c) 자동 구현**: worktree(또는 메인) 에서 구현 → 관련 테스트 작성·실행 → 회귀 테스트 → 사용자에게 결과 보고 → **푸시는 명시 승인 후**(main 직접 푸시는 분류기가 차단함, 사용자 "푸시해줘" 필요).
3. **(d) HITL 게이트**: 구체적 후보를 `AskUserQuestion`(multiSelect) 으로 제시 — 각 옵션에 트레이드오프/리스크 명시, 추천 옵션 먼저(+"(추천)"). 선택된 것만 **config-gated**(default 는 현행 동작 보존) 로 구현 후 테스트.
4. **(a)/(b)**: 실행 불가/불필요 → 명확히 안내(운영 머신 한 줄 명령 등).

## 기존 메커니즘 (분류 (b) — 이미 코드에 있음, 미배포/설정 문제)

복기에서 "이걸 추가하자" 권고가 나와도 아래는 이미 존재한다. 신규 구현 대신 배포/토글로 처리.

| 메커니즘 | 위치 | 현재값/토글 |
|---|---|---|
| 갭가드(시초갭 추격 차단) | `scripts/intraday_buy_daemon.py` `_ZONE_MAX_FLU` | 15%, env `BARRO_ZONE_MAX_FLU`. 적용 `_GAP_GUARD_STRATEGIES={gold_zone, f_zone}` (sf·supertrend 제외) |
| 진입 컷오프(이월 차단) | 동 `_zone_entry_cutoff_passed` | 14:30, swing_38 예외 |
| 매도 재시도 | `backend/core/risk/live_order_gate.py` | `retry_sell_only=1`(매도만), env `SUPERTREND_AUTO_RETRY_SELL_ONLY=0` 로 매수도 |
| 거래비용 실측 | `backend/core/trading_costs.py` | 편도 0.175%+매도세 0.20%, env `BARRO_COMMISSION_RATE` |
| EOD 매수 스냅샷 | `scripts/intraday_buy_daemon.py` `_eod_buy_snapshot` | buy_audit.csv (BAR-OPS-39) |
| 일일매수한도 | `live_order_gate.py` | policy `daily_max_orders`(데몬 300), limit_up_chase 별도 보수캡 |

## (d) 실거래 파라미터 — 자주 나오는 후보와 주의점

- **갭가드 임계 하향(15→12) / sf_zone 편입**: 고갭 손실(대우건설 sf +20.6% -181K)을 잡지만, 고갭 흑자도 있음(6/15 한온 sf +22% 흑자). → **일괄차단보다 진입가 위치(눌림 여부) 결합**. 단순 하향은 양날. 반드시 AskUserQuestion.
- **supertrend 회전/청산 튜닝**: 오버나잇 보유는 강하고(6/16 이월 +447K) 당일 고갭 진입은 약함(신규 7건 -474K). 청산 임계 변경은 실측 누적 후. 추측 변경 금지.
- **수수료 요율**: 비용이 gross 를 거의/전부 잠식(6/12 1.6배·6/15 5.7배·6/16 0.97배)하는 구조적 1순위지만 이는 (a) 운영(협의)이지 코드 아님. 협의 성사 후 `BARRO_COMMISSION_RATE` 설정.

## 전문가 에이전트 위임 (barrotrade-*)

권고를 손으로만 처리하지 말고 등록된 전문가 에이전트(Agent tool, `subagent_type`)에 위임한다. **제약**: 이 에이전트들은 `workspace/_intraday/<date>/`(signals/executions/pnl/incidents) + `workspace/_memory/semantic/` 레이아웃을 가정한다. 이 머신엔 그 `workspace/`가 없을 수 있으므로(BarroTrade 오케스트레이션 미가동), 입력을 프롬프트에 직접 실어 보내는 **브리지** 후 위임하고, 실패 시 폴백한다.

| 분류/단계 | 에이전트 | 위임 내용 | 폴백(에이전트/워크스페이스 불가) |
|---|---|---|---|
| (d) 승인 후 파라미터 패치 | `barrotrade-code-surgeon` | 승인된 권고를 recap §5 형식으로 전달 → PolicyConfig(우선)/strategy dataclass **숫자 default만** 바꾸는 unified diff + proposal.md. **git apply 안 함, HITL 결재 후 적용.** AST 검증·temp 0.1. | 직접 config-gated 구현(기본값 현행 유지) + 테스트 |
| Phase 3 손실 관점 | `barrotrade-self-reflector` | 손절·고갭 손실의 "무시된 리스크 신호 / 재진입 금지 오판 패턴" 추출 | 그 렌즈만 수동 적용해 손실 해설 작성 |
| 대체 recap(라이브) | `barrotrade-intraday-reporter` | `workspace/_intraday/` 실재 시 recap+§5 권고 직접 작성 | 본 스킬 Phase 2-4(EOD-zip+_daily_strategy_audit) 사용 |
| 리스크 사이징/스탑(선택) | `barrotrade-risk-manager` | ATR 포지션 사이징·트레일링·VaR 권고 검토 | 권고 본문에 수동 명시 |

**code-surgeon 위임 절차 (d)**:
1. AskUserQuestion 으로 값/방향 승인 받음.
2. 권고를 recap §5 형식(종목 또는 PolicyConfig 필드 / 현재 default → 제안 default / 근거 1~2줄)으로 작성.
3. `Agent(subagent_type="barrotrade-code-surgeon", prompt=<§5 권고 본문>)` 호출.
4. 반환된 unified diff + proposal.md 를 **사람이 검토**(HITL) — 숫자 default 외 변경이 없는지, PolicyConfig 우선인지 확인.
5. 승인 시에만 적용(패치 적용 + 테스트). 에이전트가 워크스페이스 부재로 실패하면 폴백(직접 config-gated).

핵심: code-surgeon 은 "숫자 default 만, AST 검증, HITL 강제, 직접 apply 금지"라 (d) 의 안전판으로 직접 손편집보다 우수하다. **단 위임해도 HITL 결재 단계는 생략 불가**.

## 안전 가드(불변)
- HITL 승인 전 자가진화/파라미터 튜닝 코드 적용 금지(MEMORY feedback).
- 신규 거래동작은 config-gated default-OFF + 테스트 동반(레포 관행: round_figure, BAR-OPS-39).
- main 직접 푸시는 사용자 명시 승인. 커밋까지는 OK, 푸시는 "푸시해줘" 후.
- 옵션 표기에 그리스/일본어 문자 금지(1/2/3 또는 A/B/C).
