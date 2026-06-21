---
tags: [report, feature/dante-uplift, channel/주식단테, summary, status/done]
---

# 주식단테 접목 트랙 — 종합 요약 (1-stop)

> **이 문서**: 주식단테(@주식단테) 방법론 → BarroAiTrade 접목 이니셔티브 전체(분석→설계→구현→검증→(d)게이트)를 하나로 요약. 세부는 각 산출물 링크.
> **연관 트랙**: [[2026-06-21-uplift-summary.report|더트레이딩 접목 요약]] (BarroAiTrade 전략의 원천 방법론)
>
> **Summary**: 주식단테 공개자막 1,010편을 채굴해 정량 기법(JD-R1~25)을 추출, 현 시스템(=더트레이딩 코드화)에 *부재한* 부분만 전이. 핵심 발견 = 주식단테는 **장기 112/224/448선 기반 바닥·반전 셋업**으로 현 시스템(단기 5·20·60 돌파후 눌림)과 **직교적 보완**. 백테스트·OOS 결과 **distribution(세력이탈 장대음봉)만 진짜 (d) 적격 엣지**로 확정·config-gated 구현(default-OFF), sr_flip(공구리)은 레짐 결합 후에도 베타라 진입 비권고, 나머지는 inert/거부. **현재까지 라이브 매매 동작 변경 0**(전부 default-OFF·관측, 회귀 1556 passed).
>
> **Date**: 2026-06-22 · **Status**: 트랙 1차 완료. 잔여 = distribution dry-run(트레이딩 머신·사용자).

---

## 1. 트랙 한눈에 (PR #186 → #190)

| 단계 | PR | 머지 | 산출 | 거버넌스 |
|------|----|----|------|---------|
| 분석·설계 | #186 | b59bc3c | [[../../03-analysis/2026-06-21-dante-methodology-extract|추출]] + [[../../02-design/features/2026-06-21-dante-uplift.design|설계]] (JD-R1~25) | 문서 |
| (c) 구현+shadow | #187 | 2e61fca | [[2026-06-21-dante-uplift.report|dante_filters + 백테스트]] (inert) | (c) 무영향 |
| OOS 검증 | #188 | c37ee25 | [[2026-06-22-dante-oos-validation.report|3축 OOS]] | 관측 |
| distribution (d) | #189 | 1cbc35d | [[2026-06-22-dante-distribution-exit.report|청산 게이트]] (config-gated) | (d) default-OFF |
| sr_flip 레짐 재검증 | #190 | 28c21db | [[2026-06-22-dante-srflip-regime-oos.report|레짐 결합 OOS]] | 관측 |

**라이브 영향 누계 = 0** (코드 default 전부 OFF, 회귀 1556 passed, byte-identical).

## 2. 방법론 핵심 (왜 주식단테인가)

BarroAiTrade 전략(F존/골드존/SF존/supertrend)은 이미 *더트레이딩* 채널의 코드화. 주식단테는 별개 채널이며 **방향이 반대**:

| 축 | 현 시스템(더트레이딩) | 주식단테(신규) |
|----|----------------------|----------------|
| 기준선 | 단기 5·20·60 | **장기 112·224·448** |
| 셋업 | 상승추세 돌파 후 눌림(연속) | 역배열 바닥 장기선 재돌파(반전) |
| 시간축 | 당일~수일 | 수주~수개월 |

→ **경쟁 아닌 보완.** 채굴(8 병렬 에이전트, 코퍼스 14.2M자)로 JD-R1~25 + 메타원칙 JD-P1~7 추출. 신규성 검증(grep): 224/112/448 레짐·S-R flip·distribution 청산·수급게이트·R:R 게이트 = **현 시스템 부재 확인**. 눌림목/신고가/유동성/과열/이격은 중복(=교차검증). 비공개 지표(수박지표·파란점선)·재량 레이블·세력 서사는 전이 불가. **농사매매=마틴게일 거부**.

## 3. 증거 — 백테스트·OOS 종합 (일봉 ~2,900종목, 비용 0.90% 차감)

