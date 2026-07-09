# 테마 구현 문제점 분석 및 수정 설계

작성일: 2026-07-08 (오전 정규장 중)
대상: `2026-07-08-theme-data-implementation-analysis.md`(이하 "원본 문서") 검증 + 원본 문서가 다루지 않은 병행 변경 사항 분석
전제: 아래 0~3절은 2026-07-08 당시 main 워킹트리의 미커밋 상태를 분석한 기록이다.
2026-07-10 검토와 구현 결과는 4절에 정리했다.

## 0. 조사 방법

- 원본 문서 원문 정독
- `git -C <main> status/diff` 로 미커밋 변경분 전수 확인 (committed 3건 + uncommitted 다수)
- 신규 파일 3종(`market_row_store.py`, `theme_market_row_jobs.py`, `test_theme_market_row_store.py`) 전문 읽기
- 수정 파일 4종(`themes_calendar_news.py`, `kiwoom_quotes.py`, `theme_board_cache_jobs.py`, `scheduler.py`) diff 검토
- 실행 중인 백엔드(:8000)에 대해 `curl`로 실제 동작 검증(문서에 없는 회귀를 여기서 발견)
- `finup_importer.py`/`collect_finup_theme_data.py` 확인

## 1. 원본 문서 검증 결과 — 정확함

원본 문서가 설명하는 3계층 구조, `market_row_store.py`의 5개 함수(`fetch_ranking_rows`/`merge_symbol_rows`/`aggregate_theme_memberships`/`load_theme_memberships`/`capture_theme_market_rows`), CSV 스키마, 신규 API 3종(`POST /market-rows/capture`, `GET /market-rows/latest`, `GET /market-aggregates/latest`)은 코드와 실측 결과 모두 문서 내용과 일치한다. "현재 한계" 5개 항목(①프론트 미연동 ②랭킹 미매칭 종목 누락 ③복수테마 중복 ④ETF row 미필터 ⑤CSV 무제한 누적)도 정확한 자가진단이다.

**다만 이 문서는 "같은 커밋 작업 세트 안에서 동시에 일어난 다른 변경"을 다루지 않는다. 그 변경들이 실제로는 더 시급한 문제다.**

## 2. 원본 문서가 다루지 않은 발견 사항

### 2-A. [P0] 테마 상세 종목 라이브 시세 표시 — 회귀 발생 (실측 확인)

배경: 이전 세션에서 사용자가 명시적으로 요청("거래대금 등락율... 표시 안되고 있으니 구현해줘")해 구현하고 검증했던 기능이 이번 병행 작업으로 **인지되지 않은 채 비활성화됨**.

변경 내용(`themes_calendar_news.py`, `theme_board_cache_jobs.py` diff):

```text
GET /api/themes/{id}/stocks 에 enrich 쿼리파라미터 추가, 기본값 False로 변경
  - 이전: 항상 enrich=True 로 fetch_theme_stocks 호출
  - 이후: enrich 파라미터 없으면 enrich=False (DB score 스냅숏만 반환)

theme_board_cache_jobs._refresh_all_themes()
  - 이전: fetch_theme_stocks(t.id, enrich=True) 하드코딩
  - 이후: enrich=_live_enrich_enabled() → BARRO_THEME_BOARD_CACHE_ENRICH 플래그,
          기본값 "0"(OFF)
```

**실측 검증** (`curl http://127.0.0.1:8000/api/themes/55/stocks`):

```json
{"symbol":"301300","score":9.97,"change_pct":9.97,"price":null,"value_traded":null}
```

같은 종목 `?enrich=true`:

```json
{"symbol":"301300","score":9.97,"change_pct":-5.67,"price":1415.0,"value_traded":0.75}
```

**문제**: 기본 경로가 `change_pct`로 **테마 시드 시점의 score(9.97%)를 그대로 노출**하는데, 이 값은 며칠 전(또는 Finup 스냅숏 수집 시점) 값일 수 있어 오늘자 실제 등락률(-5.67%)과 정반대 방향으로 오인시킬 수 있다. `price`/`value_traded`는 아예 null이라 화면에 빈칸/0으로 보인다.

