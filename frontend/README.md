# BarroAiTrade Frontend

AI 기반 멀티마켓 자동매매 플랫폼의 프론트엔드

## 기술 스택

- **Framework:** Next.js 14
- **Language:** TypeScript
- **Styling:** Tailwind CSS
- **State Management:** Zustand
- **HTTP Client:** Axios
- **Real-time:** WebSocket

## 프로젝트 구조

```
frontend/
├── app/              # Next.js App Router
├── components/       # React 컴포넌트
├── hooks/           # 커스텀 훅
├── lib/             # 유틸리티 & API 클라이언트
├── types/           # TypeScript 타입 정의
└── public/          # 정적 자산
```

## 설치

```bash
npm install
```

## 환경 설정

```bash
cp .env.example .env.local
```

## 개발

```bash
npm run dev
```

브라우저에서 [http://localhost:3000](http://localhost:3000)으로 접속

## 빌드

```bash
npm run build
npm start
```

## 개발 로드맵

- [ ] 대시보드 페이지
- [ ] 마켓 데이터 조회 페이지
- [ ] 주문 관리 페이지
- [ ] 포지션 조회 페이지
- [ ] 실시간 차트
- [ ] 위험도 관리 UI
- [ ] 성과 분석
