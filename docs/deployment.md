# BarroAiTrade 배포 가이드

## 시스템 요구사항

- Docker 24.0+
- Docker Compose 2.20+
- (선택) Python 3.11+ — 로컬 개발 시
- (선택) Node.js 20+ — 프론트엔드 로컬 개발 시

---

## 빠른 시작

```bash
# 1. 저장소 클론
git clone <repo-url>
cd BarroAiTrade

# 2. 환경변수 설정
cp .env.example .env.local
# .env.local 편집하여 Telegram 토큰 등 설정

# 3. 모의투자 모드로 시작
./scripts/start.sh simulation

# 4. 실거래 모드로 시작 (확인 단계 있음)
./scripts/start.sh live
```

---

## 서비스 엔드포인트

| 서비스        | URL                        | 설명               |
|-------------|----------------------------|--------------------|
| 대시보드       | http://localhost:3000      | Next.js 트레이딩 UI   |
| REST API     | http://localhost:8000      | FastAPI 백엔드       |
| API 문서      | http://localhost:8000/docs | Swagger UI          |
| Grafana      | http://localhost:3001      | 모니터링 대시보드       |
| Prometheus   | http://localhost:9090      | 메트릭 수집           |

---

## Telegram 알림 설정

1. [BotFather](https://t.me/botfather) 에서 `/newbot` 명령으로 봇 생성
2. 발급된 토큰을 `.env.local`의 `TELEGRAM_BOT_TOKEN`에 저장
3. [userinfobot](https://t.me/userinfobot) 에서 Chat ID 확인 후 `TELEGRAM_CHAT_ID`에 저장

**수신 알림 종류:**
- 시스템 시작/중지
- 매수/매도 신호
- 리스크 경고
- 오류 발생

---

## 로그 확인

```bash
# 전체 서비스 로그
./scripts/logs.sh

# 백엔드만
./scripts/logs.sh backend

# 파일 로그 (Docker 볼륨)
docker-compose exec backend tail -f /app/logs/barro.log
docker-compose exec backend tail -f /app/logs/trades.log
```

---

## 개별 서비스 재시작

```bash
# 백엔드만 재시작
docker-compose restart backend

# 특정 서비스만 재빌드 후 시작
docker-compose up -d --build backend
```

---

## 시스템 중지

```bash
./scripts/stop.sh

# 볼륨(데이터)까지 삭제할 경우
docker-compose down -v
```

---

## 로컬 개발 (Docker 없이)

```bash
# 백엔드
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# 프론트엔드 (별도 터미널)
cd frontend
npm install
npm run dev
```

---

## 모니터링 대시보드 (Grafana)

- URL: http://localhost:3001
- 기본 계정: admin / barro1234 (`.env.local`에서 변경 가능)
- 데이터소스: Prometheus (자동 프로비저닝)

---

## 트러블슈팅

### 백엔드가 시작되지 않을 때
```bash
docker-compose logs backend
```

### 포트 충돌
```bash
# 사용 중인 포트 확인
lsof -i :8000 -i :3000 -i :3001 -i :9090
```

### 모든 컨테이너 상태 확인
```bash
docker-compose ps
```
