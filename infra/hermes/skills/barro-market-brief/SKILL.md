---
name: barro-market-brief
description: BarroAiTrade 운영 머신에서 advisory.json·refined_signals.json을 읽어 현재 시장국면·거래대금 집중 테마·포트폴리오 테마 쏠림·최근 매수 verdict를 한국어로 브리핑한다. 운영자가 "오늘 시장 어때", "시장 브리핑", "테마 쏠림 점검", "지금 advisory 상태" 같이 물을 때 사용.
metadata:
  standard: agentskills.io
  scope: read-only
---

# BarroAiTrade 시장 브리핑 (read-only)

운영자가 라이브 매매 상태를 빠르게 파악하도록, **이미 생성된 산출물만 읽어** 브리핑한다.
주문을 내거나 policy/게이트를 바꾸지 않는다(read-only). 모든 수치는 파일에서 인용한다.

## 입력 (운영 머신 경로 — 설치 시 실제 경로로 치환)
- `~/workspace/BarroAiTrade/data/advisory.json` — 종목 verdict + `market_context`/`sector_themes`/`portfolio_signals` 섹션.
- `~/workspace/BarroAiTrade/data/refined_signals.json` — 데몬이 탐지한 당일 신호(regime 포함).
- `~/workspace/BarroAiTrade/logs/decisions/<오늘>.jsonl` — writer가 남긴 verdict 로그.
- `~/workspace/BarroAiTrade/data/policy.json` — 어떤 add-on이 활성인지(`*_enabled`).

## 절차
1. `advisory.json` 을 읽어:
   - **시장국면**: `market_context.regime`/`risk_on`/`confidence`/`strategy_gates`/`source`(snapshot vs snapshot+llm).
   - **거래대금 집중 테마**: `sector_themes.hot` 상위 3 (theme·turnover_pct).
   - **포트폴리오 쏠림**: `portfolio_signals.theme_exposure`(테마별 %)·`concentration_pct`. cap(기본 30%) 초과 테마 강조.
   - **최근 verdict**: `verdicts[]` 중 NO-GO/WAIT 종목.
2. `policy.json` 의 `*_enabled` 로 각 add-on이 **표시 전용인지 실제 게이트 활성인지** 구분해 명시.
3. 결과를 다음 형식의 한국어 브리핑으로 출력(간결, 이모지 최소):
   ```
   🧭 국면: <regime> (<risk-on/off>, 신뢰 NN%, source)
   🔥 핫테마(거래대금): <t1 NN%>, <t2 NN%>, <t3 NN%>
   📦 포트폴리오 쏠림: <테마 NN%>…  (⚠️ cap 초과: …)
   🚦 최근 차단/대기: <NO-GO/WAIT 종목 + 사유>
   ⚙️ 활성 게이트: <enabled add-on 목록 / 없으면 "전부 표시 전용(OFF)">
   ```
4. (선택) 운영자가 "효과 측정"·"shadow"를 요청하면, 셸로
   `cd ~/workspace/BarroAiTrade && ./venv/bin/python scripts/agent_advisory_shadow.py --date <오늘>`
   를 실행해 게이트 반사실 효과를 보고한다. (그 전에 `_daily_strategy_audit.py --date <오늘> --save` 필요할 수 있음.)

## 금지
- 주문 송출·policy.json 수정·게이트 활성/비활성 **금지**(read-only). 활성은 운영자가 HITL로 직접.
- advisory.json 부재/오래됨이면 "writer 미가동 또는 stale" 로 보고(추측 금지).

## 비고
- 파일이 없으면(장 시작 전 등) "아직 산출물 없음 — 데몬/writer 가동 확인" 으로 답한다.
- 이 스킬 본문의 경로는 운영 머신 실제 경로에 맞게 1회 수정(설치 스크립트가 안내).
- agentskills.io 표준 프런트매터는 설치된 Hermes 버전 스펙에 맞게 검증/조정할 것.
