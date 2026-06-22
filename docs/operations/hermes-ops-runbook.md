# Hermes 운영 런북 — 설치 · 설정 · 일일 운영 프로세스 (운영 머신)

> 대상: Kiwoom 토큰 보유 **운영 머신**. 목적: advisory/market-context 파이프라인 + Hermes Agent를 `git pull` 후 1커맨드로 셋업하고 운영.
> 안전: 라이브 매매 게이트는 전부 **default-OFF**. 본 런북의 설치/표시는 **라이브 무영향**. 게이트 활성만 ★HITL.
> 관련: [[agent-advisory-runbook]](add-on 활성 상세) · [[2026-06-23-market-context-addons.design]] · [[recommendation-policy]](거버넌스)

## 0. 역할 분리 (왜 이렇게 나누나)

| 레이어 | 담당 | 가동 방식 |
|---|---|---|
| **결정적 critical 파이프라인** | 데몬(snapshot 덤프) + writer(verdict·market-context 생산·Telegram·LLM 오버레이) | **launchd**(검증됨, Hermes 버전 비의존) |
| **Hermes Agent** | LLM 프로바이더 + 멀티채널 게이트웨이(Telegram) + **대화형 ops 에이전트**(시장 브리핑·shadow 질의) + 영속 메모리 | `hermes gateway` |

→ critical 경로는 Hermes 장애와 무관하게 launchd로 돈다. Hermes는 "운영자가 채팅으로 시장을 묻고, 에이전트가 advisory를 읽어 답하는" 보조 레이어. **LLM은 어디서도 주문 동기경로에 없음**(데몬은 advisory.json만 읽고 fail-open).

## 1. 한 줄 요약
```bash
cd ~/workspace/BarroAiTrade && git pull origin main
bash scripts/setup_hermes_ops.sh                 # 점검 + 안내(설치 안 함)
bash scripts/setup_hermes_ops.sh --install-hermes --install-launchd --install-skill   # 실제 설치(opt-in)
```

## 2. 사전 준비
- venv(`./venv`), `claude` CLI(writer LLM 백엔드), git.
- 시크릿 env(커밋 금지) — 템플릿 `infra/hermes/ops.env.example`:
  - `ANTHROPIC_API_KEY`(writer claude-cli) · `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`(표시+게이트웨이) · (선택) `OPENROUTER_API_KEY`(Hermes 추론).
  - 채워 `~/.barro_ops.env` 로 저장 → 셸 프로필 `source` 또는 launchd `EnvironmentVariables` 주입.

## 3. 설치 (setup_hermes_ops.sh)
멱등·비파괴. 기본은 점검+안내, 실제 수행은 플래그 opt-in:
- `[1]` 사전 점검(venv/claude/env).
- `[2]` advisory 부트스트랩(`setup_agent_advisory.sh` — 디렉터리·theme_map·mock 스모크).
- `[3]` Hermes: 설치됨이면 `hermes doctor`. 미설치면 안내(또는 `--install-hermes` 로 `curl|bash`).
- `[4]` Hermes 스킬 `barro-market-brief` → `~/.hermes/skills/`(`--install-skill`).
- `[5]` writer launchd 등록(`--install-launchd`, 경로 자동 치환 + `launchctl load`).

## 4. Hermes 설정 (설치 후)
```bash
source ~/.zshrc                 # 설치 후 셸 재로딩
hermes doctor                   # 진단
hermes setup                    # LLM 프로바이더 마법사 (또는 hermes setup --portal = Nous Portal OAuth)
hermes model anthropic:<model>  # 또는 openrouter:<model> 등으로 모델 선택
hermes gateway setup            # Telegram 등 채널 페어링(봇 토큰)
hermes gateway start            # 게이트웨이 가동 → 텔레그램에서 봇에 말 걸기
```
- 스킬 가동 후 텔레그램에서 *"오늘 시장 브리핑"* → `barro-market-brief` 스킬이 advisory.json을 읽어 국면·핫테마·쏠림·verdict를 보고(read-only).
- ※ Hermes는 신생(2026-02)이라 `setup`/`gateway`/`config` 의 정확한 하위명령은 설치 버전에서 `hermes --help`로 확인. critical 파이프라인은 launchd라 Hermes 설정 차이와 무관.

