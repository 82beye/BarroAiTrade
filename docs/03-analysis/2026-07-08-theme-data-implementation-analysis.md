# 테마 데이터 분석 및 구현 현황

작성일: 2026-07-08 09:47 KST
대상: BarroAiTrade 테마 보드, 키움 REST 랭킹 row 저장, 테마별 등락률/거래대금 집계

## 요약

현재 테마 데이터는 세 계층으로 나뉜다.

1. 테마 마스터/구성 종목
   - DB 테이블 `themes`, `theme_stocks`
   - Finup 스냅숏 또는 뉴스 기반 자동발굴 결과가 적재된다.

2. 테마 화면 표시용 종목 데이터
   - `/api/themes/{theme_id}/stocks`
   - 기본값은 DB/캐시 기반 즉시 응답이다.
   - `enrich=true`일 때만 키움 개별 ticker 조회를 수행한다.

3. 신규 구현된 실시간 통계 원천 row
   - 키움 랭킹 TR row를 CSV로 저장한다.
   - 같은 CSV row를 기준으로 테마별 거래대금/등락률 집계를 만든다.
   - 스케줄러가 정규장 중 60초마다 실행한다.
   - 빈 응답은 마지막 정상 `latest` 스냅숏을 덮어쓰지 않는다.

핵심 변화는 "테마 통계를 바로 DB에 덮어쓰기"가 아니라, 먼저 키움 랭킹 row를 CSV로 남긴 뒤 그 row를 기준으로 테마 집계를 만드는 구조를 추가한 점이다. 이로써 운영 중 실제 원천 row를 열어 검증할 수 있고, 이후 DB 적재나 프론트 반영 전에 데이터 품질을 확인할 수 있다.

## 현재 데이터 흐름

### 1. 테마 마스터

테마 목록은 DB에서 조회된다.

```text
themes
  id
  name
  description

theme_stocks
  theme_id
  symbol
  score
```

관련 API:

```text
GET /api/themes
GET /api/themes/{theme_id}/stocks
GET /api/stocks/{symbol}/themes
```

현재 `/api/themes/{theme_id}/stocks`의 기본 동작은 다음과 같다.

```text
enrich=false
  DB theme_stocks.score를 change_pct fallback으로 반환
  price/value_traded는 채우지 않음

enrich=true
  키움 개별 ticker 조회
  price/change_pct/volume 보강
  value_traded = price * volume / 1e8 계산
```

중요한 점은 기본 테마 화면 경로가 키움 개별 ticker를 대량 호출하지 않도록 되어 있다는 것이다. Finup 기반 테마는 테마당 종목 수가 많기 때문에, 프론트 polling이 직접 키움 ticker 조회를 유발하면 화면 지연과 키움 429 rate-limit이 커진다.

### 2. 키움 랭킹 row 원천

신규 구현은 키움 랭킹 API를 사용한다.

```text
ka10032 거래대금상위
ka10027 전일대비등락률상위
```

현재 수집 필터:

```text
value
gainers
losers
```

기본 수집 파라미터:

```text
top_n = 100
stex_tp = 3
mrkt_tp = 000
interval = 60s
```

키움 랭킹 row는 `000660_AL` 같은 코드가 내려올 수 있으므로, 구현부에서 `000660` 형태로 정규화한다.

## 신규 구현 파일

### 키움 row 저장 및 집계

```text
backend/core/themes/market_row_store.py
```

역할:

```text
fetch_ranking_rows()
  키움 랭킹 API 호출
  value/gainers/losers row 정규화

merge_symbol_rows()
  중복 심볼 병합
  source별 rank 보존

aggregate_theme_memberships()
  CSV row와 theme_stocks를 symbol 기준 join
  테마별 등락률/거래대금 집계

capture_theme_market_rows()
  rows CSV 저장
  aggregates CSV 저장
  latest.json 갱신
```

### 스케줄러

```text
backend/core/scheduler/theme_market_row_jobs.py
```

역할:

```text
theme_market_rows_capture
  기본 60초마다 실행
  키움 랭킹 row CSV 저장
  테마 집계 CSV 생성
```

환경변수:

```text
BARRO_THEME_MARKET_ROWS_ENABLED=0       # 비활성화
BARRO_THEME_MARKET_ROWS_INTERVAL_SEC=60 # 실행 주기
BARRO_THEME_MARKET_ROWS_TOP_N=100       # 랭킹별 수집 개수
BARRO_THEME_MARKET_ROWS_FILTERS=value,gainers,losers
BARRO_THEME_MARKET_ROWS_DIR=...         # CSV 저장 위치 override
```

등록 위치:

```text
scripts/finance/telegram_integration/scheduler.py
```

