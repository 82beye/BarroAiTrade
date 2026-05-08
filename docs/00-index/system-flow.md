---
tags: [index, architecture, mermaid]
---

# BarroAiTrade 시스템 흐름도

> 26 OPS BAR 누적된 운영 자동화 시스템 전체 흐름.
> [[ops-track-index|작업 순서 인덱스]]

---

## 1. 매수 사이클 (09:30 cron)

```mermaid
flowchart TD
    A[09:30 cron 트리거] --> B[simulate_leaders.py]
    B --> P{policy.json 자동 로드<br/>OPS-32}
    P --> C1[ka10032 거래대금 상위<br/>OPS-12]
    P --> C2[ka10027 등락률 상위<br/>OPS-12]
    P --> C3[ka10030 거래량 상위<br/>OPS-12]
    C1 --> S[LeaderPicker 3-factor 점수<br/>OPS-12]
    C2 --> S
    C3 --> S
    S -->|min_score 통과| L[Top N 주도주]
    L --> CD[ka10081 일봉 600개<br/>OPS-10]
    CD --> SIM[IntradaySimulator<br/>OPS-08+35]
    SIM -->|next_open 진입<br/>intrabar 청산<br/>fee/tax/slippage| TR[전략별 trades]
    TR --> CSV[simulation_log.csv 영속<br/>OPS-13]
    TR --> BAL[kt00018/kt00001 잔고 조회<br/>OPS-15]
    BAL --> GATE[balance_gate 자금 정책<br/>per 30% / total 90%<br/>OPS-16]
    GATE --> REC[추천 qty]
    REC --> LOG[LiveOrderGate 4중 안전<br/>OPS-17]
    LOG -->|env flag<br/>일일 손실<br/>거래수 한도<br/>audit| EX[KiwoomNativeOrderExecutor<br/>OPS-14]
    EX --> KT[kt10000 매수<br/>DRY_RUN/실 주문]
    KT --> AU[order_audit.csv]
    KT --> TG1[Telegram 매수 알림<br/>OPS-21]
    
    style P fill:#fef3c7
    style LOG fill:#fecaca
    style TG1 fill:#dbeafe
```

---

## 2. 매도 사이클 (매시간 10~15시)

```mermaid
flowchart TD
    A[매시간 cron] --> B[evaluate_holdings.py]
    B --> P{policy.json 자동 로드<br/>TP/SL}
    P --> H[kt00018 보유 종목 조회<br/>OPS-15]
    H --> EVAL[ExitPolicy 평가<br/>OPS-20]
    EVAL --> D{시그널}
    D -->|pnl_rate ≥ TP| TP[TAKE_PROFIT]
    D -->|pnl_rate ≤ SL| SL[STOP_LOSS]
    D -->|중간| HOLD[HOLD]
    TP --> LOG[LiveOrderGate]
    SL --> LOG
    LOG --> EX[kt10001 매도]
    EX --> AU[order_audit.csv]
    EX --> TG[Telegram ✅ TP / 🛑 SL 알림]
    
    HOLD -.보유 유지.-> END[종료]
    
    style EVAL fill:#fef3c7
    style LOG fill:#fecaca
    style TG fill:#dbeafe
```

---

## 3. 미체결 처리 (필요 시)

```mermaid
flowchart LR
    A[지정가 매수] -.미체결.-> O[/orders 명령<br/>kt00004]
    O --> CHK{체결 안 됨?}
    CHK -->|yes| C[/cancel_order ORD_NO SYMBOL<br/>kt10003]
    CHK -->|no| W[대기]
    C --> AU[audit append CANCELED]
    C --> RE[새 가격으로 재진입]
    RE -.사이클 반복.-> A
    
    style O fill:#fef3c7
    style C fill:#fecaca
```

---

## 4. 학습 루프 (주1회)

```mermaid
flowchart TD
    A[1주~1개월 누적] --> B[/diff 명령<br/>OPS-29]
    B --> SIM[simulation_log.csv<br/>예측 PnL]
    B --> REAL[ka10073 실현 PnL<br/>OPS-28]
    SIM --> CMP[compare 매칭]
    REAL --> CMP
    CMP --> BIAS[bias_counts<br/>양호 / 과대 / 과소 / 신호없음]
    BIAS --> TUNE[/tune 명령<br/>OPS-30]
    TUNE -->|과대 ≥50%| R1[min_score +0.1 ⚠️]
    TUNE -->|양호 ≥80%| R2[min_score -0.1 ℹ️]
    TUNE -->|과소 ≥30%| R3[stop_loss +0.5 🚨]
    TUNE -->|양호 ≥80% n≥5| R4[max_per_position +0.05 ℹ️]
    R1 --> APPLY{/tune apply<br/>OPS-31}
    R2 --> APPLY
    R3 --> APPLY
    R4 --> APPLY
    APPLY --> JSON[data/policy.json<br/>+ history 50건]
    JSON -.다음 시뮬 자동 반영.-> NEXT[OPS-32 자동 로드]
    NEXT -.다음 매수 사이클.-> M[매수 사이클]
    
    style BIAS fill:#fef3c7
    style APPLY fill:#dbeafe
    style JSON fill:#d1fae5
```

---

## 5. 텔레그램 양방향 봇 (24/7 데몬)

