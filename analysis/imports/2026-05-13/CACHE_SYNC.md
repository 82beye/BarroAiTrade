# OHLCV 캐시 동기화 — 2026-05-13

**소스:** `~/Downloads/ohlcv_cache_20260513.tar.gz` (29 MB)
**대상:** `data/ohlcv_cache/` (153.9 MB / 2,967 JSON + `meta.json`)
**동기화 일시:** 2026-05-14

## 운영 머신 동기화 메타 (`data/ohlcv_cache/meta.json`)

```json
{
  "updated": "2026-05-13",
  "count": 2916,           # 처리 성공
  "total_requested": 2966, # 요청 총수
  "failed": 8,             # 운영 머신에서 fetch 실패
  "skipped": 42,           # 정책 스킵
  "new_days_added": 169545,
  "elapsed_seconds": 2605.3,
  "api_method": "ka10081"
}
```

## 커버리지

| 항목 | 값 |
|------|---:|
| 종목 파일 | **2,967** (+ `meta.json`) |
| 총 디스크 | 153.9 MB |
| 최신 캔들 = 2026-05-13 | **2,958 (99.6%)** |
| STALE (이전 일자) | 8 |
| 운영 종목 cover | **8/10** |

## STALE 8건 (latest != 2026-05-13)

| symbol | latest | bars | 추정 |
|--------|--------|-----:|------|
| 138490 | 2026-03-25 | 515 | 거래정지·관리종목 |
| 152550 | 2026-04-08 | 511 | 거래정지 |
| 394350 | 2026-03-04 | 500 | 동일 일자 정지 그룹 |
| 441330 | 2026-03-04 | 500 | 〃 |
| 452670 | 2026-03-04 | 500 | 〃 |
| 452980 | 2026-03-04 | 500 | 〃 |
| 460280 | 2026-03-04 | 500 | 〃 |
| 473370 | 2026-03-05 | 485 | 동일 시점 정지 |

→ 운영 머신 `meta.json.failed=8` 과 일치. 대부분 상장폐지·거래정지로 추정되어 정상 fetch 실패. **재시도 의미 없음**(별도 정리 작업 필요 시 시뮬·후보 풀에서 제외 정책 권장).

## 최근 운영 종목 캐시 매칭

| symbol | name | 캐시 | 비고 |
|--------|------|:----:|------|
| 319400 | 현대무벡스 | ✅ 600봉 (2023-11-22 ~ 2026-05-13) | |
| 066570 | LG전자 | ✅ 600봉 | |
| 090710 | 휴림로봇 | ✅ 600봉 | |
| 010170 | 대한광통신 | ✅ 600봉 | |
| 003280 | 흥아해운 | ✅ 600봉 | |
| 012200 | 계양전기 | ✅ 600봉 | |
| 356680 | 엑스게이트 | ✅ 600봉 | |
| 012860 | 모베이스전자 | ✅ 600봉 | |
| **439960** | 코스모로보틱스 | ❌ **누락** | 신규 상장(?) — morning.log 5/12·5/13 모두 "캔들 부족 (2~3 < 31), 스킵" 흔적과 일치 |
| 252670 | KODEX 200선물인버스2X | ❌ 누락 | ETF/선물 — 캐시 정책에 포함 안 될 가능성 |

## 즉시 가능한 동기화 확인 명령

```bash
cd /Users/beye/workspace/BarroAiTrade

# 종목별 최신 일자 일괄 점검
./venv/bin/python -c "
import json
from pathlib import Path
from collections import Counter

c = Counter()
for p in sorted(Path('data/ohlcv_cache').glob('*.json')):
    if p.name == 'meta.json': continue
    try:
        d = json.loads(p.read_text())['data']
        if d: c[max(x['date'] for x in d)] += 1
    except: c['ERROR'] += 1
for d, n in sorted(c.items(), reverse=True)[:5]:
    print(f'{d}: {n}')
"

# 특정 종목 최신/오래된 봉
./venv/bin/python -c "
import json; d = json.loads(open('data/ohlcv_cache/319400.json').read())['data']
print('bars:', len(d), 'range:', min(x['date'] for x in d), '~', max(x['date'] for x in d))
"
```

## 후속 작업 (제안)

| # | 작업 | 우선순위 |
|---|------|---------|
| 1 | 시뮬 시 STALE 8건 자동 제외 — `policy.json` 에 `excluded_symbols` 또는 `min_latest_date` 정책 | 🟡 |
| 2 | 코스모로보틱스(439960) 같은 신규 상장 종목을 위한 별도 fetch 경로 | 🟢 |
| 3 | 캐시 동기화 자체를 로컬에서 실행할 수 있는 CLI (`scripts/sync_ohlcv_cache.py`) — 현재는 운영 머신에서만 갱신됨 | 🟢 |
| 4 | meta.json 기반 cache 헬스체크 — backend.api 또는 telegram 명령 `/cache_status` | 🟢 |

## 디렉토리 정책 (재확인)

- `data/ohlcv_cache/` — `.gitignore:63 data/` 에 의해 자동 제외 ✅
- `data/` 자체가 ignored 이므로 캐시 추가 시 git 영향 없음
- meta.json은 운영 머신 동기화 결과 메타데이터 — 보존 가치 있음
