# BAR-OPS-11 — 당일 주도주 자동 선정 + 다종목 시뮬

## 사용자 요구
> 시뮬레이션 진행시에 특정 종목을 설정하고 시뮬레이션 하면 안되고, **당일 기준 주도주 기준**으로 시뮬레이션 실행이 되어야해

## 산출
- `backend/core/gateway/kiwoom_native_rank.py`:
  - `KiwoomNativeLeaderPicker` — ka10032(거래대금상위) + ka10027(등락률상위) 결합 ranking
  - 점수: `0.6 × (1 - TV_rank/N) + 0.4 × (1 - FR_rank/N)`
  - 등락률 ≥ +1.0% 필터 (양봉 강세만), threshold 조정 가능
  - 종목코드 정규화 `005930_AL` → `005930` (`_AL`/`_NX` 통합거래소 마커 strip)
  - 가격 부호 `+276500` → 276500 abs 정규화
- `scripts/simulate_leaders.py` — 단일 `--symbol` 제거, 자동 선정 + 다종목 시뮬 + 통합 리포트
- `backend/tests/gateway/test_kiwoom_native_rank.py` — 8 cases

## 실 검증 (2026-05-08, mockapi.kiwoom.com)

### 일봉 시뮬 (top 5, 600 캔들 each)

| rank | symbol | name | 가격 | 등락률 | TVrk | FRrk | score |
|------|--------|------|------|--------|------|------|-------|
| 1 | 307950 | 현대오토에버 | 592,000 | +29.97% | 8 | 4 | 0.952 |
| 2 | 319400 | 현대무벡스 | 37,700 | +21.61% | 6 | 19 | 0.934 |
| 3 | 012330 | 현대모비스 | 507,000 | +14.84% | 10 | 31 | 0.886 |
| 4 | 001440 | 대한전선 | 72,300 | +12.79% | 9 | 42 | 0.870 |
| 5 | 277810 | 레인보우로보틱스 | 778,000 | +11.62% | 13 | 50 | 0.830 |

```
== 시뮬 실행 (5 종목 × 5 전략) ==
  307950 현대오토에버           candles= 600 trades=17  PnL= -10,105,300
  319400 현대무벡스            candles= 600 trades=20  PnL=    -117,730
  012330 현대모비스            candles= 600 trades=11  PnL=  +3,150,000
  001440 대한전선             candles= 600 trades=27  PnL=    +222,520
  277810 레인보우로보틱스         candles= 600 trades=16  PnL=  +3,150,000

  총 거래   : 91 건 / 총 PnL: -3,700,510 원
  swing_38: -3,700,510 (다른 전략은 진입 시그널 없음)
```

### 5분봉 시뮬 (top 5, min_flu=5%, 900 캔들 each)
```
  307950 현대오토에버           trades= 8  PnL=  +1,675,000
  319400 현대무벡스            trades=10  PnL=    +258,400
  012330 현대모비스            trades= 6  PnL=  +1,608,000
  001440 대한전선             trades= 0  PnL=          +0
  277810 레인보우로보틱스         trades= 0  PnL=          +0

  총 거래   : 24 건 / 총 PnL: +3,541,400 원
  gold_zone: +3,541,400 (5분봉에서는 단기 변동성 전략이 활성)
```

→ 일봉(swing_38) vs 5분봉(gold_zone) 시간단위별 활성 전략이 다른 정상 패턴.

## CLI

```bash
set -a; . ./.env.local; set +a

# 기본 — top 5, 일봉
python scripts/simulate_leaders.py

# 분봉 (5분 단위, 등락률 ≥ 5%)
python scripts/simulate_leaders.py --mode minute --tic-scope 5 --min-flu 5.0

# top 10
python scripts/simulate_leaders.py --top 10

# 일부 전략만
python scripts/simulate_leaders.py --strategies gold_zone,swing_38

# 등락률 필터 강화
python scripts/simulate_leaders.py --min-flu 10.0
```

## 점수 산정 검증 (단위 테스트)
- 4종목 fixture, n=4:
  - 005930 TV1·FR3 → 0.6×1.00 + 0.4×0.50 = **0.80**
  - 319400 TV2·FR2 → 0.6×0.75 + 0.4×0.75 = 0.75
  - 307950 TV3·FR1 → 0.6×0.50 + 0.4×1.00 = 0.70
  - 000660 +0.50% (필터 제외)
- 거래대금 가중(0.6) > 등락률 가중(0.4) → TV 1위가 최종 1위.

## 보안
- ✅ SecretStr 강제 (CWE-798)
- ✅ https-only base_url (CWE-918)
- ✅ 토큰 캐시 + 30min margin auto-refresh
- ✅ 에러 로그 토큰 마스킹 (CWE-532)
- ✅ rate limit 0.25s × 2 ranking 호출

## Tests
- 8 신규 / 회귀 **672 → 680 (+8)**, 0 fail

## 다음 — BAR-60 정식 통합
운영 b 트랙(`backend/core/theme/leader_picker.py`) 정식 구현 시:
- 체결강도(`ka10025`) 추가 신호로 점수 보강
- 테마/섹터 클러스터링과 결합 (BAR-59 + 60)
- 월 1회 weights grid search 자동화
