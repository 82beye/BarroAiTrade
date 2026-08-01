# ai_swing 운영 런북

`ai-trade`가 만든 당일 스캔·예측 교집합을 `AiSwingStrategy`로 다시 판정한다.
`scripts/ai_swing_daemon.py`는 관측·텔레그램 전용이며 주문하지 않는다. 실제 진입은
`scripts/intraday_buy_daemon.py` 안의 기존 주문 경로만 사용한다.

상태: 모든 플래그 기본 OFF. 실주문 전환은 운영자 승인 사항이다.

관련 문서:

- 설계: `docs/02-design/features/2026-07-31-ai-swing.design.md`
- 단타 진입 중지: `docs/operations/daytrading-pause-to-ai-swing.md`
- 실측 원본: `docs/04-report/features/2026-07-30-ai-swing-p0.report.md`

## 1. 배포 전제

두 저장소가 모두 운영 머신에 배포되어야 한다.

| 저장소 | 필요한 변경 | 확인 |
|---|---|---|
| `ai-trade` | `PredictionCoordinator.predict()`가 `logs/predictions_YYYY-MM-DD.json`을 원자 저장 | `rg '_save_predictions' scanner/agents/coordinator.py` |
| `BarroAiTrade` | 당일 교집합 진입, 예산·슬롯 캡, shadow 이력·텔레그램, 복구·청산 보강 | `rg '_ai_swing_current_signal' scripts/intraday_buy_daemon.py` |

운영 머신의 실제 checkout으로 이동한 뒤 절대경로를 고정한다. 아래 두 `cd` 경로는 실행 전에
운영 머신에서 확인한 값으로 바꾸며, 저장소 문서에 남은 과거 경로를 복사하지 않는다.

```bash
cd /ACTUAL/PATH/BarroAiTrade || exit 1
BARRO_REPO="$(pwd -P)"
cd /ACTUAL/PATH/ai-trade || exit 1
AI_TRADE_REPO="$(pwd -P)"

if [ -x "$BARRO_REPO/.venv/bin/python" ]; then
  PYTHON="$BARRO_REPO/.venv/bin/python"
elif [ -x "$BARRO_REPO/venv/bin/python" ]; then
  PYTHON="$BARRO_REPO/venv/bin/python"
else
  echo 'BarroAiTrade 가상환경 Python 없음'; exit 1
fi
if [ -x "$AI_TRADE_REPO/.venv/bin/python" ]; then
  AI_TRADE_PYTHON="$AI_TRADE_REPO/.venv/bin/python"
elif [ -x "$AI_TRADE_REPO/venv/bin/python" ]; then
  AI_TRADE_PYTHON="$AI_TRADE_REPO/venv/bin/python"
else
  echo 'ai-trade 가상환경 Python 없음'; exit 1
fi
export BARRO_REPO AI_TRADE_REPO PYTHON AI_TRADE_PYTHON

test "$(date '+%Z %z')" = 'KST +0900' || {
  echo '시스템 시간대가 Asia/Seoul(KST +0900)이 아님'; exit 1;
}
printf 'BARRO_REPO=%s\nAI_TRADE_REPO=%s\nPYTHON=%s\nAI_TRADE_PYTHON=%s\n' \
  "$BARRO_REPO" "$AI_TRADE_REPO" "$PYTHON" "$AI_TRADE_PYTHON"
date '+%F %T %Z %z'
```

각 저장소의 브랜치와 full SHA가 배포 승인 기록과 같고 worktree가 깨끗해야 한다. `rg` 결과만으로
배포 완료를 판정하지 않는다.

