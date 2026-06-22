# 시장-맥락 Add-on 프레임워크 — advisory 확장 (as-built)

> 생성: 2026-06-23 · 상태: 코어 + 배선 구현(라이브 무영향) · 활성/하드 승격은 HITL
> 관련: [[2026-06-22-agent-advisory-realtime.design]](Phase1~2), [[recommendation-policy]](거버넌스)

## 1. 목적

advisory 레이어(종목 GO/WAIT/NO-GO)를 **시장 전반 판단**으로 확장 — ① 실시간 시장국면 ② 오늘 거래대금 집중 테마 ③ 포트폴리오 테마 쏠림 ④ 포트폴리오 리스크. **"애드온 형태"**(레지스트리). LLM이 주문에 더 기여하되 거버넌스 유지.

## 2. 불변 원칙
1. **LLM 주문 동기경로 금지** — 데몬은 미리 계산된 `advisory.json`만 읽음. 섹션 부재/stale/미매핑 → **fail-open**.
2. **결정적 하드캡 + LLM 소프트** — 테마 쏠림/집중은 결정적 계산이 하드 한도, LLM(국면·테마 판단)은 사이징/우선순위 소프트.
3. **config-gated default-OFF** — 전 add-on off → 데몬 byte-identical. 활성/하드 승격은 (d) HITL. 단계: off→soft→hard.

## 3. 아키텍처 (as-built)

```
[데몬, 결정적]  _save_market_snapshot → data/market_snapshot.json
                  {ts, regime, leaders:[{symbol,trade_value}], positions:[{symbol,eval_value}]}
                        │ (LLM 없음, 관측 덤프)
                        ▼
[writer]  produce_market_sections(snapshot, theme_map)  [+ 후속 LLM 오버레이]
                        ▼
        data/advisory.json  { verdicts:[...](Phase1~2),
                              market_context:{regime,risk_on,confidence,strategy_gates,...},
                              sector_themes:{hot:[{theme,turnover,turnover_pct,rank,symbols}]},
                              portfolio_signals:{theme_exposure:{theme:pct},concentration_pct,leverage_warn} }
                        │
                        ▼
[데몬, config-gated default-OFF]  _scan_and_buy 매수필터 직후 apply_* + 사이징 배수
```

- **결정적 집계**: `backend/core/risk/theme_map.py` — `hot_themes`(거래대금×theme_map), `theme_exposure`(포지션×theme_map). 종목→테마는 커밋 `data/theme_map.json`(유지보수).
- **소비**: `backend/core/risk/market_context.py` — `load_market_advisory`(fail-open) + `apply_market_context`/`apply_theme_guard`/`apply_portfolio_risk`(off→무변경).

## 4. 4개 Add-on

| add-on | 결정적 | 효과(데몬 hook) | config(policy.json) |
|---|---|---|---|
| market_context | snapshot.regime | risk-off/bearish → `regime_max_buy` 축소(soft) / 전략게이트(hard) | `market_context_enabled`·`_mode`·`_ttl_sec` |
| sector_themes | hot_themes(거래대금) | 핫테마 표시·우선(soft, 후속 priority 배선) | `sector_themes_enabled`·`_mode` |
| portfolio_theme_guard | theme_exposure ≥ cap | 과다 테마 매수 **차단(hard)/사이징 축소(soft)** | `portfolio_theme_enabled`·`_mode`·`portfolio_max_theme_pct`(0.30)·`_soft_factor`(0.5) |
| portfolio_risk | concentration/leverage | 전역 **사이징 throttle** | `portfolio_risk_enabled`·`_mode`·`portfolio_max_concentration_pct`(0.40)·`_throttle`(0.5) |

사이징 배수 = `theme soft_factor × portfolio throttle`(미설정 1.0 → `round(qty×0.6)` 무변경).

## 5. 변경 파일
- 신규: `backend/core/risk/theme_map.py`·`market_context.py`, `data/theme_map.json`(+.gitignore 예외).
- 수정: `intraday_buy_daemon.py`(snapshot 덤프 + 소비 훅 config-gated + 사이징 배수), `agent_advisory_writer.py`(produce_market_sections + advisory 섹션 merge), `policy_config.py`(add-on 필드 default-OFF).
- 테스트: `test_market_context.py`(19), `test_market_context_producer.py`(4).

## 6. 단계적 롤아웃
- **A 표시/생산(라이브 무영향)**: 데몬 snapshot + writer 섹션 생산 + 데몬 소비 훅(default-OFF). `git pull`로 코드 반영, 동작 inert.
- **B shadow**: add-on 효과(테마캡/regime가 게이트·축소했을 매매 vs 실제) net-of-cost 측정.
- **C 활성(HITL)**: 결정적 하드캡(테마/집중) 우선 hard → regime/사이징 soft → LLM 하드(입증 후). 전부 policy.json, 롤백=off 즉시.

## 7. 검증
- 단위 23건 + 전체 회귀 그린. off → 데몬 byte-identical(사이징 ×1.0, regime_max_buy 무변경, 섹션 부재 fail-open).
- 생산자→소비자 라운드트립(테마 100% → hard 차단) 통과.

## 8. 한계 / 후속
- `theme_map.json` 커버리지 = 가드 정확도(미매핑 fail-open). LLM 오버레이(국면 내러티브·테마 진위), sector priority 배선, leverage 판정(balance), Telegram 표시, shadow add-on 확장은 후속 증분.
- 거래대금 스냅샷=사이클 단위 근사. 모델/런타임 ≠ alpha — 결정적 가드가 1차 안전망.
