# Finup 테마 기능 분석 및 기초 데이터

분석 기준: 2026-07-08 00:30:40 KST 수집 스냅샷
소스 페이지:
- https://finance.finup.co.kr/lab/themelog
- https://finance.finup.co.kr/theme/3344

## 1. 기능 구조

Finup 테마 기능은 Next.js 클라이언트 화면에서 공개 JSON API와 SSE를 조합해 구성된다. HTML에는 실제 테마 데이터가 거의 없고, 정적 JS 청크가 아래 런타임 구성을 가진다.

| 영역 | 런타임/API | 역할 |
|---|---|---|
| 테마록 홈 | `POST /api/radar/themelog/capture-chart` | 현재 테마 맵 스냅샷. 기본 20개, `top=30`까지 확인 |
| 실시간 갱신 | `/api/realtime/v1/sse?types=3&app=Finance&keywordIdx=...` | 테마 및 가격 이벤트 스트림 |
| 다시보기 | `/api/radar/themelog/play/info`, `/api/radar/themelog/play` | 특정일 테마 변화를 시간축으로 재생하는 코드 경로 |
| 테마 상세 | `/api/radar/theme/summary` | 테마 설명, 순위, 평균 등락률, 최고/최저 등락률 |
| 관련 종목 | `/api/radar/theme/relation-stocks` | 테마 구성 종목과 편입 사유 |
| 유사 테마 | `/api/radar/theme/similarity` | 종목 겹침 기반 유사 테마 |
| 뉴스 | `/api/radar/themelog/news` | 테마별 관련 뉴스. 상세 화면에는 유사한 `/api/radar/news/theme` 경로도 존재 |
| 테마 포커스 | `/api/finance/contents?keywordIdx=...` | 테마 관련 자체 콘텐츠 |
| 등락률 차트 | `https://stockdata.finup.co.kr/embed/theme-chart?keywordIdx=...` | iframe 차트 |

홈 화면의 주요 UI 탭은 `실시간 테마로그`와 `테마로그 다시보기`다. 상세 화면은 `테마 등락률`, `관련 종목`, `테마 포커스`, `유사 테마`, `관련 뉴스`, `실시간 테마 순위`로 나뉜다.

## 2. 테마 분류 방식

### 2.1 테마록 랭킹/맵 분류

`capture-chart` 응답의 한 행이 하나의 테마 노드다. 주요 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `keywordIdx` | 테마 ID |
| `keyword` | 테마명 |
| `rank` | 테마 포인트 순위 |
| `score` | 테마 점수 |
| `percentage` | 트리맵 가중치. 사각형 크기에 사용 |
| `diff` | 테마 평균 등락률. 색상/정렬/툴팁에 사용 |
| `new` | 신규 테마 플래그 |
| `hot` | 핫 테마 플래그 |
| `captureItemIdx` | `yyyyMMddHHmmss` 형태의 스냅샷 시각 키 |
| `captureDT` | 스냅샷 시각 |

코드상 기본 `keywordCount`는 30개다. `capture-chart`는 `top=30` 요청을 받으면 30개를 반환한다. 화면 렌더링은 `Percentage`를 트리맵 크기로 쓰고, `Diff`를 등락률 표시로 쓴다. 기본 상태에서는 테마 배열을 `Diff` 기준으로 정렬하는 코드가 있으며, 숨겨진 개발 컨트롤에는 `Percentage` 기준 정렬 경로도 존재한다.

### 2.2 시간/모드 분류

홈 화면은 현재 시각 기준으로 `개장 전`, `장중`, `장마감` 배지를 계산한다. 별도 모드로는 다음 두 가지가 있다.

| 모드 | UI 값 | 설명 |
|---|---|---|
| Realtime | `ddlTypePlay=0` | SSE로 최신 테마/가격 스트림 반영 |
| Specific Date | `ddlTypePlay=1` | 특정일을 선택해 `play/info`, `play` 데이터로 타임라인 재생 |

### 2.3 상세 페이지 분류

상세 페이지는 테마를 다음 축으로 다시 나눈다.