```bash
cd "$BARRO_REPO"
git status --short --branch
git branch --show-current
git rev-parse HEAD
test -z "$(git status --porcelain)" || { echo 'BarroAiTrade worktree dirty'; exit 1; }
"$PYTHON" -m pytest backend/tests -q

cd "$AI_TRADE_REPO"
git status --short --branch
git branch --show-current
git rev-parse HEAD
test -z "$(git status --porcelain)" || { echo 'ai-trade worktree dirty'; exit 1; }
"$AI_TRADE_PYTHON" -m unittest -v test_prediction_persistence.py

crontab -l
ps aux | grep -E 'ai-trade|intraday_buy_daemon|evaluate_holdings|run_telegram_bot' | grep -v grep
```

`ai-trade/scripts/setup_cron.sh`는 프로젝트와 Python 경로가 하드코딩되어 있다. 운영 머신에서
두 경로를 확인하기 전에는 실행하지 않는다. 서버 시간대도 `Asia/Seoul`이어야 한다.
watchlist는 시스템 날짜, predictions는 KST 날짜를 쓰므로 시간대가 다르면 파일명이 갈린다.

주문 플래그를 열기 전에는 `.env.local`을 소싱하고 endpoint와 계좌 잔고를 읽기 전용으로 확인한다.
첫 필드 테스트가 모의인지 실계좌인지 승인 기록에 명시하며, `KIWOOM_BASE_URL` 미설정은 허용하지 않는다.

```bash
cd "$BARRO_REPO"
set -a; . "$BARRO_REPO/.env.local"; set +a
if [ "${BARRO_DATA_DIR+x}" = x ]; then
  echo 'BARRO_DATA_DIR는 빈 값 포함 미설정이어야 함 — 원장 경로 불일치'; exit 1
fi
CUSTOM_LEDGER_ARGS="$({ crontab -l 2>/dev/null || true; ps aux; } | \
  grep -E '[i]ntraday_buy_daemon.*--(pos-log|audit-log)' || true)"
test -z "$CUSTOM_LEDGER_ARGS" || {
  printf '%s\n' "$CUSTOM_LEDGER_ARGS"
  echo 'live intraday_buy_daemon은 기본 data/ 원장을 사용해야 함'; exit 1
}
case "$KIWOOM_BASE_URL" in
  https://mockapi.kiwoom.com) echo 'Kiwoom mode=MOCK' ;;
  https://api.kiwoom.com)     echo 'Kiwoom mode=REAL — 실계좌 승인 필수' ;;
  *) echo "미승인 KIWOOM_BASE_URL=$KIWOOM_BASE_URL"; exit 1 ;;
esac
"$PYTHON" - <<'PY'
import asyncio, os
from pydantic import SecretStr
from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ["KIWOOM_BASE_URL"],
    )
    balance = await KiwoomNativeAccountFetcher(oauth=oauth).fetch_balance()
    print(f"read-only balance: holdings={len(balance.holdings or [])} "
          f"estimated_asset={balance.estimated_deposit}")

asyncio.run(main())
PY
```

현재 `intraday_buy_daemon`은 repo `data/`를 기본 원장으로 쓰지만 shadow/recover는
`BARRO_DATA_DIR`를 해석하고 recover에는 대응 경로 CLI가 없다. 따라서 라이브 운영에서는
`BARRO_DATA_DIR`를 빈 값으로도 두지 말고 완전히 unset하며, 데몬의 `--pos-log`/`--audit-log`
커스텀 인자도 금지한다. 그래야 아래 백업·진단·복구가 실제 운영 원장을 가리킨다.

코드에는 KRX 휴장일 캘린더가 없으므로 당일이 실제 거래일이고 정규장이 열렸는지도 브로커 화면에서
별도로 확인한다. endpoint·계좌·잔고·거래일 중 하나라도 예상과 다르면 다음 단계로 넘어가지 않는다.

## 2. 환경 설정

`BarroAiTrade/.env.local`에 아래 값을 추가한다. `BARRO_AI_TRADE_DIR`은 운영 머신의
`ai-trade/logs` 절대경로다.

