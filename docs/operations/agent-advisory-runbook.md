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

## 4. Phase 3 — 게이트 활성 (★HITL)
```bash
# data/policy.json 에 추가 (jq 로 키만 — 통째 덮어쓰기 금지)
jq '. + {"agent_advisory_enabled": true, "agent_advisory_ttl_sec": 180}' data/policy.json > /tmp/p && mv /tmp/p data/policy.json
# 데몬은 매 사이클 policy.json 을 재로딩 → 다음 사이클부터 NO-GO 신호 매수 차단
```
- 보수적으로 WAIT도 차단: `"agent_advisory_block_wait": true`.
- 저신뢰 무시: `"agent_advisory_min_confidence": 0.6`.
- **롤백(즉시)**: `agent_advisory_enabled` → `false`. 데몬 byte-identical 복귀.

## 5. 안전 불변식
- LLM은 주문 동기 경로에 **없음** — 데몬은 미리 계산된 advisory.json만 읽고, 없음/stale/저신뢰/파싱실패 → **fail-open**(베이스라인 매매).
- 게이트 OFF(기본) → 데몬 byte-identical. 활성/사이징/청산 확장은 전부 (d) HITL.
- writer 비정상(프로세스 다운)이어도 advisory.json이 stale → 데몬 자동 fail-open. 라이브는 절대 멈추지 않음.

## 6. 트러블슈팅
- Telegram 미표시: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env, `--telegram` 플래그 확인.
- verdict 0건: `data/refined_signals.json`에 신호가 있는지(장중·전략 활성), writer 로그 확인.
- claude-cli 실패: `command -v claude`, `ANTHROPIC_API_KEY` — 실패해도 해당 종목 verdict 없음 → 데몬 fail-open(안전).
- 게이트가 안 먹힘: `policy.json`의 `agent_advisory_enabled`가 true인지, verdict ts가 TTL 내인지.