| 신호 | shadow(H20 net) | OOS 판정 | 결론 |
|------|-----------------|----------|------|
| **distribution(JD-R13)** | 회피: baseline 하회·음수 | **견고 PASS** (IS/OOS/종목 하회, 임계 단조, 5/7분기) | **✅ (d) 적격 — 유일** |
| sr_flip 공구리(JD-R7) | +1.796%(목표도달 72.8%) | 조건부→레짐결합 후도 **regimeΔ≈0** | ⚠️ 롱-베타, **진입 비권고** |
| odori 5/15(JD-R20) | net 음수 | — | ❌ 단독 비용잠식 |
| saucer 밥그릇(JD-R5) | 희소·부진 | — | ⚠️ 미검증 |
| 레짐 224위/아래(JD-R1) | +0.22%p 약우위 | — | ~ 약함(불장 표본) |

- **distribution**: 임계 강화할수록 회피효과 단조 증가(vol 2.5→3.5: +0.08→−0.23%) = 시장베타 아닌 상대-약세 예측력.
- **sr_flip**: 레짐 필터(above_ma224·정배열)가 절대 net·약세분기 방어는 개선하나 regime-matched baseline 대비 순수 알파 ≈0 → "상승추세 종목 베타 수확"이지 패턴 알파 아님.
- **공통 한계**: 평가구간 ~2024-10~2026-06(224 warmup+불장) → **진정한 약세장 OOS 불가**. 모든 라이브 활성 전 dry-run 필수.

## 4. 구현·거버넌스 현황

| 분류 | 항목 | 상태 |
|------|------|------|
| **(c) inert** | `dante_filters.py`(ma_alignment·above_ma224·sr_flip·saucer·accumulation·distribution·odori·rr_ratio) | 머지·호출처 없음(스캐너 미참조) |
| **(d) config-gated default-OFF** | distribution 청산 게이트 — `holding_evaluator`+`policy_config`+daemon 배선, 전량청산·거래량3.0배·몸통3% | 머지(#189). **enabled=False → 라이브 무변경** |
| **관측/연구** | sr_flip + 레짐 변형 | inert 유지(진입 비승격) |
| **거부** | 농사매매(마틴게일), 비공개 지표 | 도입 안 함 |

- 활성화(`distribution_exit_enabled=True`)는 **약세장 dry-run 후 별도 HITL** — 아직 안 켬.
- 롤백: 모든 게이트 flag False 즉시 무력화.

## 5. 산출물 인덱스
- 분석: `docs/03-analysis/2026-06-21-dante-methodology-extract.md`
- 설계: `docs/02-design/features/2026-06-21-dante-uplift.design.md`
- 리포트: `2026-06-21-dante-uplift`(c구현) · `2026-06-22-dante-oos-validation` · `2026-06-22-dante-distribution-exit`(d) · `2026-06-22-dante-srflip-regime-oos`
- 코드: `backend/core/strategy/dante_filters.py`(+`DistributionExitConfig`), `backend/core/risk/holding_evaluator.py`(SellSignal.DISTRIBUTION), `backend/core/journal/policy_config.py`(distribution_exit_*), `scripts/intraday_buy_daemon.py`(게이트 배선)
- 검증 스크립트: `scripts/backtest_dante_filters_shadow.py` · `backtest_dante_oos_validation.py` · `backtest_dante_srflip_regime_oos.py`
- 테스트: `backend/tests/strategy/test_dante_filters.py` · `test_distribution_exit.py`

## 6. 남은 단계
1. **distribution dry-run** (★사용자, 트레이딩 머신) — `policy.json`에 `distribution_exit_enabled:true` + `--dry-run` 데몬으로 1~2주 관찰(발동빈도·whipsaw·약세세션). 패치 스크립트 전달 완료.
2. dry-run 통과 → **라이브 활성**(`--no-dry-run`, 최종 HITL) → `barro-trade-review`로 효과 측정.
3. (후보) 약세장 데이터 확보 시: distribution·정배열 sr_flip 재OOS. 지수 레짐(macro `market_regime`) 결합은 미탐색 축.

## 7. 한계 (트랙 전체)
- 표본=공개자막(멤버십 미관측, 미끼층 편향) + 불장 OOS 편향 + 일봉 측정(장중 슬리피지 미반영).
- sr_flip win<50%(비대칭) — 자본곡선 변동성/MDD는 평균기준 밖.
- 모든 임계 단일 그리드(±1스텝 민감도로 보완, walk-forward 미수행 — 데이터 길이 한계).