```bash
BARRO_AI_TRADE_DIR=/ABS/PATH/ai-trade/logs

# shadow
BARRO_AI_SWING_ENABLED=1
BARRO_AI_SWING_FALLBACK=scan_only
BARRO_AI_SWING_ALLOW_STALE=0
BARRO_AI_SWING_MAX_AGE_H=12
BARRO_AI_SWING_MIN_PRED_SCORE=0
BARRO_AI_SWING_MIN_CONSENSUS=
BARRO_AI_SWING_TOP_N=0

# 실제 진입은 계속 닫아 둔다
BARRO_AI_SWING_ENTRY_ENABLED=0
BARRO_AI_SWING_BUDGET_RATIO=0
BARRO_AI_SWING_MAX_POSITIONS=1

# 기존 텔레그램 설정을 사용한다
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

`FALLBACK=scan_only`는 당일 예측 파일이 없을 때 당일 스캔 목록만 관측하기 위한 용도다.
이 경우도 watchlist의 날짜와 수정시간이 `MAX_AGE_H`를 통과해야 하며, 과거 파일은
`ALLOW_STALE=0`에서 판정하지 않는다. 완전 교집합은 shadow와 실진입 모두 공통
신선도 검사(당일 두 파일 + 수정시간 12시간 이내)를 통과해야 한다. 실진입은 `scan_only`와
`ALLOW_STALE`을 허용하지 않고 오직 이 검사를 통과한 `status=ok`만 사용한다.

환경 파일은 스크립트가 자동 로드하지 않는다. 수동·cron 실행 모두 먼저 소싱한다.

```bash
cd "$BARRO_REPO"
set -a
. "$BARRO_REPO/.env.local"
set +a
```

## 3. ai-trade 산출물

저장소의 `scripts/setup_cron.sh` 템플릿은 평일 08:25 `python main.py --mode simulation`을
제안한다. 이것이 운영 머신의 실제 일정이라는 보장은 없으므로 §1의 `crontab -l`로 먼저
확인한다. 해당 실행 흐름은 Pre-Market 스캔 후 예측 순서이며 각각 아래 파일을 만든다.

```text
ai-trade/logs/watchlist_YYYY-MM-DD.json
ai-trade/logs/predictions_YYYY-MM-DD.json
```

09:00 전 확인:

```bash
TODAY=$(date +%F)
test -s "$BARRO_AI_TRADE_DIR/watchlist_${TODAY}.json"
test -s "$BARRO_AI_TRADE_DIR/predictions_${TODAY}.json"
ls -l "$BARRO_AI_TRADE_DIR"/{watchlist,predictions}_"${TODAY}".json
"$PYTHON" - <<'PY'
import json, os
from datetime import date
for name in ("watchlist", "predictions"):
    path = os.path.join(os.environ["BARRO_AI_TRADE_DIR"], f"{name}_{date.today():%Y-%m-%d}.json")
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh).get("date") == date.today().isoformat(), path
print("source JSON date OK")
PY
```

둘 중 하나가 없으면 `ai-trade` 로그에서 스캔/예측 실패를 먼저 고친다. 빈 파일을 만들거나
전일 파일을 복사하지 않는다.

## 4. shadow 실행과 텔레그램 종목 목록

주문 없는 1회 실행:

```bash
cd "$BARRO_REPO"
set -a; . "$BARRO_REPO/.env.local"; set +a
"$PYTHON" scripts/ai_swing_daemon.py --telegram
echo "exit=$?"
```

텔레그램은 선정 종목 전체를 번호로 나열하고 종목명·코드, scan/pred 점수, 현재 신호 유무와
사유, 신호가 있으면 예상 진입가·SL·TP1을 보낸다. `scan_only`이면 “예측 없음”으로 표시한다.
텔레그램 실패는 파일 관측 결과를 지우지 않으며 종료코드도 실패로 바꾸지 않는다. 따라서
**텔레그램 채팅에서 당일 선정 목록을 직접 수신한 것**까지가 성공 조건이다.

산출물:

| 파일 | 의미 |
|---|---|
| `data/ai_swing_universe.json` | 최신 로더 결과 |
| `data/ai_swing_signals.json` | 최신 판정·제외 사유 |
| `data/ai_swing_history/YYYY-MM-DD.jsonl` | 실행별 유니버스·선정 목록·신호·상태 누적 |

확인:

```bash
"$PYTHON" -m json.tool data/ai_swing_universe.json | head -80
"$PYTHON" -m json.tool data/ai_swing_signals.json | head -120
tail -1 "data/ai_swing_history/$(date +%F).jsonl"
```

hard error는 종료코드 1과 `run_status=error`를 남긴다. 데이터 차단·원본 부재·시세 조회기
부재는 대체로 `run_status=degraded`지만, 허용된 완전 stale이나 `status=ok`의 빈 교집합은
`run_status=ok`일 수 있다. 따라서 `run_status`만 보지 말고 `universe_status`, `reason`,
`evaluated`를 함께 확인한다.

운영 crontab에서 ai-trade가 실제로 08:25에 시작된다면 shadow 첫 시도는 09:00에 둔다.
등록된 값에는 §1에서 확정한 실제 절대경로만 사용하고, 당일 두 파일이 없으면 오류 로그와
non-zero를 남겨 기존 운영 알림이 울리게 한다. `%`는 crontab에서 `\%`로 이스케이프해야 한다.
등록 전 `mkdir -p "$BARRO_REPO/logs"`로 로그 디렉터리를 만든다.

```cron
# 아래 두 값은 §1 출력의 실제 절대경로로 등록한다. /ACTUAL/PATH 문자열을 남기지 않는다.
BARRO_REPO=/ACTUAL/PATH/BarroAiTrade
PYTHON=/ACTUAL/PATH/BarroAiTrade/venv/bin/python
0 9 * * 1-5 cd "$BARRO_REPO" && set -a && . "$BARRO_REPO/.env.local" && set +a && { test -z "${BARRO_DATA_DIR+x}" || { echo "[ALERT] BARRO_DATA_DIR must be unset" >&2; exit 1; }; } && TODAY=$(date +\%F) && { test -s "$BARRO_AI_TRADE_DIR/watchlist_${TODAY}.json" && test -s "$BARRO_AI_TRADE_DIR/predictions_${TODAY}.json" || { echo "[ALERT] ai_swing source missing ${TODAY}" >&2; exit 1; }; } && "$PYTHON" "$BARRO_REPO/scripts/ai_swing_daemon.py" --telegram >> "$BARRO_REPO/logs/ai_swing_shadow.log" 2>&1
```

09:00에 원본이 늦어 실패하면 09:05, 필요 시 09:10에 §4의 1회 명령을 사람이 다시 실행한다.
중복 텔레그램을 막기 위해 성공한 날을 무조건 반복하는 cron은 추가하지 않는다. 다음 네 가지를
모두 확인해야 재시도 완료다.

- 두 당일 원본 존재, `run_status=ok`, `universe_status=ok`.
- `as_of`가 당일 KST이고 원본 수정시간이 `MAX_AGE_H` 이내.
- `logs/ai_swing_shadow.log`에 예기치 않은 오류가 없음.
- 텔레그램 채팅에 당일 번호 매긴 선정 목록이 실제 도착함. 미수신이면 로그 확인 후 `--telegram`을 수동 재실행.

## 5. dry-run 필드 테스트

shadow에서 당일 `status=ok`, `intersect_count>0`, `evaluated>0`, `signal_count>0`이 확인된 뒤에만
적용한다. 실제 후보 중 최소 1개가 유니버스 필터·예산·슬롯·리스크 게이트까지 통과해야
`DRY_RUN` 주문 배선을 검증할 수 있다. 실주문 플래그와 `--no-dry-run`은 사용하지 않는다.

```bash
# .env.local
BARRO_DAEMON_STRATEGIES=ai_swing
BARRO_AI_SWING_FALLBACK=
BARRO_AI_SWING_ENABLED=1
BARRO_AI_SWING_ENTRY_ENABLED=1
BARRO_AI_SWING_BUDGET_RATIO=0.10
BARRO_AI_SWING_MAX_POSITIONS=1
BARRO_AI_SWING_MAX_AGE_H=12
BARRO_AI_SWING_ALLOW_STALE=0
```

```bash
cd "$BARRO_REPO"
set -a; . "$BARRO_REPO/.env.local"; set +a
FIELD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/barro-ai-swing-field.XXXXXX")
FIELD_POS_LOG="$FIELD_DIR/active_positions.json"
FIELD_AUDIT_LOG="$FIELD_DIR/order_audit.csv"
echo "field artifacts: $FIELD_DIR"
"$PYTHON" "$BARRO_REPO/scripts/intraday_buy_daemon.py" \
  --dry-run --entry-only-once --strategies ai_swing --telegram \
  --pos-log "$FIELD_POS_LOG" --audit-log "$FIELD_AUDIT_LOG"