현재 로그에서 확인된 등록 상태:

```text
테마 랭킹 row CSV 저장 잡 등록 완료: theme_market_rows_capture (interval=60s)
Added job "테마 랭킹 row CSV 저장 (60s)"
```

### API

```text
backend/api/routes/themes_calendar_news.py
```

추가 API:

```text
POST /api/themes/market-rows/capture
GET  /api/themes/market-rows/latest
GET  /api/themes/market-aggregates/latest
```

수동 캡처 예:

```bash
curl -X POST \
  'http://127.0.0.1:8000/api/themes/market-rows/capture?top_n=100&filters=value,gainers,losers&stex_tp=3&mrkt_tp=000'
```

집계 조회 예:

```bash
curl 'http://127.0.0.1:8000/api/themes/market-aggregates/latest?limit=5'
```

## CSV 산출물

저장 디렉터리:

```text
data/theme_market_rows/
```

현재 생성되는 파일:

```text
data/theme_market_rows/latest.json
data/theme_market_rows/latest_rows.csv
data/theme_market_rows/latest_aggregates.csv
data/theme_market_rows/{YYYY-MM-DD}/theme_market_rows_{YYYYMMDD_HHMMSS}.csv
data/theme_market_rows/{YYYY-MM-DD}/theme_market_aggregates_{YYYYMMDD_HHMMSS}.csv
```

### row CSV 스키마

```text
captured_at
trade_date
source
source_rank
symbol
name
price
change_pct
value_traded
stex_tp
mrkt_tp
top_n
```

예시:

```text
source=value
source_rank=1
symbol=000660
name=SK하이닉스
price=2298000
change_pct=4.41
value_traded=66524.51
```

`value_traded` 단위는 억원이다.

### aggregate CSV 스키마

```text
captured_at
trade_date
rank_by_value
rank_by_change
theme_id
theme_name
stock_count
matched_count
avg_change_pct
value_weighted_change_pct
sum_value_traded
top_value_traded
max_change_pct
min_change_pct
positive_count
negative_count
top_symbols
```

집계 의미:

```text
stock_count
  해당 테마 전체 구성 종목 수

matched_count
  이번 키움 랭킹 row에 잡힌 테마 구성 종목 수

avg_change_pct
  matched 종목의 단순 평균 등락률

value_weighted_change_pct
  거래대금 가중 평균 등락률

sum_value_traded
  matched 종목의 거래대금 합계, 억원

rank_by_value
  sum_value_traded 기준 순위

rank_by_change
  avg_change_pct 기준 순위
```

## 현재 검증 결과

최신 확인 시각:

```text
2026-07-08 09:47 KST
```

최신 캡처 메타:

```text
captured_at = 2026-07-08T09:47:00.600099+09:00
top_n = 100
filters = value,gainers,losers
row_count = 300
symbol_count = 280
aggregate_count = 29
```

최신 row CSV:

```text
data/theme_market_rows/2026-07-08/theme_market_rows_20260708_094700.csv
```

최신 집계 CSV:

```text
data/theme_market_rows/2026-07-08/theme_market_aggregates_20260708_094700.csv
```

거래대금 기준 상위 집계 예:

```text
rank_by_value = 1
theme_name = AI(인공지능)
stock_count = 152
matched_count = 17
avg_change_pct = 4.6112
value_weighted_change_pct = 2.827
sum_value_traded = 123116.04
top_symbols = 000660:SK하이닉스:4.36:66524.51 | 005930:삼성전자:0.34:48509.58 | 066570:LG전자:7.35:4490.2 ...
```

현재 개발 서버 상태:

```text
backend: 8000 정상
frontend: 3000 정상
WS: /ws/realtime 연결 정상
```

## 기존 동적 테마 발굴과의 관계

뉴스 기반 동적 테마 발굴은 별도 파이프라인이다.

```text
backend/core/themes/news_theme_discovery.py
POST /api/themes/discover
```

이 파이프라인도 키움 랭킹 row 개념을 사용한다.

```text
거래대금 top-N ∪ 등락률 top-N
뉴스 매칭
키워드 추출
애널리스트 분류
테마 DB 적재
```

다만 현재 신규 구현의 목적은 테마 발굴이 아니라 "원천 row 보관과 현재 테마별 통계 집계"다. 두 기능은 연결 가능하지만 지금은 분리되어 있다.

## 구현상 중요한 판단

### 개별 ticker 대량 조회를 피한다

테마 전체 종목을 대상으로 `ka10001`을 반복 호출하면 다음 문제가 생긴다.

```text
테마 수 x 테마별 종목 수 만큼 호출 증가
키움 429 rate-limit 증가
프론트 polling 지연
화면 렌더링 지연
```

