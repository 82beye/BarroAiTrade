# BAR-49: Telegram 일일 성과 리포트 스케줄러 구현

**상태**: ✅ 구현 완료  
**작성일**: 2026-05-16  
**대상 시간**: 매일 18:00 KST

---

## 개요

BarroAiTrade 시스템의 모의투자 일일 성과를 매일 오후 6시(18:00 KST)에 Telegram 채널로 자동 발송하는 스케줄러를 구현했습니다. BAR-44 스펙을 준수하며 포트폴리오 통계, 전략별 성과, 상위 성과자, 위험 신호를 포함합니다.

---

## 구현 내용

### 1. 스케줄러 시간 변경
**파일**: `scripts/finance/telegram_integration/scheduler.py`

```python
# 기존: 매일 09:00 KST
# 변경: 매일 18:00 KST (거래 종료 1시간 후)
CronTrigger(hour=18, minute=0, timezone="Asia/Seoul")
```

**변경사항**:
- ✅ CronTrigger hour를 9에서 18로 변경
- ✅ 로그 메시지 업데이트
- ✅ 주석 업데이트 (거래 종료 1시간 후 설명 추가)

---

### 2. 일일 리포트 포매터 (신규)
**파일**: `scripts/finance/telegram_integration/daily_report_formatter.py`

BAR-44 스펙을 정확히 구현한 리포트 포매터:

#### 주요 기능
- **날짜/요일 표시**: YYYY-MM-DD (요일)
- **성과 요약**: 
  - 포트폴리오 수익률
  - 일일 손익액
  - 참여 사용자 수
  - 평균 수익률
- **전략별 성과**: 
  - 블루라인(돌파) - 수익률, 거래건수, 승률
  - F존(모멘텀) - 수익률, 거래건수, 승률
- **TOP 3 성과자**: 닉네임, 수익률, 상위 거래 종목
- **경고 메시지**: 위험 신호 및 주의사항
- **내일 전망**: 주목 종목, AI 분석 포인트

#### 메시지 예시
```
📊 BarroAiTrade 일일 성과 리포트
═══════════════════════════════════
📅 2026-05-16 (토)

🎯 오늘의 성과
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 전체 포트폴리오 수익률: +2.45%
💰 일일 손익액: +125,000 원
👥 참여 사용자: 150 명
📊 평균 수익률: +1.85%

[... 전략별 성과, TOP 3, 경고, 전망 ...]

💬 공유하기 | 📱 앱 열기 | 🎯 전략 수정
```

---

### 3. 리포트 서비스 확장
**파일**: `backend/core/monitoring/report_service.py`

#### 신규 메소드

**`build_comprehensive_daily_report()`**
- 거래 내역과 활성 사용자 수로부터 comprehensive 리포트 데이터 생성
- BAR-44 포매터가 사용할 데이터 구조 반환
- 전략별 통계 자동 계산

**`_calc_strategy_stats()`**
- 전략별 수익률, 거래 건수, 승률 계산
- 빈 거래 시 기본값 반환

**`_get_top_performers()`**
- TOP 3 성과자 추출
- 현재: 시뮬레이션 데이터 (실제 사용자 데이터 연동 가능)
- TODO: 실제 사용자별 수익률 데이터 통합

#### `send_daily_report()` 개선
- Comprehensive 리포트 자동 감지
- `telegram.send_raw_message()` 사용 (포매팅 유지)
- Telegram 비활성화 시 로그 저장

---

### 4. Telegram 봇 확장
**파일**: `backend/core/monitoring/telegram_bot.py`

#### 신규 메소드: `send_raw_message()`
```python
async def send_raw_message(self, text: str) -> None:
    """형식된 텍스트 직접 전송 (emoji/title 자동 추가 없음)"""
    # Markdown 형식 그대로 전송
    # 리포트 포매터의 복잡한 구조 보존
```

**용도**: BAR-44 스펙 리포트의 시각적 형식 보존

---

### 5. 스케줄러 작업 업데이트
**파일**: `scripts/finance/telegram_integration/scheduler.py`

#### `_daily_report_job()` 변경
```python
# 기존: build_daily_report() + 간단한 메시지
# 변경: build_comprehensive_daily_report() + BAR-44 포매팅
```

**변경 내용**:
- Comprehensive 리포트 빌드
- 활성 사용자 수 수집 (TODO)
- 형식화된 메시지 전송

---

## 데이터 구조

### build_comprehensive_daily_report() 반환값

