# 🍉 주식단테 수박지표 당일 자동매매 시스템

주식단테의 **역매공파 이론**에서 파생된 파란점선 + 수박지표를 활용한 **데이트레이딩** 시스템.
키움 REST API 기반으로 당일 종목 산출 → 자동 매수 → **15시 전 전량 청산**.

## 시스템 흐름

```
08:30  전종목 스캔 → 파란점선 돌파 임박 종목 산출
09:00  장 시작
09:05  매수 가능 시작 (초반 5분 노이즈 회피)
       ├─ 파란점선 돌파 + 거래량 3배 폭증 → 매수
       ├─ +3% 도달 → 50% 익절
       ├─ +5% 도달 → 전량 익절
       └─ -2% 도달 → 전량 손절
14:30  신규 매수 마감
14:50  ⚠️ 강제 청산 (전 포지션 시장가 매도)
15:10  일일 리포트 텔레그램 발송
```

## 빠른 시작

### 1. 사전 준비
- 키움증권 계좌 + REST API 신청 (openapi.kiwoom.com)
- 텔레그램 봇 생성 (@BotFather)
- Python 3.10+

### 2. 설치

```bash
git clone <this-repo>
cd ai-trade

pip install -r requirements.txt

cp config/.env.example config/.env
# config/.env 에 API 키 입력
```

### 3. 실행

```bash
# 모의투자 (기본)
python main.py --mode simulation

# 스캔만 (매매 없음, 종목 확인용)
python main.py --scan-only

# 실거래 (⚠️ 충분한 모의투자 검증 후)
python main.py --mode live
```

## 프로젝트 구조

```
ai-trade/
├── SKILL.md                  ← OpenClaw 에이전트 스킬 정의
├── HEARTBEAT.md              ← 자율 스케줄 (하트비트)
├── main.py                   ← 메인 실행 파일
├── requirements.txt
│
├── config/
│   ├── settings.yaml         ← 모든 파라미터 (지표, 매매, 리스크)
│   └── .env.example          ← API 키 템플릿
│
├── scanner/                  ← 종목 스캐닝
│   ├── indicators.py         ← 파란점선 + 수박지표 계산 엔진
│   └── daily_screener.py     ← 장 시작 전 전종목 스캔
│
├── strategy/                 ← 매매 전략
│   ├── entry_signal.py       ← 매수 조건 (돌파+거래량+양봉)
│   └── exit_signal.py        ← 매도 조건 (익절/손절/강제청산)
│
├── execution/                ← 주문 실행
│   ├── kiwoom_api.py         ← 키움 REST API 클라이언트
│   └── order_manager.py      ← 주문 관리 + 포지션 추적
│
├── monitoring/               ← 모니터링
│   ├── telegram_bot.py       ← 텔레그램 알림
│   └── daily_report.py       ← 일일 리포트 생성
│
└── docs/
    └── INDICATOR_SPEC.md     ← 지표 수식 상세 문서
```

## 핵심 지표

### 파란점선
```
파란점선 = Highest(High, 224) - ATR(224) × 2.0
```
224일 최고가에서 장기 평균 변동폭의 2배를 뺀 값.
이 선 위로 주가가 올라오면 세력의 개입을 시사.

### 수박지표
```
수박 = (거래량 > 20일평균 × 2.5) AND (변동폭 > ATR14 × 1.5) AND (종가 < MA224 × 1.1)
```
거래량 폭증 + 캔들 확장 + 바닥권의 3중 조건.
세력이 돈을 쓴 흔적. 발생 캔들의 중심값 = 세력 평단가.

## 리스크 관리

| 항목 | 설정값 |
|------|--------|
| 종목당 최대 비중 | 총자산의 10% |
| 동시 최대 종목 수 | 5종목 |
| 총 투자 비중 상한 | 50% |
| 손절 | -2% |
| 익절 1차 | +3% (50% 매도) |
| 익절 2차 | +5% (전량 매도) |
| 강제 청산 | 14:50 시장가 전량 |
| 일일 손실 한도 | -5% |

## 캔들 데이터 캐시

팀 에이전트 상승 예측 및 역매공파 스캔은 로컬 캐시(일봉 OHLCV)를 사용합니다.
캐시는 용량(~300MB) 문제로 `.gitignore`에 의해 git 추적 제외 상태이며, 별도로 동기화해야 합니다.

### 캐시 경로

```
ai-trade/
└── data/
    └── ohlcv_cache/
        ├── meta.json        ← 마지막 업데이트 날짜 및 통계
        ├── 005930.json      ← 종목별 일봉 데이터 (삼성전자 예시)
        └── ...              ← 전종목 JSON 파일 (~2,960개)
```

### 캐시 업데이트

```bash
# 전종목 일봉 데이터 동기화 (장 마감 후 실행, 약 35분 소요)
python3 main.py --update-cache
```

- 키움 ka10081 API로 종목별 누락 구간만 증분 조회
- 기존 캐시와 자동 병합 (중복 제거 + 날짜 정렬)
- 완료 시 텔레그램 알림 발송

### 자동 업데이트 (cron)

매일 장 마감 후 15:30에 자동 실행되도록 cron 등록:

```bash
bash scripts/setup_cron.sh
```

### 신규 환경 셋업 시

```bash
git clone <this-repo>
cd ai-trade
pip install -r requirements.txt
cp config/.env.example config/.env   # API 키 입력
python3 main.py --update-cache       # 캐시 최초 구축 (~35분)
```

## ⚠️ 면책조항

이 시스템은 교육 및 연구 목적으로 제작되었습니다.
실제 투자에 사용할 경우 발생하는 모든 손실에 대한 책임은 사용자에게 있습니다.
반드시 **모의투자로 충분히 검증**한 후 실거래에 적용하시기 바랍니다.
