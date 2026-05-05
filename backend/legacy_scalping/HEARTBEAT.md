# 하트비트 스케줄 (자율 점검)

## 매일 반복 스케줄 (장 운영일만, 월~금)

### 08:00 - 계좌 현황 점검 (cron)
- `python main.py --account-status`
- 키움 REST API 계좌 잔고 조회
- 총 평가금액, 예수금, 보유종목 상세를 텔레그램 발송
- 전일 미청산 포지션 확인

### 08:25 - 모의매매 시스템 시작 (cron)
- `python main.py --mode simulation`
- 내부 타임라인:
  - 08:25~ Pre-Market 전종목 스캔 (캐시 우선 로드)
  - 09:00  장 시작 대기
  - 09:05~ 매수 가능 시작 (장 초반 5분 노이즈 회피)
  - 14:30  신규 매수 마감
  - 14:50  강제 청산 (전 포지션 시장가 매도 + 미체결 취소)
  - 15:10  일일 리포트 텔레그램 발송
- 장중 5분 주기 포지션 현황 텔레그램 보고

### 15:30 - OHLCV 캐시 업데이트 (cron)
- `python main.py --update-cache`
- 전종목 일봉 데이터를 JSON 캐시에 저장
- 다음날 스캔 시 API 호출 없이 캐시에서 로드 (수 초 내 완료)
- 완료 시 텔레그램 알림

### 16:00 - 마감 후 계좌 최종 현황 (cron)
- `python main.py --account-status`
- 장 마감 후 최종 계좌 상태 텔레그램 발송

## Cron 관리
```bash
# 등록
./scripts/setup_cron.sh

# 제거
./scripts/setup_cron.sh remove

# 확인
crontab -l
```

## 비장 운영일 (주말/공휴일)
- 매매 관련 스케줄 자동 스킵 (`is_krx_trading_day()` 체크)
- `--account-status`는 거래일 무관하게 실행 가능
