---
name: barro-trade-review
description: BarroAiTrade 일일 매매복기(trade review) 정식 워크플로 — EOD 아카이브 임포트 → 데이터 무결성 검증(verify_eod_data) → 기존 KPI 도구(scripts/_daily_strategy_audit.py --source auto, 브로커 실측 fill_audit 우선) → 손실 drill-down → 서술형 md/html 리포트(reports/<date>/) → 권고(우선순위) 제안 및 안전한 코드개선 구현(HITL 게이트). 손으로 ledger 를 새로 짜지 말고 반드시 _daily_strategy_audit.py 를 진실원천으로 쓴다. 사용자가 "<날짜> 매매복기", "매매복기 해줘", "매매 복기", "trade review", "캔들/매매로그 업데이트하고 복기", "EOD 복기", "오늘 복기", "X일 복기 + 리포트", "권고 적용/구현" 같은 표현을 쓰거나 ohlcv_cache_*.tar.gz + barroaitrade_trade_data_*.tar.gz 아카이브를 주며 복기를 요청할 때 트리거.
---

# BarroAiTrade 매매복기 스킬

하루치 거래를 **브로커 실측 기준**으로 복기하고, 서술형 리포트를 산출한 뒤, 권고(우선순위)를 제안하고 **안전한 개선은 구현**한다. 핵심 원칙 2가지:

1. **진실원천은 기존 도구.** 전략별 실현손익/승률은 손으로 ledger 를 짜지 말고 `scripts/_daily_strategy_audit.py --source auto` 가 산출한 수치를 그대로 쓴다(과거 손작업이 이 도구와 완전 일치함을 검증함, 2026-06-16). 서술형 리포트는 그 위에 얹는 해설일 뿐 숫자를 재계산하지 않는다.
2. **실거래 파라미터는 HITL.** 권고 중 매매 동작을 바꾸는 변경(임계·청산로직·전략가중)은 사용자 승인(AskUserQuestion) 전에 default 를 바꾸지 않는다. 관측성·자가검증·config-gated default-OFF 같은 **실거래 무영향** 개선만 자동 구현한다. (`references/recommendation-policy.md`)

작업 루트: `/Users/beye/workspace/BarroAiTrade` (데이터·스크립트는 메인 레포). 날짜 인자 = 복기 대상 영업일 `YYYY-MM-DD`.
PY 선택: `PY="./.venv/bin/python"; [ -x "$PY" ] || PY="./venv/bin/python"; [ -x "$PY" ] || PY=python3`

---

## Phase 0 — 임포트 (아카이브가 주어지면)

3종 아카이브를 받으면 먼저 백업 후 추출한다(아카이브 없이 "복기만" 요청이면 건너뛴다).

```bash
cd /Users/beye/workspace/BarroAiTrade
D=2026-06-16            # 대상일
TS=$(date +%Y%m%d_%H%M%S); BK="data/_backup_pre_${D//-/}_$TS"; mkdir -p "$BK"
for f in active_positions.json balance_history.json barro_trade.db order_audit.csv \
         fill_audit.csv buy_audit.csv refined_signals.json simulation_log.csv policy.json; do
  [ -f "data/$f" ] && cp -p "data/$f" "$BK/"; done
tar xzf ~/Downloads/ohlcv_cache_${D}.tar.gz    -C data/    # 일봉 → data/ohlcv_cache/
tar xzf ~/Downloads/ohlcv_cache_5m_${D}.tar.gz -C data/    # 5m   → data/ohlcv_cache_5m/
tar xzf ~/Downloads/barroaitrade_trade_data_${D}.tar.gz -C .  # → data/ (order/fill/buy_audit, balance, active_positions...)
```

검증: `data/ohlcv_cache/meta.json` 의 `updated == D`, `005930.json` 마지막 일봉/5m 이 D 인지. data/ 는 gitignore(커밋 대상 아님).

## Phase 1 — 데이터 무결성 검증 (verify_eod_data)

```bash
bash scripts/verify_eod_data.sh "$D"; echo "exit=$?"
```