```python
{
    "date": "2026-05-16",                    # YYYY-MM-DD
    "overall_return": 2.45,                  # 포트폴리오 수익률 %
    "daily_pnl": 125000,                     # 일일 손익액
    "active_users": 150,                     # 참여 사용자
    "avg_return": 1.85,                      # 평균 수익률 %
    "strategies": {
        "blueline": {
            "return": 3.2,                   # 전략 수익률 %
            "trades": 45,                    # 거래 건수
            "winrate": 62.5                  # 승률 %
        },
        "fzone": {
            "return": 2.1,
            "trades": 38,
            "winrate": 58.0
        }
    },
    "top_3_users": [
        {
            "rank": 1,
            "nickname": "트레이더A",
            "return": 5.5,
            "top_stock": "삼성전자"
        },
        # ... 2, 3위
    ],
    "warnings": [                            # 경고 메시지
        "SK하이닉스 -5% 급락 주의"
    ],
    "tomorrow_forecast": {                   # 내일 전망
        "watch_stocks": ["005930", "000660"],
        "ai_insight": "기술적 반등 신호 감지"
    }
}
```

---

## 설정 요구사항

### 환경변수 (필수)
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_channel_id_here
```

### 의존성
```
apscheduler>=3.10.0
python-telegram-bot>=13.0
```

---

## 통합 가이드

### FastAPI Lifespan에 스케줄러 등록

```python
from contextlib import asynccontextmanager
from scripts.finance.telegram_integration.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작
    scheduler = start_scheduler()
    yield
    # 종료
    stop_scheduler()

app = FastAPI(lifespan=lifespan)
```

### 즉시 리포트 전송 (테스트용)

```python
from scripts.finance.telegram_integration.scheduler import send_report_now

# 수동 트리거
await send_report_now()
```

---

## TODO 항목

### 우선순위 1 (Phase 1 완료용)
- [ ] 실제 활성 사용자 수 데이터 연동
- [ ] 사용자별 수익률 계산 로직
- [ ] 실제 경고 조건 구현 (손실률, 집중도 등)

### 우선순위 2 (Phase 2)
- [ ] AI 분석 기반 내일 전망 데이터
- [ ] 주간 리포트 (금요일 18:00)
- [ ] 긴급 알림 자동 트리거

### 우선순위 3 (향후)
- [ ] 개인 맞춤형 리포트
- [ ] 커뮤니티 투표 통합
- [ ] 여러 채널 동시 발송

---

## 테스트 결과

✅ **5개 검증 항목 모두 통과**

```
✅ Test 1: 정상 리포트 포매팅 성공
✅ Test 2: 손실 리포트 포매팅 성공
✅ Test 3: 빈 리포트 포매팅 성공
✅ Test 4: Comprehensive 리포트 빌드 성공
✅ Test 5: 빈 거래 리포트 성공
```

**테스트 파일**: `scripts/test_bar49_simple.py`

---

## 파일 목록

### 신규 파일
- `scripts/finance/telegram_integration/daily_report_formatter.py` (146줄)
- `scripts/test_bar49_simple.py` (테스트)
- `docs/BAR-49-TELEGRAM-SCHEDULER-IMPLEMENTATION.md` (이 문서)

### 수정된 파일
- `scripts/finance/telegram_integration/scheduler.py`
  - 시간: 09:00 → 18:00 KST
  - 리포트 빌드 방식 변경
  - 작업 함수 업데이트

- `backend/core/monitoring/report_service.py`
  - `build_comprehensive_daily_report()` 메소드 추가
  - `_calc_strategy_stats()` 헬퍼 추가
  - `_get_top_performers()` 헬퍼 추가
  - `send_daily_report()` 개선

- `backend/core/monitoring/telegram_bot.py`
  - `send_raw_message()` 메소드 추가

---

## 다음 단계

### Phase 1 마무리
1. 실제 Telegram Bot 토큰/채널 ID 설정
2. 환경변수 `.env.local`에 설정
3. FastAPI 앱에 스케줄러 등록 확인
4. 매일 18:00에 자동 발송 확인

### Phase 2 준비
1. 실제 사용자 데이터 통합
2. 고급 분석 지표 추가
3. 채널 구독 활성화

---

## 관련 문서

- BAR-44: `docs/bar-44-telegram-daily-report-spec.md`
- 배포 가이드: `docs/DEPLOYMENT-TELEGRAM-CHANNEL.md`
- 메시지 샘플: `docs/telegram-message-samples.md`
- 템플릿: `docs/telegram-message-templates.md`

---

**작성**: Backend Engineer (BAR-49)  
**최종 업데이트**: 2026-05-16 16:00 KST