프론트(`frontend/lib/api.ts`, `themes/page.tsx`)는 `getThemeStocks(theme.id)`를 `enrich` 인자 없이 호출하므로 **현재 프론트가 렌더링하는 값은 기본 경로(비enrich)다** — 즉 지금 대시보드에 접속하면 라이브 가격 없이 오래된 score만 보인다.

**설계**:

1. 즉시(운영) 완화: `.env.local`에 `BARRO_THEME_BOARD_CACHE_ENRICH=1` 추가 — 배경잡이 다시 라이브 enrich를 수행하도록 복원. 이 잡은 이미 동시조회 세마포어(5)·종목별 20s 캐시가 있어 API 부하는 통제됨(§2-C 참조, 단 완전 무해하진 않음).
2. 근본 수정: `get_theme_stocks` 라우트가 캐시 미스일 때 `enrich` 파라미터와 무관하게 **한 번은 백그라운드 잡의 최신 캐시를 우선 사용**하도록 순서를 바꾼다 — "쿼리파라미터로 껐다 켰다" 이원화 대신, "표시용 캐시(잡이 채움, 항상 enrich) vs 온디맨드 상세조회(?enrich=true, 캐시 미스 시 즉시 1회)"로 책임을 분리. 즉:
   - `_THEME_STOCKS_CACHE`는 항상 잡이 enrich=True로 채운다(기존 6aafb36 방식으로 되돌림).
   - `enrich=true` 쿼리는 "캐시 무시하고 지금 당장 재조회"라는 의미로만 쓴다(캐시 신선도 우회 강제 새로고침 버튼용).
   - `enrich` 미지정 시에도 **DB score가 아니라 캐시(있으면)를 우선** 반환 — 지금 코드도 이렇게 되어 있으나 잡 자체가 enrich=False로 채우고 있어 캐시에도 애초에 라이브 값이 없다.
3. 프론트가 "score-only(빠름, 방향성 참고용)"와 "라이브(정확, 약간 느림)"를 구분해서 보여줘야 한다면, 응답에 `is_live: bool` 필드를 추가해 프론트가 "지연 시세" 배지를 표시하게 한다(날조 금지 원칙과 일치 — 값이 오래됐음을 사용자에게 숨기지 않음).

### 2-B. [P0] 테마 마스터 데이터 전면 교체 — 조율 없는 wipe-and-replace

`backend/core/themes/finup_importer.py:142-144`:

```python
await db.execute(text("DELETE FROM theme_stocks"))
await db.execute(text("DELETE FROM theme_keywords"))
await db.execute(text("DELETE FROM themes"))
```

**실측**: 현재 `GET /api/themes` 는 30개 테마를 반환하며, 기존 큐레이션 21종(반도체·HBM·바이오·자동차·AI·금융 등, `theme_map.json` 기반)은 "방산" 1개를 빼고 전부 사라졌다. Finup(`finance.finup.co.kr`, 외부 테마 서비스) 스냅숏으로 완전 치환된 것이다.

**문제**:
1. 이 세션에서 구현한 `news_theme_discovery.py`(뉴스기반 신규 테마 발굴, 갭필링)가 만든 테마/키워드도 함께 삭제되었을 것이다 — 두 기능 모두 `themes`/`theme_stocks`/`theme_keywords` 를 공유하는데, 하나는 "전면 교체가 진실원천", 다른 하나는 "기존 위에 없는 것만 추가"라는 상반된 데이터 수명주기 가정을 갖고 있다. 조율 로직이 전혀 없다.
2. 다행히 `import_finup_theme_snapshot` 은 스케줄러에 등록되어 있지 않다(수동 실행 전용) — 그래서 지금 당장 반복적으로 데이터가 날아가진 않는다. 하지만 향후 이걸 주기 잡으로 승격하면, 뉴스발굴 결과가 매번 삭제된다.

**설계**:
1. **단기**: `finup_importer.py`에 `description` 컬럼으로 출처를 구분(`"finup_snapshot"` vs `"news_discovery"` vs `"curated_seed"`)하고, DELETE 시 `WHERE description = 'finup_snapshot'` 조건을 걸어 다른 출처의 row는 보존한다. 현재 `description` 필드가 이미 `ThemeRepository.upsert_theme(name, description=...)` 에 존재하므로 스키마 변경 없이 가능.
2. **중기**: 세 데이터 소스(curated seed / Finup 스냅숏 / 뉴스발굴)의 소유권을 명시적으로 분리 — 예를 들어 `themes.source` 컬럼 추가(마이그레이션 필요) 후 각 파이프라인이 자기 source만 지운다.
3. `theme_refresher.py`(큐레이션 시드)와 `finup_importer.py`가 동시에 존재하는 이유/우선순위를 문서화 — 지금은 "Finup이 사실상 메인, 큐레이션 21종은 폐기"로 보이는데 이게 의도된 결정인지 확인 필요(사용자 확인 권장).

