---
tags: [operations, runbook, feature/dante-uplift, strategy/closing_bet]
---

# 배포 런북 — dante / distribution / 종베 (uplift 트랙)

> **연관**: [[../04-report/features/2026-06-22-dante-uplift-summary.report|dante 트랙 요약]] · [[../04-report/features/2026-06-22-dante-distribution-exit.report|distribution (d)]] · [[../04-report/features/2026-06-22-closing-bet-dryrun-disparity.report|종베 dry-run]] · [[strategy-restart-toggles|전략 재기동 토글]]
>
> 전제: **운영 머신(Kiwoom 토큰 보유)에서만** 실행. 추가 기능은 전부 **default-OFF** → pull·머지만으로 매매 동작 무변경. 활성은 *명시적 + dry-run 후 라이브(HITL)*. 대상 머지: PR #186~#193.

---

## 0) 현재 활성 전략 (기준선 — 변동 없음)
`sf_zone · f_zone · gold_zone`(1분 단타) + `swing_38`(일봉 스윙). 그 외(closing_bet·blue_line·crypto_breakout)=OFF, supertrend=opt-in(`--supertrend`).

## 1) 배포 — 코드 반영 (운영 머신)
```bash
cd ~/workspace/BarroAiTrade
git fetch origin && git log --oneline -1 origin/main      # 최신 머지 확인
git pull origin main
./venv/bin/python -m pytest backend/tests/ -q             # (선택) 회귀 그린
# 데몬 재기동 — 운영 기동 방식대로 (--dry-run 이 기본 ON)
```
검증: 활성 전략 4종 동일 / `distribution_exit_enabled`·`regime_exit_enabled`·`net_aware_tp_enabled`=False.

## 2) distribution 청산 게이트 (dry-run → 라이브)
```bash
# (A) dry-run 활성 — policy.json 키 추가(기존 값 보존). 거래량 3.0배·몸통 3% 표준.
cp data/policy.json data/policy.json.bak.$(date +%Y%m%d_%H%M%S)
jq '. + {distribution_exit_enabled:true, distribution_exit_vol_mult:3.0, distribution_exit_body_min:0.03}' \
   data/policy.json > data/policy.json.tmp && mv data/policy.json.tmp data/policy.json
python scripts/intraday_buy_daemon.py --interval 60        # --dry-run 기본 ON (주문 미체결)
#  → order_audit.csv 의 'DRY_RUN' + 사유 "distribution(세력이탈 장대음봉…" 1~2주 관찰
#     (발동빈도 · 수익포지션 조기청산(whipsaw) · 약세세션 거동)
# (B) 라이브 — 관찰 OK 후 (★HITL)
python scripts/intraday_buy_daemon.py --no-dry-run          # flag 유지 + 실주문
# 롤백: jq '.distribution_exit_enabled=false' … (또는 백업 복원) → 즉시 무력화
```

## 3) 종가베팅(closing_bet) — dry-run (이격도 게이트 ON)
```bash
# 정규장 15:00~15:20(KST). 실주문 0 (페이퍼/알림).
BARRO_CB_DISPARITY_YELLOW=1 python scripts/closing_bet_paper_scan.py --top 10        # → data/closing_bet_paper.csv
BARRO_CB_DISPARITY_YELLOW=1 python scripts/closing_bet_alert_daemon.py --mode loop --interval 60   # 텔레그램 알림
#  → 1~2주: 신호빈도(게이트로 감소)·익일 슈팅 적중·오버나잇 갭. 익일 결과= _daily_strategy_audit.
#  끄기: env 제거.   진입창=15:00~15:20, 청산=익일10:00 / D1~D3.
```
⚠️ **종베 자동매매 실편입은 미배선** — 데몬 EOD dispatch 블록 + `_CUTOFF_EXEMPT_STRATEGIES`+=closing_bet(14:30 cutoff 면제) + 오버나잇 carry 한도(설계 §6.5/6.6) 구현 후 `enabled_strategies={"closing_bet":True}` + 실주문. **dry-run 통과 후 별도 HITL.** (2026-06-18 '종베 수동관리 전용' 결정 변경)

## 4) 실행 안 함 (참고)
- `dante_filters`(공구리·매집봉·224레짐 등) = **inert(호출처 없음)**, 연구/관측용.
- `sr_flip` 진입 = OOS상 베타라 **비권고**(레짐 결합 후도 알파≈0). `swing_38` = OOS 5/5 PASS → 활성 유지.

## 5) 측정 · 롤백 · HITL
- **측정**: 활성 며칠 후 `/barro-trade-review <YYYY-MM-DD>` (브로커 실측 기준).
- **롤백**: 모든 게이트 flag/env OFF → 즉시 무력화. 코드 = `git revert`.
- **★HITL(자동화 금지)**: distribution 라이브(`--no-dry-run`) · 종베 자동매매 편입/활성 · 모든 실거래 파라미터 변경.

## 6) 주의
- **운영 머신 한정**(Kiwoom 토큰 필요). dev 머신은 토큰 부재로 데몬 기동 불가.
- `policy.json`은 머신별(값 다를 수 있음) → 통째 덮어쓰지 말고 `jq`로 키만 추가(위 §2).
- 모든 임계는 **불장 표본 OOS** 기반 → 약세장 dry-run 통과 전 실자본 증액 금지.
