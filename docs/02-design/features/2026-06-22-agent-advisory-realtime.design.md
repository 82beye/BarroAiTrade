# 전문가 에이전트 판단 실시간 반영 — Advisory 레이어 설계

> 생성: 2026-06-22 · 상태: Phase 1~2 코드 구현(라이브 무영향) · 활성(Phase 3)은 HITL
> 관련: [[recommendation-policy]] 거버넌스(a/b/c/d), [[2026-06-22-dante-distribution-exit]] (동일 config-gated 선례)

## 1. 목적 / 배경

라이브 트레이딩(규칙 기반 데몬)에 `barrotrade-*` 전문가 에이전트(첫 대상 **quick-decider**)의 판단을 **실시간 반영**한다. 단, LLM의 비결정성·지연·신생도 리스크와 (d) 거버넌스 때문에 **단계적(표시→shadow→게이트)** + **config-gated default-OFF**로 도입한다.

런타임은 **Hermes Agent**(self-hosted, cron, 병렬 sub-agent, 멀티채널 게이트웨이, pluggable LLM, 영속 메모리)를 *선택적* 스케줄러/게이트웨이로 쓴다 — 단, 핵심 생산자(`agent_advisory_writer.py`)는 **레포 내 독립 파이썬**이라 Hermes 없이 cron/launchd로도 가동된다(신생도 리스크 격리 + "git pull → 바로 사용").

## 2. 불변 원칙 (거버넌스)

1. **LLM은 주문 동기 경로 금지.** 데몬은 미리 계산된 `data/advisory.json`만 TTL로 읽는다. 없음/stale/저신뢰/파싱실패 → **fail-open**(베이스라인대로 매매).
2. **config-gated default-OFF.** 데몬 게이트는 `policy.json: agent_advisory_enabled`(기본 false) — OFF면 byte-identical. 활성은 (d) **HITL**, 롤백은 플래그 false 즉시.
3. **표시→shadow→게이트** 순서로만 승격. 라이브 주문 영향은 shadow가 net-of-cost 개선을 입증한 뒤에만.

## 3. 아키텍처

```
[라이브 데몬]  --refined_signals.json(탐지 신호)-->  [writer(생산자, 독립 프로세스)]
                                                       │ quick-decider (claude-cli | mock)
                                                       ▼
                              data/advisory.json {verdicts:[{symbol,action,confidence,reason,ts,strategy}]}
                              logs/decisions/<date>.jsonl (감사)  + Telegram(실시간 표시)
[라이브 데몬]  <--매 사이클 폴링(config-gated default-OFF)--  data/advisory.json
```

- **타이밍 비결합**: writer가 종목별 최신 verdict를 연속 생산. 데몬은 다음 사이클(≈60s)에 TTL 신선한 verdict만 매수 게이트에 사용. verdict 없는 신규 신호 → fail-open.
- **Hermes 위치**: writer를 스케줄(cron)·표시(게이트웨이)·LLM 백엔드(로컬 vLLM/OpenRouter/커스텀=Claude)로 감싸는 *선택* 레이어. writer 자체는 Hermes 비의존.

## 4. 구성요소 / 계약

| 구성요소 | 파일 | 역할 |
|---|---|---|
| 소비자(게이트) | `backend/core/risk/agent_advisory.py` | `AgentAdvisoryConfig.from_policy_config`·`load_advisory`(fail-open)·`apply_buy_advisory`. enabled=False→무변경 |
| policy 플래그 | `backend/core/journal/policy_config.py` | `agent_advisory_enabled=False`(+ttl/block_wait/min_confidence) |
| 데몬 훅 | `scripts/intraday_buy_daemon.py` | 매수 신호 필터(`_scan_and_buy`, config-gated). enabled 시에만 적용 |
| 생산자(writer) | `scripts/agent_advisory_writer.py` | refined_signals → verdict(claude-cli/mock) → advisory.json + decisions.jsonl + Telegram |
| 부트스트랩 | `scripts/setup_agent_advisory.sh` | 멱등·비파괴: 디렉터리·의존성·env 점검 + mock 스모크 + 절차 안내 |
| 스케줄 템플릿 | `infra/com.barro.advisory-writer.plist.example` | launchd 등록 예시(경로 수정 후 load) |

**advisory.json 스키마**: `{"updated_at": ISO, "verdicts": [{symbol, action(GO|WAIT|NO-GO), confidence(0~1), reason, ts(ISO UTC), strategy}]}`. 소비자는 symbol당 마지막 항목 우선.

## 5. 단계별 로드맵

- **Phase 1 — 표시**(라이브 무영향): writer 루프 가동 → verdict를 Telegram + decisions.jsonl. 데몬 게이트 OFF. `order_audit.csv` baseline diff=0.
- **Phase 2 — shadow**(라이브 무영향): advisory.json 축적. "데몬 실제 매매 vs advisory가 게이트했을 매매"를 ≥1~2주 비교(진실원천 `_daily_strategy_audit.py`). 데몬 리더는 이미 코드에 있음(default-OFF).
- **Phase 3 — 게이트**(★HITL): shadow 개선 입증 시 `policy.json: agent_advisory_enabled=true`. 시작은 매수 신호 필터(NO-GO defer), 이후 사이징·청산 확장.

## 6. 검증

- 소비자/생산자 단위 + 라운드트립: `make test-advisory`(23건). default-OFF byte-identical·fail-open 5종·생산자→소비자 일치.
- 전체 회귀: `pytest backend/tests -q` 그린(advisory 추가 후 무영향).
- 라이브 무영향: enabled=false에서 `order_audit.csv` baseline 동일.

## 7. 한계 / 주의

- **모델/런타임 ≠ alpha.** shadow 측정 전 라이브 반영 금지.
- 선결: 단타(1m) 결정적 엣지가 왕복 0.9% 비용에서 흑자 미검증([[2026-06-22-strategy-deep-dive]] §4) — 상위 레버리지.
- Hermes 신생(2026-02) → Phase 0~2 shadow 파일럿. writer는 Hermes 비의존이라 cron/launchd 폴백 가능.
- dev 머신엔 Kiwoom 토큰 없음 → 실가동·게이트 활성은 운영 머신 + HITL.
