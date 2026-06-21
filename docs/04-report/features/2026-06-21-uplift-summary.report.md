---
tags: [report, summary, strategy-uplift, status/final]
---

# BarroAiTrade 전략 고도화 — 적용 요약 (1페이지)

> **2026-06-21** · main `8cbf83a` 푸시 완료 · 전체 **1496 테스트 통과**

## 1. 적용된 변경 (한눈에)

| 변경 | 영역 | 무엇 | 라이브 상태 |
|------|------|------|------------|
| **trap_guard** | 진입 | 가짜돌파/개미꼬시기 차단(과확장·윗꼬리·고갭 ATR) | ⚪ default-OFF |
| **regime_exit** | 청산 | 국면 적응(SIDEWAYS SL −4→−3, BULL TP 확장) | ⚪ default-OFF |
| **net-aware TP** | 청산 | TP/분할익절에 왕복비용 가산(net 익절) | ⚪ default-OFF |
| **trap SHADOW** | 진입 | 차단 안 하고 "차단했을 것"만 로깅(측정용) | ⚪ default-OFF |
| **COMMISSION_RATE 0.00175→0.0035** | 비용모델 | 실측 2배 과소 정정 | 🔴 **적용됨(라이브)** |
| 측정도구 2개 + 리포트 3종 | 관측성 | 신호결정 audit·TP/SL net진단 | 🟢 적용 |

> 🔴 **라이브에 실제로 영향을 주는 변경은 비용율 정정 1개뿐.** 나머지는 전부 default-OFF(켜야 작동).

## 2. 비용율 정정의 의미 (유일한 라이브 변경)

`COMMISSION_RATE` 0.00175 → **0.0035** (fill_audit **298행** 재도출로 확정: 편도 0.3497%, 종전은 2배 과소).
→ ops `git pull` 시 **시뮬/선정이 비용을 정확히(net) 반영** → 한계셋업 과매매 감소(보수적 선정). 우대요율 협의 시 `BARRO_COMMISSION_RATE`로 하향.

## 3. 핵심 측정 발견

- **신호**: 6/19 트랩 후보 13건 차단 → 고점 추격 시 EOD까지 평균 **−5.5%**(차단 정당). 단 09:30 추격 +7.4%(무료 아님 → 측정 필요).
- **TP/SL**: 임계 전부 gross → **gold 분할익절 +2%는 비용이 45% 잠식**, SL net −4.9%.
- **6/16 실현**: +13,862원(전적으로 이월 supertrend 덕, sf 대우건설 고갭 −181K).

## 4. 활성화 런북 (ops, 원할 때만 · 코드 default 는 OFF 유지)

```bash
# ① trap SHADOW 측정 (1~2주, 미차단) — .env.local
BARRO_TRAP_UPPER_WICK_MAX=1.0  BARRO_TRAP_OVER_EXT_K_ATR=2.5
BARRO_TRAP_GAP_ATR_MULT=3.0  BARRO_TRAP_GAP_ABS_MAX_PCT=15.0  BARRO_TRAP_SHADOW=1
# ② regime_exit / ③ net-aware — data/policy.json
{ "regime_exit_enabled": true, "regime_sideways_sl_mult": 0.75, "net_aware_tp_enabled": true }
```

## 5. 잔여 액션 (사용자/ops)

1. **수수료 요율 협의** — net 잠식 구조적 1순위 레버(코드 아님).
2. **trap/regime SHADOW 측정 후 enforce/활성화 결정**(휩쏘 양날).
3. 운영 머신 `git pull origin main`(비용율 정정 반영, 나머지 OFF 유지).

## 6. 산출물

- 리포트: `2026-06-21-tp-sl-exit-logic`(TP/SL 감사) · `2026-06-21-0619-trap-guard-simulation`(6/19 시뮬) · `2026-06-21-strategy-uplift-signal-tpsl-check`(고도화) — 모두 `docs/04-report/features/`.
- 측정도구: `scripts/_signal_decision_audit.py` · `scripts/_tpsl_zone_diagnostic.py`.
- git: main `8cbf83a`(증분1 `e6fadd5` + 증분2). feat `feat/thetrading-uplift-increment1` 동기화.
