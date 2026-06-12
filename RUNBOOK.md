# BarroAiTrade RUNBOOK

운영 장애 대응 절차서. 모든 절차는 `monitoring/alerts.yaml` 의 alert 트리거에 매핑.

---

## 1. KillSwitch 발동 (DailyLoss / Slippage / GatewayDisconnect)

### 증상
- Alert: `DailyLossExceeded` / `SlippageHigh` / `GatewayDisconnect`
- 신규 매매 진입 차단됨

### 대응
1. `KillSwitch.state.reason` 확인 (audit_log 또는 prometheus)
2. 보유 포지션 청산은 정상 작동 — `evaluate_position` 계속 실행
3. 원인 분석:
   - `daily_loss`: 시장 변동 / 전략 오작동 — 백테스트 재검증
   - `slippage`: 호가 갱신 lag — gateway 상태 점검
   - `gateway_disconnect`: NXT/KRX gateway 재연결 시도
4. cooldown 4h 후 자동 reset 또는 수동 reset (admin 권한):
   ```python
   from backend.core.risk.kill_switch import KillSwitch
   ks.reset(now)
   ```

### 회귀 검증 후 재가동
- `pytest backend/tests/risk/` PASS 확인
- 모의 1주 운용 무사고 후 실거래 재진입

---

## 2. 게이트웨이 단절 (NxtGateway 30s)

### 증상
- Alert: `GatewayDisconnect`
- `NxtGatewayManager.status == DEGRADED` 또는 `DOWN`

### 대응
1. NxtGatewayManager 상태 확인
2. fallback gateway 자동 전환 동작 확인 (primary → fallback)
3. 재연결 시도 횟수 (`reconnect_attempts`) 점검
4. 5분 무수신 시 manual restart:
   ```bash
   docker compose restart backend
   ```
5. KOSCOM CHECK 또는 키움 OpenAPI 키 재발급 필요 여부 확인

---

## 3. 뉴스 임베딩 Consumer Lag (BAR-58)

### 증상
- Alert: `NewsConsumerLag` (lag > 5분)

### 대응
1. Redis Streams `news_items` consumer group `embedder_v1` 상태 확인:
   ```bash
   redis-cli XINFO GROUPS news_items
   ```
2. PEL (pending entries) 누적 확인 — `XPENDING news_items embedder_v1`
3. EmbeddingWorker pod scale-up:
   ```bash
   kubectl scale deployment embedder --replicas=4
   ```
4. claim 으로 정체 PEL 회복:
   ```python
   await client.xclaim(stream_key, group, consumer, min_idle_time, ids)
   ```

---

## 4. DB Pool Exhausted

### 증상
- Alert: `DBPoolExhausted` (90% 초과)

### 대응
1. 롱 트랜잭션 식별:
   ```sql
   SELECT pid, query, state, now() - query_start AS duration
   FROM pg_stat_activity
   WHERE state != 'idle' ORDER BY duration DESC LIMIT 10;
   ```
2. 필요 시 강제 종료:
   ```sql
   SELECT pg_terminate_backend(<pid>);
   ```
3. pool_size 임시 증가 또는 PgBouncer 도입 (BAR-72b)

---

## 5. 캐시 갱신 (Redis)

### 증상
- 가격/임베딩 stale 데이터

### 대응
- 단일 키:
  ```bash
  redis-cli DEL <key>
  ```
- 패턴 일괄:
  ```bash
  redis-cli --scan --pattern 'news:dedup:*' | xargs redis-cli DEL
  ```
- 전체 flush (긴급만):
  ```bash
  redis-cli FLUSHDB
  ```

---

## 6. 키 회전 (BAR-67/69 SecretStr)

### 분기별 절차
1. 새 secret 생성 (env var 갱신)
2. JWT_SECRET 회전:
   - 기존 access token 은 1h 후 자연 만료
   - refresh token (7d) 사용자 재로그인 유도
3. ANTHROPIC_API_KEY / KIWOOM_APP_SECRET 회전:
   - Vault / Secrets Manager 갱신 (BAR-69b)
   - 라이브 서비스 무중단 적용 — 환경변수 reload
4. Fernet 키 회전:
   - 기존 ciphertext 점진적 재암호화 (audit_log + RLS 적용 컬럼)

---

## 7. 실거래 진입 절차 (Master Plan v2 Phase 4 종료 후)