| 분류 축 | 데이터 | 설명 |
|---|---|---|
| 요약 | `summary` | 테마 설명, 순위, 평균 등락률, 최고/최저 종목 등락률 |
| 관련 종목 | `relation-stocks` | 편입 종목, 현재가, 등락률, 테마 포함 사유 |
| 상승/하락 종목 | `relation-stocks.diff` | 상세 요약에서 양수/음수 개수로 집계 |
| 유사 테마 | `similarity` | 종목 겹침 기반으로 4개 표시 |
| 관련 뉴스 | `themelog/news` | 최신 뉴스 5개 수집 |
| 테마 포커스 | `finance/contents` | 자체 콘텐츠 2개 수집 |

`typeStock`은 API에는 숫자로 내려온다. 샘플 대조 결과 `1`은 `kospi`, `2`는 `kosdaq` 종목과 일치해 CSV에는 `type_stock_market_inferred` 컬럼으로 추론 라벨을 추가했다.

## 3. 3344 샘플 확인

사용자가 제공한 `https://finance.finup.co.kr/theme/3344`는 현재 스냅샷에서 `시멘트/레미콘` 테마로 수집됐다.

| 항목 | 값 |
|---|---|
| 테마 ID | `3344` |
| 테마명 | `시멘트/레미콘` |
| 순위 | 12 |
| 평균 등락률 | `13.57%` |
| 최고/최저 종목 등락률 | `29.93%` / `-9.87%` |
| 관련 종목 수 | 16 |
| 뉴스 수집 수 | 5 |
| 포커스 콘텐츠 수 | 2 |
| 유사 테마 | 재건축, 도로, 대북/남북경협, 대구경북신공항 |

상위 관련 종목 예시는 `서산(079650, +29.93%)`, `강동씨앤엘(198440, +13.27%)`, `모헨즈(006920, +8.92%)`다.

## 4. 생성 데이터

수집 스크립트:

```bash
python3 scripts/finance/collect_finup_theme_data.py --top 30 --capture-idx 10 --out-dir data/finup_theme --sleep 0.12
```

최신 산출물 인덱스:

```text
data/finup_theme/latest.json
```

최종 스냅샷 파일:

| 파일 | 행 수 | 설명 |
|---|---:|---|
| `data/finup_theme/finup_theme_snapshot_20260708_003040.json` | 30 themes | 원본+정규화 통합 JSON |
| `data/finup_theme/finup_themes_20260708_003040.csv` | 30 | 테마 랭킹/요약 |
| `data/finup_theme/finup_theme_stocks_20260708_003040.csv` | 1,908 | 테마-종목 매핑 |
| `data/finup_theme/finup_similar_themes_20260708_003040.csv` | 120 | 테마별 유사 테마 4개 |
| `data/finup_theme/finup_theme_news_20260708_003040.csv` | 150 | 테마별 뉴스 5개 |
| `data/finup_theme/finup_theme_focus_contents_20260708_003040.csv` | 40 | 테마별 포커스 콘텐츠 최대 2개 |

주의: repo의 `.gitignore`에 `data/*`가 있어 위 데이터 파일은 Git 추적 대상이 아니다. 작업공간에는 생성되어 있다.

## 5. 데이터 품질 메모

| 점검 항목 | 결과 |
|---|---|
| 테마 ID 유일성 | 30/30 유일 |
| 수집 오류 | 0건 |
| 스냅샷 시각 | 모든 테마 `2026-07-07 15:39:55.339011` |
| 신규/핫 플래그 | 신규 1개, 핫 0개 |
| 테마 등락률 범위 | `-16.20%` ~ `13.57%` |
| 종목 등락률 범위 | `-30.00%` ~ `30.00%` |
| 테마별 종목 수 | 최소 5개, 최대 205개 |
| 종목 시장 추론 | KOSPI 572행, KOSDAQ 1,336행 |
| 거래량/거래대금 | 현재 수집 응답에서는 종목 `volume`, `valueSum` 및 테마 거래대금이 전부 0 |
| 설명 결측 | 테마 설명 1/30 결측, 종목 편입 사유 23/1,908 결측 |
| 뉴스 요약 결측 | 2/150 결측 |

거래량과 거래대금은 현재 API 응답에서 모두 0으로 내려오므로 바로 팩터로 쓰면 안 된다. 현재 기초 데이터에서 신뢰하기 좋은 값은 테마/종목 식별자, 테마명, 종목명, 등락률, 랭킹, 편입 사유, 유사 테마 관계, 뉴스/콘텐츠 링크다.