```

이 명령은 KST 정규 거래일 09:05~15:20에만 실행한다. 진입 스캔을 정확히 한 번 수행하고
매도·DCA·EOD·운영 신호/스냅샷 저장 경로를 실행하지 않는다. DRY_RUN은 포지션을
생성하지 않고 audit와 일일 손실 latch를 `FIELD_DIR` 아래로 격리한다. `FIELD_DIR`은
검토가 끝날 때까지 보존한다.

다음 조건이 모두 성립해야 통과다.

- 당일 watchlist와 predictions가 모두 있고 각각 12시간 이내다.
- 실제 진입 후보는 shadow와 같은 `AiSwingStrategy.analyze()` 현재 일봉 신호다.
- 교집합 밖 종목, `partial`, `stale`, 데이터 판정 실패는 진입하지 않는다.
- 주문 직전 호가 기준 ai_swing 예상 주문금액 합계가 추정자산의 10% 이내이고 동시 보유는
  1종목 이하다.
- 위 게이트를 통과한 후보가 있으면 `$FIELD_AUDIT_LOG`의 ai_swing 행이 `DRY_RUN`이다. 없으면
  이번 실행은 관측 전용이며 실제 주문 배선 통과로 판정하지 않는다.
- `$FIELD_POS_LOG`가 생성되지 않았거나 포지션이 없고, 운영 `data/active_positions.json`과
  운영 audit가 바뀌지 않았다.

실주문 전환 시 현재 진입은 기존 추천 수량의 60%를 1회 주문하고 단일 tranche로 기록한다.
ai_swing에는 generic DCA를 연결하지 않았다. 이 구조로 충분하며 체결 표본 없이 2차 진입을
추가하지 않는다.

예산 캡은 주문 직전 조회 가격으로 수량을 내림 제한한다. 시장가 주문의 실제 체결가는 그 가격보다
높을 수 있어 최종 체결금액이 10%를 소폭 넘을 수 있다. 따라서 10%는 체결 보장선이 아니라
pre-order cap이며, 체결 후 broker 평균체결가·수량으로 실제 비중을 다시 확인한다.

## 6. 실주문 전환 게이트

실주문은 다음 체크를 사람이 확인한 뒤 별도 승인한다. 이 런북을 실행했다는 사실만으로 승인된
것이 아니다.

1. 최소 5거래일 shadow 이력과 당일 텔레그램 목록이 있다.
2. 최소 1거래일 dry-run 감사 로그에서 ai_swing 외 주문 후보가 없다.
3. ai_swing 실매도 writer는 `intraday_buy_daemon` 하나다. 운영 포지션 장부를 읽는
   `evaluate_holdings.py` cron은 DRY_RUN 여부와 관계없이 전체 중지한다.
4. `scripts/ai_swing_recover.py --dry-run` 결과가 고아 0건이다.
5. 예산 10%, 슬롯 1을 유지한다.
6. §1의 KST·거래일·`KIWOOM_BASE_URL`·모의/실계좌·읽기 전용 잔고가 승인 기록과 같다.
7. 운영자가 `LIVE_TRADING_ENABLED=true`와 `--no-dry-run`을 명시한다.

실주문 명령은 기존 `intraday_buy_daemon` 운영 프로세스에 ai_swing 설정을 합치는 방식으로만
적용한다. 별도 두 번째 데몬을 띄워 `active_positions.json` 작성자를 늘리지 않는다. `.env.local`
변경은 실행 중 프로세스에 자동 반영되지 않으므로 기존 서비스 방식으로 해당 데몬을 재기동하고,
이전 PID가 종료됐고 새 PID가 하나만 남았는지 확인한다.

## 7. 청산과 복구

청산은 `intraday_buy_daemon._evaluate_and_sell`만 담당한다. 이 경로는 ai_swing의 sell intent,
미체결·확정 체결 대사와 중복 주문 차단을 함께 제공한다. `evaluate_holdings.py`는 같은 판단
프로파일을 쓰더라도 이 수명주기가 없고 DRY_RUN도 장부를 갱신할 수 있으므로, 운영 장부를 보는
cron 자체를 중지한다. 별도 진단은 복사한 장부와 격리된 `--pos-log`/`--audit-log`만 사용한다.

ai_swing 라이브 정책:

- SL 기본 -5%: `min_hold_days`보다 먼저 평가한다.
- 그 외 TP·트레일링은 최소 보유 3일 이후 평가한다.
- 최대 보유 20일에 전량 청산한다.
- 트레일링은 +10%부터 활성, 고점에서 가격 기준 3% 하락 시 청산한다.

백테스트의 보호된 `ExitEngine`은 아직 min-hold 전에 hard SL을 허용하지 않는다. 따라서 현재
백테스트와 라이브의 첫 3일 손절 동작은 다르다. 라이브 안전 정책을 우선하며, 이 차이를 해소한
새 백테스트 전에는 과거 수익률을 라이브 기대수익으로 해석하지 않는다.

라이브 ai_swing 매수는 외부 주문 호출 전 복구 가능한 provisional intent를
`active_positions.json`에 원자 저장한다. 확정적인 게이트 차단이면 제거하고, 접수되면 실제
주문번호로 갱신한다. 결과가 불명확하면 보존하고 추가 주문을 중지한다. 매도도 외부 호출 전
`order_audit.csv`에 intent를 fsync해, 응답 timeout이나
`ORDERED` 기록 실패 후에도 잔고 감소를 대사한다. 다음 주기에 브로커 잔고와 미체결을
대사해 확정 매수·매도만 `FILLED`로 백필한다. 미체결 주문이 남은 종목과 최근 매도
intent는 5분 대사 유예 중 재매도하지 않는다. 미체결 조회나 매도 audit 읽기·쓰기·fsync가
실패하면 잘못된 확정·장부 변경 대신 해당 주기 대사를 건너뛴다. `[POSITION-ERR]`나 `[POSITION-CLEANUP-ERR]`가
나오거나 `[ORDER-UNCERTAIN]` 또는 `[SELL-ORDER-UNCERTAIN]`이 나오면 추가 주문을 중지하고 브로커
주문·잔고·장부·audit를 수동 대사한다.

고아 진단은 주문을 만들지 않는다.

```bash
cd "$BARRO_REPO"
set -a; . "$BARRO_REPO/.env.local"; set +a
"$PYTHON" "$BARRO_REPO/scripts/ai_swing_recover.py" --dry-run
```

복구는 체결수량과 평균체결가가 확인된 audit 또는 `FILLED`만 인정한다. `DRY_RUN`, 단순
`ORDERED`, 미체결, 매수·매도 상계가 불명확한 기록은 복원하지 않는다. `--apply`는 출력된
복원 후보와 브로커 수량이 정확히 일치할 때만 사람이 실행한다.

`--apply`는 `active_positions.json`을 쓰므로 다음 순서를 지킨다.

1. 관련 cron을 잠시 비활성화하고 `intraday_buy_daemon`, `run_telegram_bot`,
   `evaluate_holdings`, `intraday_buy.py`, `simulate_leaders`, `_daily_evening_pipeline` 등
   `active_positions.json` writer를 기존 서비스 방식으로 모두 중지한다.
2. 아래 `ps` 결과가 0줄인지 확인하고 장부를 timestamp 이름으로 백업한다.
3. `--apply` 후 즉시 다시 `--dry-run`하여 고아 0건과 장부 수량을 확인한다.
4. 승인된 writer만 재기동하고, ai_swing 실매도는 `intraday_buy_daemon`만 담당하게 한다.

```bash
WRITERS="$(ps aux | grep -E 'intraday_buy_daemon|run_telegram_bot|evaluate_holdings|intraday_buy.py|simulate_leaders|_daily_evening_pipeline' | grep -v grep || true)"
test -z "$WRITERS" || { printf '%s\n' "$WRITERS"; echo 'writer 중지 필요'; exit 1; }
BACKUP_STAMP=$(date '+%Y%m%d-%H%M%S')
if [ -f "$BARRO_REPO/data/active_positions.json" ]; then
  cp -p "$BARRO_REPO/data/active_positions.json" \
    "$BARRO_REPO/data/active_positions.json.before-ai-swing-recover-${BACKUP_STAMP}"