### 2-C. [P1] 백그라운드 잡 3~4개가 동시에 키움 REST를 두드림 — 오늘 새벽 장애의 개연성 있는 원인

현재(또는 켜질 수 있는) 배경잡 목록과 주기:

```text
theme_board_cache_jobs       120s   ka10001 (종목별, enrich=True 일 때)
theme_market_rows_capture     60s   ka10032 + ka10027 (신규, 기본 ON)
news_collector_tick           60s   RSS만(키움 아님, 기본 OFF)
theme_discovery                30분  ka10032 + ka10027 + RSS매칭(기본 OFF)
intraday_buy_daemon(실거래)    -     ka10081 등 (실제 매매용, 항상 ON, 최우선)
```

**실측 근거**: 오늘 새벽 02:30~02:33 사이 `theme_board_cache_jobs`가 SQLite `database is locked` 에러로 9회 연속 실패 후 백엔드가 재시작됨(정상적인 graceful shutdown 시퀀스, 크래시 아님 — 아마 다른 세션이 반복적으로 코드를 바꾸며 수동 재시작했을 가능성이 높음). 09:15~09:19에는 `SignalScanner`가 시장개장과 함께 다수 종목을 스캔하며 `ka10001 429` 가 연쇄 발생했고, 그 직후에도 실거래 데몬 쪽 DCA 매수 주문이 `429`로 실패했다가 재시도로 복구된 사례가 있었다(마녀공장 439090).

**문제**: 대시보드용 배경잡들이 실거래 데몬과 **동일한 키움 계정 레이트리밋 예산**을 공유한다. 지금은 각 잡에 세마포어/캐시가 있어 개별적으로는 안전하지만, 여러 잡이 겹치는 시점(예: 장 시작 직후 다수 잡이 동시에 첫 사이클을 도는 순간)에는 실거래 주문 API 호출까지 429 확률이 올라간다. 실거래 매수 주문 실패는 결과적으로 재시도로 복구되긴 했으나, **대시보드 편의 기능이 실거래 신뢰성에 영향을 주는 구조는 우선순위가 잘못됐다.**

**설계**:
1. 대시보드 계열 배경잡(테마보드 캐시, 랭킹 row 캡처, 뉴스수집/발굴)에 **장 시작 직후 5분(09:00~09:05) 유예 구간**을 추가 — 이 시간대는 실거래 데몬의 개장 스캔이 가장 민감하다. `intraday_buy_daemon.py`의 `CLOSE-RUSH-YIELD`(장마감 직전 양보) 패턴을 참고해 `OPEN-RUSH-YIELD` 형태로 대칭 구현.
2. 가능하면 키움 REST 클라이언트 레벨에 **우선순위 큐**를 두어, 실거래 주문/포지션 관리 호출이 대시보드용 조회 호출보다 항상 먼저 처리되게 한다(간단하게는 대시보드 잡들이 사용하는 `KiwoomQuotes` 인스턴스에 더 보수적인 `rate_limit_seconds`를 주는 것만으로도 완화 가능 — 현재 각 잡이 기본값으로 별도 인스턴스를 만들고 있어 인스턴스 간 레이트리밋 조율이 안 됨).
3. `theme_market_rows_capture`(60s)와 `theme_board_cache_jobs`(120s)는 **같은 시장 데이터를 별도 호출**한다. `theme_board_cache_jobs`가 개별 `ka10001`(종목당 1회)을 쓰는 반면 `market_row_store`는 `ka10032`/`ka10027`(전체 한 번에), TR이 달라 완전한 중복은 아니지만, §2-D의 통합안으로 호출 총량을 줄일 수 있다.

### 2-D. [P2] 두 파이프라인(뉴스발굴 vs 랭킹row집계) 후보 유니버스 로직 중복

