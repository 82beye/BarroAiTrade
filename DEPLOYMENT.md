# BarroAiTrade DEPLOYMENT

운영 환경 배포 절차서. `RUNBOOK.md` 의 장애 대응과 분리된 사전 배포 절차.

---

## 1. 사전 조건 (Live Trading Checker)

`infra/live-checklist.yaml` 의 모든 게이트 통과 필수.

```bash
# 자동 검증
python -m backend.security.live_trading_checker --checklist=infra/live-checklist.yaml
```

### 필수 게이트
- ✅ `regression_passed` — 회귀 ≥ 605, 0 fail
- ✅ `baseline_within_5pct` — BAR-44 베이스라인 ±5%
- ⏳ `simulation_3weeks_clean` — manual attestation
- ✅ `kill_switch_sim_100pct` — 24 cases (BAR-64 + BAR-66)
- ✅ `owasp_top10_passed` — Semgrep workflow
- ⏳ `pen_test_p0_p1_zero` — manual
- ✅ `audit_chain_30d_intact`
- ✅ `alerts_iac_deployed` — `monitoring/alerts.yaml`
- ✅ `runbook_exists`
- ✅ `deployment_doc_exists`

---

## 2. 배포 토폴로지

### Docker Compose (단일 호스트)
```bash
docker compose -f docker-compose.yml up -d
# postgres → backend (depends_on healthy) → frontend → prometheus → grafana
```

### Kubernetes (멀티 노드, BAR-72b)
```yaml
# k8s/deployment.yaml — 운영 진입 시 작성
apiVersion: apps/v1
kind: Deployment
metadata:
  name: barro-backend
spec:
  replicas: 3
  selector: { matchLabels: { app: barro-backend } }
  template:
    spec:
      containers:
        - name: backend
          image: barro-backend:vX.Y.Z
          envFrom:
            - secretRef: { name: barro-secrets }   # JWT_SECRET, KIWOOM_*, ANTHROPIC_API_KEY
          resources:
            limits: { memory: 1Gi, cpu: 500m }
```

---

## 3. 시크릿 관리 (BAR-69b)

### 운영
- AWS Secrets Manager 또는 Vault
- `JWT_SECRET` (≥ 32 bytes)
- `KIWOOM_APP_KEY` / `KIWOOM_APP_SECRET` (SecretStr)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (SecretStr)
- `POSTGRES_PASSWORD` / `REDIS_URL` (SecretStr)
- `FERNET_KEY` (column 암호화)

### 키 회전
- 분기별 (RUNBOOK § 6 참조)
- JWT secret 회전 → 1h 후 access 자연 만료

---

## 4. DB 마이그레이션

```bash
# 운영 진입 전
alembic upgrade head

# 0001~0007 마이그레이션 누계
# 롤백: alembic downgrade -1 (한 단계씩)
```

### Postgres 백업
- 매일 03:00 UTC `pg_dump` → S3
- PITR (BAR-72b) — 5분 단위 WAL

---

## 5. 모니터링

### Prometheus + Grafana
```bash
# infra/grafana/provisioning/ 자동 마운트
# monitoring/alerts.yaml → Grafana alert rules
```

### OpenTelemetry (BAR-73b)
- OTLP exporter → Tempo / Jaeger
- trace_id 분산 추적

---

## 6. 실거래 진입 단계

| 단계 | 자산 | 기간 | 조건 |
|:---:|:----:|:----:|------|
| 0 | 0% | – | 모의 3주 무사고 + checklist 100% PASS |
| 1 | **5%** | 1주 | 24/7 모니터링, 사고 시 즉시 simulation 복귀 |
| 2 | 10% | 1주 | 1주 무사고 |
| 3 | 25% | 2주 | 누적 2주 무사고 |
| 4 | 50% | 4주 | 누적 4주 무사고 |
| 5 | 100% | 영구 | 누적 6주 무사고 + 사고 보고서 0건 |

---

## 7. 롤백 절차

### 코드
```bash
# 직전 태그로 복원
git checkout v<previous>
docker compose pull
docker compose up -d --force-recreate backend
```

### DB
```bash
# 한 단계 다운그레이드
alembic downgrade -1
# 또는 백업 복구
pg_restore -d barro --clean <backup>.dump
```

### KillSwitch 즉시 발동 (긴급)
```python
from backend.core.risk.kill_switch import KillSwitch, KillSwitchReason
ks.trip(KillSwitchReason.MANUAL, datetime.utcnow())
```

---

## 변경 이력
| Date | Change |
|------|--------|
| 2026-05-08 | 초안 (BAR-OPS-05) |