fi
"$PYTHON" "$BARRO_REPO/scripts/ai_swing_recover.py" --apply
"$PYTHON" "$BARRO_REPO/scripts/ai_swing_recover.py" --dry-run
```

## 8. 중지와 롤백

보유분이 있으면 진입부터 닫고 `intraday_buy_daemon` 청산 경로는 유지한다. 아래 값을 `.env.local`에
반영하는 것만으로 실행 중 데몬은 바뀌지 않는다.

```bash
BARRO_AI_SWING_ENTRY_ENABLED=0
BARRO_AI_SWING_BUDGET_RATIO=0
```

`LIVE_TRADING_ENABLED=false`를 첫 롤백으로 사용하지 않는다. 매수뿐 아니라 자동 매도도 막는다.
기존 서비스 방식으로 `intraday_buy_daemon`을 재기동하고 이전 PID 종료·새 PID 1개·진입 차단
로그를 확인해야 롤백이 완료된다. `BARRO_AI_SWING_ENABLED=0`은 shadow와 신규 진입을 끄지만
장부 기반 청산은 끄지 않는다. shadow까지 중지할 때만 내리고 청산 writer는 계속 유지한다.

```bash
ps aux | grep -E 'intraday_buy_daemon|evaluate_holdings|run_telegram_bot' | grep -v grep
"$PYTHON" "$BARRO_REPO/scripts/ai_swing_recover.py" --dry-run
```

## 9. 배포 검증

개발/CI에서 확인할 최소 명령:

```bash
cd "$BARRO_REPO"
"$PYTHON" -m pytest backend/tests -q
"$PYTHON" -c 'from backend.main import app; print("D2 OK")'
git diff --check

cd "$AI_TRADE_REPO"
"$AI_TRADE_PYTHON" -m unittest -v test_prediction_persistence.py
git diff --check
```

운영 머신에서는 실주문 없이 §3 산출물, §4 shadow/텔레그램, §5 dry-run, §7 고아 0건 순서로
검증한다. 어느 단계든 데이터가 없거나 모호하면 다음 단계로 넘어가지 않는다.