`news_theme_discovery.build_candidate_universe()`와 `market_row_store.fetch_ranking_rows()`는 **거의 동일한 로직**(거래대금 top-N ∪ 등락률 top-N, `KiwoomQuotes.ranking()` 호출)을 각자 구현하고 있다. 원본 문서도 "다음 구현 권장 순서 §6"에서 이 통합을 이미 제안했다 — 동의한다.

**설계**: `market_row_store.fetch_ranking_rows()`를 공용 함수로 승격하고 `news_theme_discovery.build_candidate_universe()`가 이를 재사용하도록 리팩터링. 단, `news_theme_discovery`는 `min_value_traded_eok` 필터가 있고 `market_row_store`는 없다는 차이가 있으므로 필터를 옵션 파라미터로 유지한 채 통합.

## 3. 원본 문서의 "현재 한계" 우선순위 재정리 (실행 순서 권고)

문서의 "다음 구현 권장 순서"는 이미 합리적이나, 위 §2 발견사항을 반영해 순서를 재조정한다.

| 순위 | 항목 | 근거 |
|---|---|---|
| 1 | §2-A 라이브가격 회귀 원복(`BARRO_THEME_BOARD_CACHE_ENRICH=1` + 캐시 우선순위 재정리) | 사용자가 이미 요청·검증했던 기능의 무의식적 회귀. 최우선 |
| 2 | §2-B Finup wipe 범위를 source별로 제한 | 다음 Finup 재수입 때 뉴스발굴 데이터가 통째로 사라지는 걸 방지 |
| 3 | §2-C 개장 직후 대시보드 잡 유예 | 실거래 신뢰성 문제로 격상 — 오늘 실제로 DCA 주문 429 재시도 사례 발생 |
| 4 | 원본 §1 프론트 `market-aggregates` 연동 | 원본 문서 그대로 |
| 5 | 원본 §2~3 테마 카드/상세 집계값 표시 | 원본 문서 그대로 |
| 6 | §2-D 랭킹 유니버스 로직 통합 | 원본 §6과 동일 취지, 구체화 |
| 7 | 원본 §4 CSV retention | 원본 문서 그대로(리스크 낮음, 디스크만 영향) |
| 8 | 원본 §5 DB 적재 여부 결정 | 원본 문서 그대로, 급하지 않음 |
| 9 | ETF/ETN row 필터 | 원본 문서 그대로, 급하지 않음 |

## 4. 2026-07-10 검토 및 구현 결과

| 항목 | 결과 |
|---|---|
| §2-A 라이브 시세 회귀 | 캐시 잡의 enrich 기본값을 ON으로 복원하고, 조회 상한 8·주기 120초·캐시 TTL 180초로 조정 |
| §2-B Finup 전면 삭제 | `theme_keywords` 소유 마커로 Finup 생성 테마만 교체. 이름이 겹치는 기존 테마는 설명·소유권·종목을 보존 |
| §2-C 개장 직후 부하 | 공용 `is_open_rush`를 적용. 랭킹 CSV 잡은 정규장 밖에서도 실행하지 않음 |
| §2-D 후보 로직 중복 | 뉴스 발굴이 `fetch_ranking_rows`와 `merge_symbol_rows`를 재사용 |
| 프론트 집계 연동 | KST 거래일이 오늘인 경우에만 `rank_by_value` 정렬 적용. 오래된 집계/API 실패 시 기존 등락률 정렬로 강등 |
| 집계 정확도 | 거래대금 가중 등락률 분모를 등락률·거래대금이 모두 있는 행으로 제한 |
| 최신 스냅숏 안정성 | 키움 빈 응답 시 마지막 정상 CSV와 `latest.json`을 보존 |

검증 결과:

- 관련 백엔드 테스트 69개 통과
- 전체 백엔드 테스트 1,892개 통과, 10개 skip, 기존 비관련 실패 3개
- 변경 Python 모듈 `py_compile` 통과
- Next.js 프로덕션 빌드와 TypeScript 검사 통과

`data/barro_trade.db`는 뉴스 100건 등 실행 중 데이터가 추가된 런타임 상태이므로 기능
커밋에 포함하지 않는다. 파일을 되돌리거나 삭제하지 않고 로컬 상태를 보존한다.
