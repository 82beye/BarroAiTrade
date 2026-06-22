# 에이전트 자문(advisory) 운영 런북 — git pull → 바로 사용

> 전제: 개발 머신에서 구현·검증 완료. **운영 머신은 `git pull` 후 1커맨드 부트스트랩**이면 사용 가능.
> 기본 상태는 **라이브 매매 무영향**(데몬 게이트 default-OFF). 게이트 활성만 ★HITL.
> 설계: [[2026-06-22-agent-advisory-realtime.design]] · 거버넌스: [[recommendation-policy]]

## 0. 한 줄 요약
`git pull` → `make advisory-setup` → (Phase1) `make advisory-writer` 스케줄 → (Phase3, HITL) `policy.json` 플래그 ON.

## 1. 배포 (운영 머신)
```bash
cd ~/workspace/BarroAiTrade
git pull origin main
make advisory-setup          # 멱등·비파괴: 디렉터리 생성 + 의존성/env 점검 + mock 스모크
```
`advisory-setup`이 자동으로:
- `data/ · logs/decisions/ · workspace/_intraday/` 생성(없으면).
- advisory 모듈 import / `claude` CLI / `hermes`(선택) / `ANTHROPIC_API_KEY` / Telegram env 점검(경고만).
- writer **mock 1회 스모크**(토큰 0, 라이브 무영향).

> 이 시점에서 **데몬은 무변경**(게이트 OFF). pull만으로 라이브 동작이 바뀌지 않는다.

## 2. Phase 1 — 실시간 표시 (라이브 무영향)
writer를 가동하면 신호마다 GO/WAIT/NO-GO가 **Telegram + `logs/decisions/<date>.jsonl`** 에 실시간 표시된다(주문 영향 0).
```bash
# 수동(포그라운드 확인용)
make advisory-writer                 # claude-cli, 30s 루프, --telegram
# 또는 launchd 상시 가동
cp infra/com.barro.advisory-writer.plist.example ~/Library/LaunchAgents/com.barro.advisory-writer.plist
#  → plist 안의 USERNAME/경로 수정 후:
launchctl load -w ~/Library/LaunchAgents/com.barro.advisory-writer.plist
```
LLM 키 없거나 절약 시 `--backend mock`(결정적 룰)로도 표시 가능. **검증**: 신호 발생 수초 내 Telegram verdict / `order_audit.csv`는 baseline과 동일.

## 3. Phase 2 — shadow 측정 (라이브 무영향)
writer가 `data/advisory.json`을 계속 갱신한다. 데몬 리더는 코드에 이미 있으나 **OFF라 읽기만 안 함** → advisory가 게이트했을 매매를 사후 비교한다(≥1~2주, 진실원천 `_daily_strategy_audit.py`). net-of-cost 개선이 확인되면 Phase 3.

**일자별 측정**(read-only, `logs/decisions/<date>.jsonl` + `order_audit.csv` + `reports/strategy_audit_<date>.json` 사용):
```bash
# 먼저 진실원천 audit 산출(없으면): python scripts/_daily_strategy_audit.py --date <D> --save
make advisory-shadow DATE=2026-06-23          # → reports/advisory_shadow_<D>.md/.json
#  improvement(+)=손실 회피 우위 / (−)=승자 제거 우위. ≥1~2주 누적 부호로 Phase3 판단.
```
판정은 **claude-cli 백엔드 실판단 누적**이 가장 의미 있다(mock은 룰 데모). 종목 단위 집계·자본 재배분 미반영은 도구가 한계로 명시한다.

## 4. Phase 3 — 게이트 활성 (★HITL)
```bash
# data/policy.json 에 추가 (jq 로 키만 — 통째 덮어쓰기 금지)
jq '. + {"agent_advisory_enabled": true, "agent_advisory_ttl_sec": 180}' data/policy.json > /tmp/p && mv /tmp/p data/policy.json
# 데몬은 매 사이클 policy.json 을 재로딩 → 다음 사이클부터 NO-GO 신호 매수 차단
```
- 보수적으로 WAIT도 차단: `"agent_advisory_block_wait": true`.
- 저신뢰 무시: `"agent_advisory_min_confidence": 0.6`.
- **롤백(즉시)**: `agent_advisory_enabled` → `false`. 데몬 byte-identical 복귀.

