# 키움 매매이력 SQLite 운영 가이드

운영 머신의 키움 REST API에서 체결·실현손익을 읽어
`data/kiwoom_trade_history_1y.db`에 보관하고, 개발 머신에서 전략 연구에 사용하는 절차다.

이 도구는 읽기 전용이다. 계좌 조회 URL(`/api/dostk/acnt`)과 다음 TR만 코드에서
허용한다.

- `kt00009`: 일자별 주문·체결 현황
- `kt00015`: 기간별 위탁 매매·정산·수수료 원장
- `ka10073`: 기간별 종목 실현손익
- `ka10074`: 기간별 일자 실현손익

주문·정정·취소 URL과 TR은 구현되어 있지 않다. 기존
`KiwoomNativeOAuth`의 프로세스 간 공유 토큰 캐시를 재사용하므로 운영 프로세스와
토큰을 불필요하게 경쟁하지 않는다.

키움 공식 사양:

- [계좌 API 가이드](https://openapi.kiwoom.com/guide/apiguide?jobTpCode=08)
- [서비스 및 호출 한도 안내](https://openapi.kiwoom.com/intro)

## 최초 1년 동기화

프로젝트 루트에서 실행한다. 날짜를 생략하면 KST 기준 오늘부터 정확히 1년 전까지다.

```bash
set -a
. ./.env.local
set +a
python3 scripts/sync_kiwoom_trade_history.py sync \
  --db data/kiwoom_trade_history_1y.db \
  --from 2025-07-12 \
  --to 2026-07-12
```

스크립트가 `.env.local`을 직접 읽으므로 위의 `set -a` 과정은 생략해도 된다. 이미
프로세스 환경에 같은 이름의 변수가 있으면 그 값이 우선한다.

소스 계정은 `KIWOOM_BASE_URL`로 결정한다.

- `https://api.kiwoom.com`: 실계정
- `https://mockapi.kiwoom.com`: 모의계정

실계정과 모의계정은 별도 App Key를 사용한다. DB의 `schema_meta.environment`와
manifest의 `metadata.environment`를 복사 전에 반드시 확인한다.

키움 모의 서버는 `kt00015` 호출에 `return_code=20`(RC9000, 모의투자 미제공)을
반환한다. 따라서 모의 DB는 `kt00009` 체결과 `ka10073`/`ka10074` 실현손익으로
구성되며 `schema_meta.kt00015_status=unsupported_by_mock_server`로 남는다. 실계정
동기화에서는 `kt00015` 위탁 거래원장도 월 단위로 수집한다.

## 증분 동기화

최근 체결 정정 가능성을 흡수하도록 마지막 성공일보다 7일 앞에서 다시 조회한다.
자연키 upsert이므로 재실행해도 체결이 중복되지 않는다.
운영 조회 quota 보호를 위해 평일 08:30~16:00 KST에는 기본적으로 실행을 거부한다.

```bash
python3 scripts/sync_kiwoom_trade_history.py sync \
  --db data/kiwoom_trade_history_1y.db \
  --incremental
```

## 검증

```bash
python3 scripts/sync_kiwoom_trade_history.py verify \
  --db data/kiwoom_trade_history_1y.db \
  --expect-environment mock
```

실계정 연구 DB를 검수할 때는 `--expect-environment real`을 사용한다. 소스가
모의계정이면 다른 무결성 검사가 모두 통과해도 검증 명령이 실패하므로, 모의 DB를
실계정 원장으로 잘못 배포하는 것을 막을 수 있다.

다음을 모두 확인한다.

- SQLite `integrity_check`
- foreign key 위반
- 저장된 API 원문 SHA-256
- 테이블별 행 수
- 실제 체결 날짜 범위와 조회 성공 날짜 범위
- DB 파일 SHA-256

동기화 후 `data/kiwoom_trade_history_1y.manifest.json`도 자동 생성된다. DB 파일과
manifest의 SHA-256이 일치하는지 개발 머신에서 다시 검증한다.

## 개발 머신 전달

동기화가 끝난 DB는 WAL을 checkpoint하고 `journal_mode=DELETE`로 닫으므로 단일
`.db` 파일로 복사할 수 있다. 동기화 중인 DB를 복사해야 한다면 SQLite backup API를
사용하는 `snapshot` 명령으로 일관된 파일을 만든다.

```bash
python3 scripts/sync_kiwoom_trade_history.py snapshot \
  --db data/kiwoom_trade_history_1y.db \
  --output data/kiwoom_trade_history_1y.snapshot.db
```

그 뒤 Git이 아닌 보안 채널로 DB와 manifest를 함께 전달한다.

```bash
rsync -av \
  data/kiwoom_trade_history_1y.db \
  data/kiwoom_trade_history_1y.manifest.json \
  DEV_HOST:/path/to/research-data/
```

DB에는 토큰, App Key, App Secret, 전체 계좌번호가 들어가지 않는다. 다만 종목별
매매 내역 자체가 민감정보이므로 파일 권한은 `0600`으로 유지한다.

## 연구용 조회

개발 머신에서는 원본을 수정하지 않도록 read-only로 연다.

```python
import sqlite3

db = sqlite3.connect(
    "file:research-data/kiwoom_trade_history_1y.db?mode=ro&immutable=1",
    uri=True,
)
fills = db.execute(
    """
    SELECT trade_date_kst, executed_at_kst, symbol, side, qty, price_krw
    FROM v_trade_fills
    ORDER BY executed_at_kst
    """
).fetchall()
```

주요 객체:

- `orders`: 주문번호 기준 최신 주문 스냅숏
- `executions`: 체결번호 기준 원장
- `realized_pnl`: 일자·종목별 브로커 실현손익과 비용
- `daily_pnl`: 일자별 브로커 실현손익 합계
- `cash_ledger`: 위탁 거래번호 기준 매매·정산·수수료·세금 원장
- `raw_responses`: 재현을 위한 정확한 API 응답 바이트
- `sync_runs`, `sync_days`, `sync_windows`: 조회 범위와 성공 여부
- `v_trade_fills`: 전략 연구용 체결 뷰
- `v_daily_trades`: 일자·종목별 매수/매도 VWAP 및 실현손익 뷰
- `v_cash_ledger`: 개발 머신에서 쓰기 쉬운 위탁 거래원장 뷰

`v_daily_trades.net_cashflow_before_costs_krw`는 매도대금에서 매수대금을 뺀
현금흐름이며 실현손익과 같지 않다. 전략 성과에는 키움이 제공한
`broker_realized_pnl_krw`와 수수료·세금을 사용한다.

일별 권위 합계는 `daily_pnl`(`ka10074`)을 사용한다. `realized_pnl`(`ka10073`)의
`buy_amount_krw[_exact]`는 매도된 포지션의 매입단가 기반 원가이고,
`daily_pnl.buy_amount_krw[_exact]`는 해당 날짜의 매수 활동 금액이므로 서로 같은
범위의 값이 아니다. 종목별 손익 귀속에는 `realized_pnl`, 일별 최종 합계에는
`daily_pnl`을 사용한다. 검증기는 두 TR에서 같은 의미인 매도금액·세금이 일별로
일치하는지 필수 검사한다.

`*_exact` 컬럼은 모의 서버가 반환하는 소수 원화 값을 손실 없이 문자열 decimal로
보존한다. 회계·성과 계산은 Python `Decimal`로 이 컬럼을 읽고, 정수 원 단위가 필요한
화면 표시에는 대응하는 `*_krw` 컬럼을 사용한다.

## 데이터 범위 주의

요청한 모든 평일은 `sync_days`에 기록한다. 체결이 없는 날도 성공한 조회일로 남는다.
실제 반환 가능한 과거 범위는 키움 서버와 계정 종류에 따르므로, “1년을 요청했다”와
“1년간 체결이 반환됐다”를 구분해야 한다. 복사 전 manifest의
`sync_date_min/max`와 `execution_date_min/max`를 함께 확인한다.

`ka10170` 당일매매일지는 공식상 최근 2개월만 제공하므로 1년 원장에는 사용하지 않는다.
