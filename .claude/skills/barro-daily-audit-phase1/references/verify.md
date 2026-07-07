# 검증 / 수용 기준

## 1. Phase 1 단위·스모크 테스트

```bash
cd /Users/beye/workspace/BarroAiTrade/.claude/worktrees/strange-jackson-3c740a
pytest backend/tests/test_daily_pipeline.py -q
```

**목표: 14 passed**.

검증 항목(`04-test-daily-pipeline.py`):

| # | 테스트 | 대상 |
|---|---|---|
| 1 | `test_normalize_kt00009_buy_fill` | `normalize_execution` — `A` 접두 제거, side 추출 |
| 2 | `test_normalize_detects_sell` | side="sell" 검출 |
| 3 | `test_compute_net_includes_commission_and_tax` | `compute_net` — 수수료·세금 차감 계산 정확성 |
| 4 | `test_compute_net_loss_is_negative` | 손실 시 net < 0 |
| 5 | `test_aggregate_matches_buy_sell_and_marks_result` | `aggregate_by_symbol` — 매수·매도 짝짓기 |
| 6 | `test_aggregate_open_position_when_no_sell` | 매도 없을 때 result="open" |
| 7 | `test_attribute_tier1_active_positions` | 전략 귀속 Tier 1 (`ActivePositionStore`) |
| 8 | `test_attribute_tier2_logs_fallback` | Tier 2 (logs grep) |
| 9 | `test_attribute_tier3_sim_fallback` | Tier 3 (IntradaySimulator) |
| 10 | `test_attribute_unknown_when_all_fail` | 모두 실패 시 "unknown" |
| 11 | `test_update_ledger_replaces_same_date` | ledger idempotency — 동일 date 재실행 시 행 교체 |
| 12 | `test_perf_aggregate_groups_by_date_strategy` | `_strategy_perf_track.aggregate` — `(date, strategy)` 그룹화 |
| 13 | `test_run_pipeline_smoke` | end-to-end `run_pipeline` — fixture executions + active_positions.json → ledger 행 + executions.json 덤프 |
| 14 | `test_diagnose_handles_float_candles_and_detects_immediate_drop` | `_loss_drill_down.diagnose` — float/Decimal 회귀 방지 + "진입 직후 즉시 하락"·"매물대 미인식" 태그 |

## 2. 회귀

```bash
cd /Users/beye/workspace/BarroAiTrade/.claude/worktrees/strange-jackson-3c740a
pytest backend/tests/strategy/ backend/tests/risk/ -q
```

**목표: 영향 없음**. Phase 1 은 기존 코드를 변경하지 않으므로 통과 수 유지. 원격 세션 기준 154 passed / 5 skipped.

## 3. 커밋 범위 검증

`git diff --cached --name-only` 결과가 정확히 다음 6개여야 한다:

```
.gitignore
backend/tests/test_daily_pipeline.py
docs/04-report/features/2026-05-22-daily-pipeline.md
scripts/_daily_evening_pipeline.py
scripts/_loss_drill_down.py
scripts/_strategy_perf_track.py
```

이외에 `docs/.bkit-memory.json`, `docs/.pdca-status.json` 등이 stage 되면 즉시 `git restore --staged <file>` 로 제거.

## 4. 라이브 검증 (스킬 범위 외 — M4 에서 별도)

라이브 `kt00009` 호출은 키움 자격증명·네트워크 필요:

```bash
export KIWOOM_APP_KEY=...
export KIWOOM_APP_SECRET=...
export KIWOOM_ACCOUNT_NO=...
python scripts/_daily_evening_pipeline.py --zip ~/Downloads/BarroAiTrade_x.zip --date 2026-05-21
```

스킬은 여기까지 안 함 — 사용자가 M4 에서 별도 실행.

## 5. 푸시 후 확인

```bash
git fetch origin BAR-OPS-09
git log origin/BAR-OPS-09 --oneline -3
```

상위에 방금 푸시한 커밋 sha 가 있어야 한다.

## 실패 시 대응

| 실패 양상 | 대응 |
|---|---|
| collection ImportError (httpx) | `references/pitfalls.md` §1 — `markdown_report` 전이 import 확인 |
| collection ModuleNotFoundError (scripts) | §4 — `scripts/__init__.py` 확인 |
| runtime TypeError (Decimal/float) | §2 — 캔들 값 `Decimal(str(c.high))` 변환 확인 |
| pipeline live 모드 ImportError | §3 — legacy 경로 — 테스트는 영향 없음 |
| 14 passed 아닌 다른 숫자 | 옮긴 파일 본문 누락 여부 확인 (`diff references/files/04-test-daily-pipeline.py backend/tests/test_daily_pipeline.py`) |
| 회귀 실패 | 기존 코드 미변경 가정 위반 — 무관한 잔재가 stage 됐는지 확인 |