```mermaid
flowchart TD
    USER[운영자 모바일] -->|/balance| BOT[run_telegram_bot.py<br/>OPS-24]
    USER -->|/sim| BOT
    USER -->|/eval| BOT
    USER -->|/sim_execute| BOT
    USER -->|/confirm TOKEN| BOT
    USER -->|/sell_execute| BOT
    USER -->|/confirm_sell TOKEN| BOT
    USER -->|/cancel_order| BOT
    USER -->|/diff| BOT
    USER -->|/tune apply| BOT
    USER -->|/pnl| BOT
    USER -->|/orders| BOT
    USER -->|/audit| BOT
    
    BOT --> WL{chat_id whitelist<br/>OPS-24}
    WL -->|차단| DROP[ignore]
    WL -->|통과| H[handler dispatch]
    
    H --> ACC[KiwoomNativeAccountFetcher<br/>OPS-15+28+33]
    H --> RANK[KiwoomNativeLeaderPicker<br/>OPS-11+12]
    H --> EXE[KiwoomNativeOrderExecutor<br/>OPS-14+34]
    H --> CFG[PolicyConfigStore<br/>OPS-31]
    H --> CONFIRM[OrderConfirmStore<br/>5분 TTL<br/>OPS-26+27]
    
    ACC --> RESP[응답 생성]
    RANK --> RESP
    EXE --> RESP
    CFG --> RESP
    CONFIRM --> RESP
    RESP --> USER
    
    style WL fill:#fecaca
    style CONFIRM fill:#fef3c7
```

---

## 6. 7중 보안 layer (매수·매도 confirm 패턴)

```mermaid
flowchart LR
    M[모바일 명령] --> L1[1.chat_id whitelist<br/>OPS-24]
    L1 --> L2[2.6자리 token<br/>secrets CSPRNG<br/>OPS-26]
    L2 --> L3[3.5분 TTL<br/>OPS-26]
    L3 --> L4[4.일회용 폐기<br/>OPS-26]
    L4 --> L5[5.chat_id 매칭<br/>OPS-26]
    L5 --> L6[6.LiveOrderGate 4중<br/>OPS-17]
    L6 --> L7[7.ENV flag<br/>LIVE_TRADING_ENABLED<br/>OPS-17]
    L7 --> EX[KiwoomOrderExecutor]
    EX --> AU[audit.csv]
    
    style L1 fill:#fee2e2
    style L2 fill:#fee2e2
    style L3 fill:#fee2e2
    style L4 fill:#fee2e2
    style L5 fill:#fee2e2
    style L6 fill:#fecaca
    style L7 fill:#fca5a5
```

---

## 7. 데이터 영속 구조

```mermaid
flowchart LR
    SIM[IntradaySimulator] --> S1[simulation_log.csv<br/>예측 PnL 누적]
    GATE[LiveOrderGate] --> A1[order_audit.csv<br/>BLOCKED/DRY_RUN/ORDERED/FAILED]
    TUNE[PolicyConfigStore] --> P1[policy.json<br/>min_score 등 + history 50건]
    REPORT[generate_daily_report.py] --> R1[reports/YYYY-MM-DD.md]
    
    S1 -.OPS-29 diff 입력.-> CMP[비교]
    KIWOOM[ka10073 실현] -.OPS-29 diff 입력.-> CMP
    CMP -.OPS-30 추천.-> TUNE
    
    style S1 fill:#d1fae5
    style A1 fill:#fed7aa
    style P1 fill:#fbcfe8
    style R1 fill:#dbeafe
```

---

## 8. End-to-End 풀 자동화 cron 매핑

```mermaid
gantt
    title 매일 운영 cron 스케줄
    dateFormat HH:mm
    axisFormat %H:%M
    
    section 매수
    시뮬 + 추천 + 실행 + 텔레그램  :09:30, 30m
    
    section 매도 평가
    매시간 TP/SL 평가  :10:00, 6h
    
    section 강제 청산
    장 마감 5분 전 전량  :15:20, 10m
    
    section 리포트
    markdown + 텔레그램  :16:00, 30m
    
    section 학습 (주1회)
    /diff → /tune apply  :crit, 16:30, 30m
```

---

## 9. 보안 layer 누적

```mermaid
mindmap
  root((보안))
    Secret 관리
      SecretStr 강제 CWE-798
      .env.local gitignored
      .gitignore 검증
    네트워크
      https-only CWE-918
      timeout 10s
    인증
      OAuth2 토큰 캐시
      30분 margin 자동 refresh
    실행 안전
      LIVE_TRADING_ENABLED env flag
      DRY_RUN 모드 우선
      일일 손실 한도 -3%
      일일 거래수 한도 50건
    감사
      audit.csv append-only
      MFA 차단 시 즉시 알림
    봇
      chat_id whitelist
      token CSPRNG
      TTL 5분
      일회용
```

---

## 운영 시작 체크리스트

- [ ] 키움 키 회전 (`KIWOOM_APP_KEY` / `KIWOOM_APP_SECRET`)
- [ ] Telegram bot token 회전 (`TELEGRAM_BOT_TOKEN`)
- [ ] `.env.local` 갱신 + `set -a; . ./.env.local; set +a` 검증
- [ ] cron 4건 등록 (매수 09:30 / 평가 매시간 / 청산 15:20 / 리포트 16:00)
- [ ] 봇 데몬 시작 (`nohup ... run_telegram_bot.py &`)
- [ ] 모바일 `/ping` 응답 확인
- [ ] 1~2주 mockapi 검증 → 실전 host 결정

→ 가이드: [[../05-paperclip/runbook-ops]]
→ 보안 가이드: [[../05-paperclip/security-rotation]]