### 사전 조건
- [ ] 회귀 ≥ 587 passed, 0 fail
- [ ] 모의 3주 자동매매 인간 개입 0회
- [ ] KillSwitch 시뮬 시나리오 100% 발동 검증
- [ ] OWASP Top 10 자동 스캔 통과 (BAR-70)
- [ ] 모의 침투 테스트 P0/P1 0건

### 진입 단계
1. 자산 5% 이내 라이브 가동
2. 1주 라이브 검증 (24/7 모니터링)
3. 사고 발생 시 즉시 simulation 복귀 + 사고 보고서
4. 1주 무사고 통과 후 자산 10% → 25% → 50% 단계적 확대

---

## 8. 재해 복구

### Postgres 백업
- 매일 03:00 UTC 자동 백업 (BAR-72b 도입)
- 복구: `pg_restore -d barro <backup>.dump`

### 매매 일지 / Audit Log
- 30일 hash chain 무결성 검증 (BAR-68 audit_chain.verify_chain)
- 실패 idx 보고 후 수동 검토

---

## 9. BAR-OPS-38 리스크 가드 (2026-06-10 매매복기 권고 — 기본 활성)

머지 후 **데몬/봇 재기동 시점부터** 아래 가드가 기본 ON 이다. 근거: `reports/2026-06-10/2026-06-10_매매복기.md`.

### 기본 활성 항목과 끄기/조정 env