- **PASS (exit 0)** → fill_audit·EOD balance·buy_audit 완비 → **브로커 실측 복기** 가능.
- **NG (exit>0)** → 이브닝 파이프라인 침묵(6/9~6/15 회귀 사례). 리포트 최상단에 **데이터 완전성 경고**를 싣고, 손익은 추정임을 명시. 권고에 "이브닝 파이프라인 점검 + ka10073 재수집(D+2 내)"을 P0 로 올린다.

## Phase 2 — KPI 산출 (진실원천 도구)

```bash
$PY scripts/_daily_strategy_audit.py --date "$D" --source auto --save
# → reports/strategy_audit_<D>.json : per_strategy(realized/wins/sells/syms), per_symbol,
#   carry(이월), gap_records, total_realized, alarms. §C 청산품질·sim-live 괴리도 stdout.
```

- `--source auto`: fill_audit 있으면 실측, 없으면 1분봉 추정(자동). NG 인 날은 추정으로 떨어진다.
- **개발머신엔 키움 네이티브 토큰이 없어 §B 진입품질용 캔들 fetch 가 인증실패(rc=3)** 한다. 이때 §A/§C 실측은 정상. 진입 갭·일중위치·run-up 이 필요하면 **네이버 fchart 1분봉**으로 보완한다(`references/operational-facts.md` 의 fetch 스니펫). 네이버는 인증 불필요.
- 이 JSON 의 `per_strategy.realized` 가 전략별 실현손익의 **진실원천**. 리포트는 이 값을 그대로 인용한다.

## Phase 3 — 손실 drill-down

`per_symbol` 에서 손실 큰 종목 1~3개를 골라:

```bash
$PY scripts/_loss_drill_down.py --symbol <SYM> --date "$D"
```

⚠️ **한계(2026-06-16 확인)**: `_loss_drill_down.py` 는 `_daily_evening_pipeline.py` 가 남긴
`analysis/imports/<date>/executions.json` + 1분봉 ohlcv_dir 에 의존한다. **직접 tar 임포트
(data/ 에 바로 푼) 방식에선 동작하지 않는다**(import dir·executions.json 부재). 이 경우
drill-down 은 건너뛰고, 대신 **Phase 2 JSON 의 `per_symbol.realized` + 네이버 1분봉**
(`references/operational-facts.md` fetch)으로 손실 종목의 진입갭·일중위치·청산 타이밍을
직접 해설한다. (evening-pipeline zip 경로로 받은 날은 정상 동작.)

손실 해설엔 **`barrotrade-self-reflector` 렌즈**를 적용한다(에이전트 가동 시 위임, 아니면 수동):
"이 손실에서 무시된 리스크 신호는? 다시 보이면 진입 금지할 오판 패턴은?" (예: 고갭 추격·비싼 진입가 위치). Phase 5 권고의 근거로 연결.

## Phase 4 — 서술형 리포트 (reports/<date>/)

`references/report-template.md` 구조로 `reports/<D>/<D>_매매복기.md` + `.html` 작성. 데이터 출처:
- 전략별/합계/시간대/알람 → Phase 2 JSON (`_daily_strategy_audit.py`). **재계산 금지, 인용.**
- 진입 갭·run-up·EOD 보유 미실현 → fill_audit buy_price·active_positions·buy_audit·일봉캐시(+필요시 네이버 1분봉).
- 이월(carry) 효과 → JSON `carry` + order_audit 의 매수 없는 매도(전일 보유분 청산).

**검증 필수**: 리포트의 전략별 합계가 JSON `total_realized` 와 일치하는지 대조 후 산출. md→html 변환기는 `references/report-template.md` 참조.

## Phase 5 — 권고 제안 및 구현 (HITL 게이트)

리포트의 "권고(우선순위)"를 도출한 뒤 `references/recommendation-policy.md` 분류에 따라:

- **(a) 운영/데이터** (배포·요율협의·파이프라인 재실행) → 코드 아님. 사용자 액션으로 명시.
- **(b) 이미 구현됨** (갭가드 `_ZONE_MAX_FLU`·재시도 `retry_sell_only`·비용반영) → 신규코드 아님. 배포/ env 토글 안내.
- **(c) 안전 개선** (관측성·자가검증·config-gated default-OFF, 실거래 무영향) → **즉시 구현 + 테스트 + 커밋(푸시는 승인)**.
- **(d) 실거래 파라미터** (갭임계·청산로직·전략가중) → **AskUserQuestion 으로 값/방향 확정 후**, 가능하면 **`barrotrade-code-surgeon` 에이전트에 위임**(AST 검증 + PolicyConfig 타깃 + HITL 강제 패치), 불가 시 직접 config-gated 구현(기본값 현행 유지). 데이터 부족하면 "측정 후 결정" 권고.

### 전문가 에이전트 위임 (있으면 활용, 없으면 폴백)

복기 권고를 손으로만 처리하지 말고 등록된 `barrotrade-*` 전문가 에이전트를 활용한다(Agent tool, `subagent_type`). 단 이 에이전트들은 `workspace/_intraday/`·`workspace/_memory/` 레이아웃을 가정하는데 **이 머신엔 그 워크스페이스가 없을 수 있다** → 그때는 권고 본문을 에이전트 프롬프트에 직접 실어 보내고(브리지), 실패하면 폴백한다.

- **(d) 승인 후 파라미터 패치 → `barrotrade-code-surgeon`**: 승인된 권고를 recap §5 형식(종목/필드/현재값→제안값/근거)으로 프롬프트에 실어 Agent 호출. 산출물은 PolicyConfig(우선) 또는 strategy dataclass의 **숫자 default 만** 바꾸는 unified diff + proposal.md. **직접 git apply 안 함 — HITL 결재 후** 적용. AST 검증·결정성(temp 0.1) 내장이라 손 편집보다 안전. 워크스페이스/에이전트 불가 시 → 직접 config-gated 구현으로 폴백.
- **Phase 3 손실 관점 → `barrotrade-self-reflector`(렌즈)**: 손절·고갭 손실 종목에 대해 "Bear가 경고했으나 묵살된 항목 / 다시 보이면 진입 금지할 오판 패턴"을 추출. 에이전트 미가동 시 그 **렌즈만 수동 적용**(어떤 리스크 신호를 무시했는가)하여 리포트 손실 해설에 반영.
- **대체 recap 경로 → `barrotrade-intraday-reporter`**: `workspace/_intraday/<date>/`(signals/executions/pnl/incidents)가 실재하는 라이브-캡처 환경에서는 이 에이전트가 recap+§5 권고를 직접 작성. EOD-zip 임포트 흐름(본 스킬)과 입력이 다르므로 그 경우만 사용.

분류·옵션 제시·에이전트 위임·구현 절차는 `references/recommendation-policy.md` 를 따른다. 옵션 표기는 그리스/일본어 문자 금지(1/2/3 또는 A/B/C).

---

## 산출물 체크리스트
- [ ] `data/_backup_pre_<date>_*` 백업 (임포트 시)
- [ ] `verify_eod_data.sh <date>` 결과 (PASS/NG) 리포트 반영
- [ ] `reports/strategy_audit_<date>.json` (KPI 진실원천)
- [ ] `reports/<date>/<date>_매매복기.md` + `.html`
- [ ] 권고 분류 + (c) 안전개선 구현/(d) HITL 제안
- [ ] MEMORY 갱신(당일 손익 요약·반복패턴·미배포/파이프라인 상태)

## 흔한 함정
- 손으로 ledger 재계산 → 도구와 어긋남. **금지.** `_daily_strategy_audit.py` 가 진실원천.
- NG 인 날을 실측처럼 서술 → 반드시 "추정" 명시 + 데이터 경고.
- (d) 실거래 파라미터를 승인 없이 default 변경 → 금지(HITL). 신규 거래동작은 config-gated default-OFF.
- 신규상장주(일봉캐시 없음, 예 코스모로보틱스 439960) → 갭계산 skip, `update_ohlcv_cache.py` 편입점검 권고.
- 키움 캔들 인증실패는 개발머신 정상상태 → 네이버 fchart 로 보완(실측 §A 와 무관).
