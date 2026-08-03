# 개장 전 Telegram 브리핑 운영

## 발송 정책

- 평일 08:25 KST에 하루 한 번 실행한다.
- 생성 또는 Telegram 네트워크 오류가 발생한 경우에만 5분 간격으로 최대 두 번 더
  시도한다(08:25 실행 기준 최장 08:35 재시도).
- `scan`, `prediction`, `strategy` 세 논리 메시지의 성공 여부를 날짜별로 기록한다.
  재시작 또는 부분 실패 후 재시도할 때 이미 성공한 블록은 다시 보내지 않는다.

5분 상시 배치를 사용하지 않는 이유는 세 입력이 전일 일봉, 전일/최근 체결 기록으로
구성되어 장중 5분마다 갱신되지 않기 때문이다. 상시 배치는 같은 긴 메시지를 반복하고,
키움 조회 한도 및 Telegram 수신 품질만 악화시킨다. 장중 변화가 필요한 포지션/체결
알림은 기존 실시간 매매 알림 프로세스가 담당한다.

## 메시지 및 산출물

1. `종목 스캔 완료`: 감시 종목 수, 상위 10개, 나머지 개수
2. `팀 에이전트 상승 예측`: 상위 20개, 합의수준, 5개 에이전트 점수와 핵심 사유
3. `전략 최적화 팀 분석 결과`: 당일 파라미터, 블랙리스트/가감 종목, 에이전트 상세

동시에 아래 파일을 생성한다.

- `logs/watchlist_YYYY-MM-DD.json`
- `logs/predictions_YYYY-MM-DD.json`

`.env.local`의 `BARRO_AI_TRADE_DIR`을 같은 `logs` 디렉터리로 설정하면 `ai_swing`의
스캔∩예측 교집합 로더가 이 파일을 직접 읽는다. 브리핑은 주문을 실행하지 않는다.

## 데이터 선행 조건

브리핑은 `data/ohlcv_cache`가 종목 마스터의 85% 이상을 포함하고 최근 7일 이내 EOD
메타를 가질 때만 실행된다. 부분 캐시로 그럴듯한 순위를 만들지 않고 명시적으로 실패한다.
`BARRO_OHLCV_SYNC_ENABLED=1`이면 백엔드 스케줄러가 평일 15:40 KST에 다음 거래일용
일봉 캐시를 갱신한다. 갱신기는 `data/stock_names.json`으로 빈 캐시도 부트스트랩한다.

## 수동 검증 및 재발송

```bash
set -a; . ./.env.local; set +a

# 실제 데이터로 생성하되 Telegram은 보내지 않음
./.venv/bin/python scripts/premarket_telegram_briefing.py \
  --dry-run --attempts 1

# 당일 상태를 무시하고 운영 채팅에 강제 재발송(의도한 경우에만)
./.venv/bin/python scripts/premarket_telegram_briefing.py \
  --force --attempts 1
```

상태 파일은 기본 `logs/premarket_briefing_state.json`, 실행 로그는 백엔드 launchd 로그와
스케줄러 로그에 남는다. 최종 세 번 모두 실패하면 Telegram으로 실패 알림을 한 번 보낸다.