## 5. advisory 파이프라인 (writer)
- launchd 등록(§3 `[5]`) 시 상시 가동: verdict + market-context 섹션 생산 + Telegram 표시.
- **LLM 시장국면 오버레이**까지: plist ProgramArguments 에 `<string>--market-llm</string>` 추가(또는 env `BARRO_MARKET_LLM=1`).
- 포그라운드 점검: `make advisory-writer`  /  `make advisory-writer ARGS=--market-llm`.

## 6. 일일 운영 프로세스
| 시점 | 동작 | 주체 |
|---|---|---|
| **장 시작 전(08:50)** | 데몬·writer launchd 가동 확인(`launchctl list | grep barro`). `git pull` 반영분 점검. | 운영자 |
| **장중(09:00~15:30)** | 데몬: snapshot 덤프 + (게이트 활성 시) add-on 적용. writer: 30s 루프 verdict/섹션 생산 + 텔레그램 표시. 운영자는 텔레그램으로 국면/핫테마/쏠림 확인, 필요 시 봇에 질의. | 자동 + 운영자 |
| **장 마감 후** | `barro-trade-review`(복기) → `_daily_strategy_audit.py --save` → `make advisory-shadow DATE=<오늘>`(게이트 반사실 효과). | 운영자/에이전트 |
| **주간** | shadow ≥1~2주 누적 → add-on 단계 승격(HITL) 판단. `theme_map.json` 신규 주도주 보강. | 운영자 |

## 7. HITL 활성 절차 (off → soft → hard)
모든 게이트는 `data/policy.json` 플래그(jq 로 키만, 통째 덮어쓰기 금지). 데몬은 매 사이클 재로딩.
```bash
# 종목 verdict 게이트
jq '. + {"agent_advisory_enabled": true}' data/policy.json | sponge data/policy.json
# 포트폴리오 테마 쏠림 — soft(사이징 축소) → 검증 후 hard(차단)
jq '. + {"portfolio_theme_enabled": true, "portfolio_theme_mode": "soft"}' data/policy.json | sponge data/policy.json
# 시장국면(risk-off→max_buy 축소) / 거래대금 집중 핫테마 우선 / 리스크 throttle
jq '. + {"market_context_enabled": true, "sector_themes_enabled": true, "portfolio_risk_enabled": true}' data/policy.json | sponge data/policy.json
```
승격 원칙: **결정적 하드캡(테마 쏠림·집중)** 먼저(저위험) → regime/사이징 soft → **LLM 신호 hard는 shadow 입증 후**. (`sponge` 없으면 `> /tmp/p && mv /tmp/p data/policy.json`.)

## 8. 모니터링
- 프로세스: `launchctl list | grep barro` · `tail -f logs/advisory-writer.log`.
- 산출물: `data/advisory.json`(updated_at 신선도) · `logs/decisions/<날짜>.jsonl`.
- Hermes: `hermes doctor` · 게이트웨이 로그.
- 효과: `reports/advisory_shadow_<날짜>.md`(improvement 부호).

## 9. 롤백 / 안전 불변식
- **즉시 롤백**: 해당 `*_enabled` → `false`(policy.json). 다음 사이클부터 데몬 byte-identical 복귀.
- writer/Hermes 다운 → advisory.json stale → 데몬 자동 **fail-open**(베이스라인 매매). 라이브는 멈추지 않음.
- LLM은 주문 동기경로에 **없음**. 모든 add-on off가 기본. 활성/하드 승격은 (d) HITL.
- writer launchd 중지: `launchctl unload ~/Library/LaunchAgents/com.barro.advisory-writer.plist`. Hermes 게이트웨이 중지: 게이트웨이 프로세스 종료. 어느 것도 데몬(매매)에 영향 없음.

## 10. 트러블슈팅
- writer verdict 0건: 장중·전략 활성·`refined_signals.json` 존재 확인.
- 텔레그램 미표시: `TELEGRAM_BOT_TOKEN`/`CHAT_ID` env + writer `--telegram`.
- market-llm 무반응: `command -v claude` + `ANTHROPIC_API_KEY` — 실패해도 결정적 base로 fail-open.
- 테마 가드 부정확: `data/theme_map.json` 커버리지 보강(미매핑 종목은 가드 미적용).
- Hermes 명령 상이: `hermes --help`로 설치 버전 확인. critical 경로(launchd)는 무관.