| 가드 | 기본값 | env (끄기=0 또는 값 조정) |
|------|--------|---------------------------|
| 일일손실 게이트 입력 = 당일실현(ka10074)+보유평가 / 추정예탁자산 | 활성(코드) | (없음 — 코드 경로) |
| 일일손실 latch (당일 sticky, 파일 영속) | ON | `SUPERTREND_AUTO_LOSS_LATCH=0` |
| 매도 주문 재시도 | 2회/백오프 2s | `SUPERTREND_AUTO_ORDER_RETRY=0` |
| supertrend 하드손절 | -6% | `SUPERTREND_AUTO_HARD_STOP=0` |
| supertrend 단일 트랜치 기록(이중매수 차단) | ON | `SUPERTREND_AUTO_SINGLE_TRANCHE=0` |
| supertrend 시초갭 진입 차단 | +15% | `SUPERTREND_AUTO_MAX_OPEN_GAP=0` |
| supertrend 진입 컷오프(이월 후보 차단) | 14:30 | `SUPERTREND_AUTO_ENTRY_CUTOFF=` (빈값) |
| 이월 포지션 갭하락 스탑(전일종가比) | -3% | `SUPERTREND_AUTO_CARRY_GAP_STOP=0` |
| EOD 이월 총액 한도(15:10 초과분 청산) | 계좌 20% | `BARRO_CARRY_LIMIT_RATIO=0` |
| 상한가(근접 잠김) 진입 차단 | 등락률 ≥29.5% | `BARRO_MAX_FLU_RATE=30` (구버전 동작) |
| gold/f 시초갭 추격 차단 | 등락률 ≥15% | `BARRO_ZONE_MAX_FLU=0` |
| gold 고점근접 무조건 차단(#7 standalone) | ON | `BARRO_GOLD_HIGH_GUARD=0` |

### 신규 상태/산출 파일 (data/, gitignore)
- `daily_gate_state.json` — latch 영속(당일 단위 자동 롤오버). **수동 latch 해제 = 파일 삭제**.
- `fill_audit.csv` — 장마감 후 ka10073 당일 체결(실현) 백필. 매매복기/일일감사의 체결가 실측 소스.
- `order_audit.csv` 에 `UNFILLED` action 행 추가 — SYNC 가 미체결로 판정한 매수의 자가설명(일일감사가 자동 상쇄).

### 운영 검증 체크리스트 (첫 거래일)
1. 장중 BLOCKED 사유에 `당일실현+보유평가/추정예탁자산` 라벨 표기 확인
2. ka10074 당일 실현손익이 장중 실시간 반영되는지 1회 대조 (mockapi 만 검증된 TR)
3. 15:10 `[CARRY-LIMIT]` 로그 / 장마감 `[FILL-BACKFILL]` 적재 확인
4. `_daily_strategy_audit.py --date <당일>` 에 이월 분리·시간대 버킷 출력 확인

### 롤백
- 전체: `git revert 7a08472 e943a30 aa8a585` (+ 본 RUNBOOK 커밋)
- 부분: 위 표의 env 로 개별 비활성 (재기동 필요)

---

## 10. BAR-OPS-39 비용 현실화 + 가드 보완 (2026-06-11 매매복기 권고)

근거: `reports/2026-06-11/2026-06-11_매매복기.md`. 핵심 = **거래 비용이 브로커 실측
기준으로 전면 교체**(편도 수수료 0.015%→0.175%, 매도세 0.18%→0.20%, 왕복 ≈0.55%).

### ⚠️ 운영 행동 변화 (머지+재기동 시점부터)

1. **아침/장중 종목·전략 선정이 보수화된다** — 데몬·simulate_leaders·intraday_buy 의
   선정 시뮬레이터(IntradaySimulator)가 종전 **비용 0(gross)** 으로 돌고 있었음이
   확인돼(BAR-OPS-39 조사) 실측 비용 default 로 교체됨. `best_pnl>0` 게이트 통과
   시그널 수가 감소한다(의도 — 비용 후 음수 신호 제거).
2. **비용 민감도 재검증 결과** (무작위 298종목 × 일봉 600봉): gold_zone +2,506만→+1,690만
   (-33%, 생존) / f_zone +1,810만→+1,568만(-13%, 생존) / **sf_zone +86만→-12만 (음수
   전환)** — sf 는 게이트가 자연 감소시키므로 별도 비활성화 불필요, 발동 시그널 추이 관찰.
3. 일반 전략(gold/f/sf)도 **14:30 진입 컷오프** 적용(st 전용이던 것의 사각 봉합 —
   6/11 현대무벡스 14:33 진입 이월). swing_38(다일보유 설계)은 예외.

### env 조정표 (신규)

| 항목 | 기본값 | env |
|------|--------|-----|
| 수수료(편도, 소수) | 0.00175 | `BARRO_COMMISSION_RATE` — **요율 협의 후 인하 시 여기만 변경** |
| 매도 거래세(소수) | 0.0020 | `BARRO_TAX_RATE_SELL` |
| 일반 전략 진입 컷오프 | 14:30 | `BARRO_ZONE_ENTRY_CUTOFF=` (빈값=비활성) |
| st 재진입 가격조건(직전 진입가 이하만) | **OFF** | `SUPERTREND_AUTO_REENTRY_BELOW_ENTRY=1` (+`_TOL` 여유%) — 측정 후 활성 판단 |

### 신규 산출/동작
- `data/buy_audit.csv` — EOD 보유 종목 매수평단 스냅샷(kt00018). fill_audit(매도 실현)와
  합쳐 매수 체결 독립 감사 소스 완성.
- 일일감사 `--source auto|fill|estimate` — fill_audit 있으면 **실측 우선**(추정-실측 괴리
  병기), §B 진입 갭(전일比) 컬럼·§C 손절 슬립·익절 후 run-up(러너 shadow 측정) 추가.
- 매도 직전 장부 재확인(데몬·st 대칭) — 중복 매도 FAILED(6/9 3건·6/11 1건) 차단.
- 일봉 캐시 EOD 갱신 수정(`gap<=1`→`gap<1`) — 6/10 정지 원인. **배포 후 1회 수동 실행으로
  당일 일봉 백필 권장**: `./.venv/bin/python scripts/update_ohlcv_cache.py`.
- simulate_leaders(09:30 cron) strategy_id 전파 — audit '미지정' 빈칸 근절.

### 검증 체크리스트 (첫 거래일)
1. 09:30 매수 audit 행의 strategy_id 채워짐 확인 (빈칸 근절)
2. `[SKIP-CUTOFF]` 로그(14:30 이후 일반 전략 진입 차단) / `[SELL-SKIP]`(경합 회피) 동작 확인
3. EOD `[BUY-SNAPSHOT]` 적재 + 일봉 meta.json `new_days_added > 0` 확인
4. `_daily_strategy_audit.py --date <당일>` 이 "실측(ka10073)" 라벨 + §C 출력 확인
5. 선정 시그널 수 변화 관찰(보수화 정도) — 매수 0건화 시 `BARRO_*` 비용 env 재검토

### 롤백
- 비용만 구버전: `BARRO_COMMISSION_RATE=0.00015 BARRO_TAX_RATE_SELL=0.0018` (재기동)
- 전체: 본 변경 커밋들 `git revert`

---

## 변경 이력
| Date | Change | Author |
|------|--------|--------|
| 2026-05-08 | 초안 (BAR-OPS-04) | – |
| 2026-06-11 | §9 BAR-OPS-38 리스크 가드 기본 활성 | – |
| 2026-06-12 | §10 BAR-OPS-39 비용 현실화 + 가드 보완 | – |