## 4b. 시장-맥락 add-on (regime · 테마 거래대금 집중 · 포트폴리오 쏠림/리스크)
종목 verdict 외에 시장 전반 신호를 같은 advisory.json 으로 소비한다. 설계: [[2026-06-23-market-context-addons.design]].
- **생산(자동)**: 데몬이 `data/market_snapshot.json`(regime·거래대금·보유)을 덤프 → writer 가 `data/theme_map.json` 과 조인해 advisory.json 의 `market_context`/`sector_themes`/`portfolio_signals` 섹션 생산. `git pull` 후 추가 설정 불요(생산은 결정적, 라이브 무영향).
- **LLM 오버레이(opt-in)**: market_context(시장국면)에 LLM 판단을 얹으려면 writer 에 `--market-llm`(또는 env `BARRO_MARKET_LLM=1`):
  ```bash
  make advisory-writer ARGS=--market-llm          # 또는: python scripts/agent_advisory_writer.py --interval 30 --backend claude-cli --market-llm --telegram
  ```
  실패/응답불가 → 결정적 regime base 로 fail-open. 결정적 테마 집계·가드는 무관(LLM은 soft 신호만).
- **실시간 표시**: writer `--telegram` 이면 verdict 외에 **시장국면·거래대금 집중 테마·포트폴리오(테마노출/집중/과다)** 요약도 텔레그램 전송(내용 변경 시만, 스팸 방지). 게이트 활성과 무관한 *표시*라 라이브 무영향.
- **테마 매핑 유지보수**: `data/theme_map.json`(symbol→[테마]) 커버리지 = 쏠림 가드 정확도. 신규 주도주는 여기에 추가. 미매핑 종목은 가드 미적용(fail-open).
- **활성(★HITL, off→soft→hard)**: `data/policy.json` 플래그(jq 로 키만):
```bash
# 포트폴리오 테마 쏠림 가드 — soft(사이징 축소) 먼저
jq '. + {"portfolio_theme_enabled": true, "portfolio_theme_mode": "soft", "portfolio_max_theme_pct": 0.30}' data/policy.json > /tmp/p && mv /tmp/p data/policy.json
# shadow 입증 후 hard(차단) 승격:  "portfolio_theme_mode": "hard"
# 시장국면(risk-off → max_buy 축소):  "market_context_enabled": true
# 거래대금 집중 핫테마 우선(under-exposed 신호 앞으로):  "sector_themes_enabled": true
#   (옵션) "sector_underexposed_max_pct": 0.30  "sector_min_turnover_pct": 0.05
# 포트폴리오 리스크 throttle:  "portfolio_risk_enabled": true
```
- **롤백(즉시)**: 해당 `*_enabled` → `false`. 데몬 byte-identical(사이징 ×1.0) 복귀.

## 5. 안전 불변식
- LLM은 주문 동기 경로에 **없음** — 데몬은 미리 계산된 advisory.json만 읽고, 없음/stale/저신뢰/파싱실패 → **fail-open**(베이스라인 매매).
- 게이트 OFF(기본) → 데몬 byte-identical. 활성/사이징/청산 확장은 전부 (d) HITL.
- writer 비정상(프로세스 다운)이어도 advisory.json이 stale → 데몬 자동 fail-open. 라이브는 절대 멈추지 않음.

## 6. 트러블슈팅
- Telegram 미표시: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env, `--telegram` 플래그 확인.
- verdict 0건: `data/refined_signals.json`에 신호가 있는지(장중·전략 활성), writer 로그 확인.
- claude-cli 실패: `command -v claude`, `ANTHROPIC_API_KEY` — 실패해도 해당 종목 verdict 없음 → 데몬 fail-open(안전).
- 게이트가 안 먹힘: `policy.json`의 `agent_advisory_enabled`가 true인지, verdict ts가 TTL 내인지.