그래서 테마 통계는 랭킹 row 기반으로 먼저 구현했다.

```text
거래대금상위 100
등락률상위 100
등락률하위 100
```

이 방식은 호출 수가 작고, 오늘 실제 수급이 몰린 종목만 테마 집계에 반영된다.

### CSV를 먼저 진실원천으로 둔다

DB에 바로 적재하지 않고 CSV를 먼저 남기는 이유:

```text
원천 row를 사람이 열어 검증 가능
키움 필드 단위/정규화 오류 추적 가능
집계 로직 수정 시 같은 row로 재계산 가능
운영 적용 전 데이터 품질 확인 가능
```

## 현재 한계

1. 테마 보드는 `latest_aggregates`의 `rank_by_value`로 카드 정렬을 수행한다.
   - 집계값 자체(`sum_value_traded`, `matched_count` 등)는 카드에 아직 표시하지 않는다.

2. 집계는 랭킹 row에 잡힌 종목만 반영한다.
   - 전체 테마 구성 종목 전수 시세가 아니다.
   - 장중 수급 테마 파악에는 적합하지만, 전체 테마 평균과는 다르다.

3. 복수 테마 종목은 각 테마에 중복 반영된다.
   - 예: SK하이닉스, 삼성전자가 AI, D램, DDR5 등에 동시에 기여할 수 있다.
   - 이는 현재 테마 구조상 의도된 동작이지만, 거래대금 총합 해석 시 중복을 고려해야 한다.

4. ETF/ETN row가 랭킹에 포함될 수 있다.
   - 예: KODEX, TIGER 계열
   - 테마 DB에 해당 symbol이 없으면 집계에는 들어가지 않지만, row CSV에는 남는다.
   - 향후 필요 시 `ETF 제외 필터`를 별도 옵션으로 추가할 수 있다.

5. CSV 산출물이 계속 누적된다.
   - 정규장 중 60초마다 파일이 생성된다(09:00~09:05 개장 유예 제외).
   - 장중 6.5시간 기준 약 390개 row CSV와 aggregate CSV가 생긴다.
   - 보관 기간/압축/정리 정책이 필요하다.

## 다음 구현 권장 순서

1. 테마 카드 헤더에 집계값 표시
   - `sum_value_traded`
   - `avg_change_pct`
   - `value_weighted_change_pct`
   - `matched_count / stock_count`

2. 집계 기반 테마 상세 페이지 보강
   - 상위 기여 종목 `top_symbols`
   - 거래대금 기여도
   - 상승/하락 종목 수

3. CSV retention 정책 추가
   - 예: 최근 5거래일만 보관
   - 또는 장 종료 후 일별 gzip 압축

4. DB 적재 여부 결정
   - CSV 검증 후 필요하면 `theme_market_rows`, `theme_market_aggregates` 테이블 추가
   - 단기 운영은 CSV만으로 충분하다.

5. 뉴스 발굴 파이프라인과 연결
   - 공통 `fetch_ranking_rows`/`merge_symbol_rows` 로직 재사용은 완료했다.
   - 저장된 최신 CSV를 `discover_dynamic_themes`의 후보 유니버스로 직접 재사용
   - 장중 반복 뉴스 분석 비용을 줄일 수 있다.

## 검증 커맨드

수동 캡처:

```bash
curl -X POST \
  'http://127.0.0.1:8000/api/themes/market-rows/capture?top_n=100&filters=value,gainers,losers&stex_tp=3&mrkt_tp=000'
```

최신 row 확인:

```bash
curl 'http://127.0.0.1:8000/api/themes/market-rows/latest?limit=3'
```

최신 테마 집계 확인:

```bash
curl 'http://127.0.0.1:8000/api/themes/market-aggregates/latest?limit=5'
```

CSV 직접 확인:

```bash
head -n 5 data/theme_market_rows/latest_rows.csv
head -n 5 data/theme_market_rows/latest_aggregates.csv
```

테스트:

```bash
.venv/bin/python -m pytest backend/tests/test_theme_market_row_store.py -q
.venv/bin/python -m py_compile \
  backend/core/themes/market_row_store.py \
  backend/core/scheduler/theme_market_row_jobs.py \
  backend/api/routes/themes_calendar_news.py \
  backend/core/gateway/kiwoom_quotes.py
```

2026-07-10 검토에서는 프로젝트 가상환경으로 관련 테스트 69개와 프론트 프로덕션
빌드를 통과했다. 시스템 `python3`에는 `pytest-asyncio`가 없으므로 테스트는 반드시
`.venv/bin/python`으로 실행한다.
