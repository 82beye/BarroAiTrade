# BarroAiTrade 보안 심층 검토 리포트

- **작성일**: 2026-07-01
- **방식**: 멀티에이전트 보안 리뷰(5개 차원 병렬 진단 → 적대적 검증). 진단 43개 서브에이전트, 989 tool-use.
- **대상**: BarroAiTrade (mock-live: `KIWOOM_BASE_URL=mockapi.kiwoom.com`, 실금 아님)
- **결과**: 총 37건 — CONFIRMED 35 · PLAUSIBLE 2 · REJECTED 0
- **심각도 분포**: 🔴 critical 2 · 🟠 high 13 · 🟡 medium 9 · 🔵 low 9 · ⚪ info 4

## 요약 (Executive Summary)

핵심 위험은 **백엔드 API 전체가 무인증 상태**이며, 이것이 **ngrok 공개 터널(`https://myspace-wagon-elephant.ngrok-free.dev`)을 통해 인터넷에 노출**된다는 점이다. JWT 인증 코드는 존재하나 `main.py`에 라우터가 배선되지 않아 완전히 비활성 상태다. 워크플로우가 실제 공개 URL로 `curl .../api/accounts/balance` 를 호출해 인증 없이 계좌 잔고·보유 포지션이 반환됨을 확인했다. mock 환경이라 금전 손실은 없으나, 계좌·매매 정보가 URL만 알면 누구에게나 노출되고, `KIWOOM_BASE_URL`을 실서버로 바꾸는 순간 무단 주문까지 가능한 구조다.

## 이번에 적용한 조치 (safe, 무중단)

| 조치 | 대상 | 효과 | 되돌리기 |
|------|------|------|----------|
| 로그 파일 권한 600 제한 | `logs/*.log`, `logs/*.err` (28개) | 봇 토큰이 평문 기록된 로그(`telegram_bot.log`, `cb_alert.log` 등)의 world-readable(644) 제거 → 소유자 전용 | `chmod 644` |

> 적용 후 텔레그램봇·종베데몬·백엔드 3개 프로세스 정상 동작 확인(무중단). 신규 생성 로그는 프로세스 umask를 따르므로 시점 조치임 — 근본 해결은 아래 CRED-001(로깅 마스킹) 권고 참조.

## 권고 조치 (미적용 — 사용자 승인/재시작 필요)

라이브(mock) 데몬·봇·대시보드가 가동 중이고 사용자가 ngrok으로 원격 모니터링하므로, 아래는 자동 적용하지 않고 권고로 남긴다. `fix_safety` 분류:

- **risky_hitl** (2건): 사람 승인 필수 (잘못 적용 시 대시보드 접근 차단 등)
- **needs_restart** (20건): 코드/설정 수정 + 서비스 재시작 필요
- **safe_auto** (15건): 무중단 적용 가능하나 대부분 코드수정 후 재기동해야 효과 발생, 또는 정보성(OK 확인) 항목

### 우선순위 Top 5 (실질 위험)

1. **[CRITICAL] JWT 인증 배선 (AUTH-001)** — `backend/main.py`에 `auth`/`auth_v2` 라우터 `include_router` 추가 + `.env.local`에 `JWT_SECRET` 설정 + `configure()` 호출. 적용 전 대시보드가 토큰을 전달하도록 프론트도 함께 수정해야 잠금 사고 방지 → **HITL**.
2. **[CRITICAL/HIGH] 전 엔드포인트 인증 강제 (ENDPOINTS-001, AUTH-002)** — `positions/trading/risk` 라우터에 Bearer 검증 추가(`admin.py` 패턴 이식). 재시작 필요.
3. **[HIGH] ngrok 공개 노출 차단 (NGROK-001)** — ngrok에 basic-auth 추가(`--basic-auth`) 또는 IP 허용목록, 미사용 시 터널 종료. **외부 영향 → 사용자 결정**.
4. **[HIGH] 백엔드 바인딩 축소 (NET-001)** — `scripts/start-local.sh`의 `--host 0.0.0.0` → `127.0.0.1`. 대시보드가 동일 호스트라 영향 없음. 백엔드 재시작 필요.
5. **[HIGH] CORS 우회 프록시 (CORS-001)** — `frontend/next.config.js`의 `rewrites`가 서버사이드 프록시라 CORS를 우회. 조건부 rewrites 또는 인증 토큰 전달로 보완. 프론트 재시작 필요.

## 전체 발견사항

| # | 심각도 | 판정 | ID | 차원 | 제목 | fix_safety |
|---|--------|------|----|----|------|-----------|
| 1 | 🔴 critical | CONFIRMED | AUTH-001 | api-authz | 인증 엔드포인트(/api/auth/*, /api/auth/v2/*) 완전 미등록 | risky_hitl |
| 2 | 🔴 critical | CONFIRMED | ENDPOINTS-001 | api-authz | GET 조회 엔드포인트도 인증 미 적용 | needs_restart |
| 3 | 🟠 high | CONFIRMED | AUTH-002 | api-authz | 주요 mutating 엔드포인트 인증 미들웨어 없음 | needs_restart |
| 4 | 🟠 high | CONFIRMED | NET-001 | api-authz | 백엔드 API 0.0.0.0:8000 전인터페이스 바인딩 (인증 없을 때) | needs_restart |
| 5 | 🟠 high | CONFIRMED | CORS-001 | api-authz | CORS allow_origins 설정은 localhost로 제한(OK) | needs_restart |
| 6 | 🟠 high | CONFIRMED | INJ-002 | code-injection-deps | Prompt injection via API-sourced position data in claude CLI invocation | safe_auto |
| 7 | 🟠 high | CONFIRMED | NET-001 | network-exposure | Backend uvicorn 전 인터페이스 바인딩(0.0.0.0:8000) | needs_restart |
| 8 | 🟠 high | CONFIRMED | AUTH-001 | network-exposure | 계좌/포지션 엔드포인트 인증 없음 | needs_restart |
| 9 | 🟠 high | CONFIRMED | AUTH-002 | network-exposure | 주문 실행 엔드포인트(/api/trading/order) 보호 없음 | needs_restart |
| 10 | 🟠 high | CONFIRMED | CORS-001 | network-exposure | CORS allow_origins가 localhost만으로 제한되었지만 ngrok rewrites로 우회 가능 | needs_restart |
| 11 | 🟠 high | CONFIRMED | NGROK-001 | network-exposure | ngrok 대시보드 공개 터널(https://myspace-wagon-elephant.ngrok-free.dev) 인증 없음 | needs_restart |
| 12 | 🟠 high | CONFIRMED | MONITOR-001 | network-exposure | launchd 로그 파일 권한(logs/*.log) 확인 필요 | safe_auto |
| 13 | 🟠 high | CONFIRMED | BAR-OPS-17-LIVE-FLAG-SINGLE | order-safety | LIVE_TRADING_ENABLED check is single-point-of-failure in LiveOrderGate._preflight() | needs_restart |
| 14 | 🟠 high | CONFIRMED | BAR-OPS-35-LOSS-LATCH-STICKY | order-safety | Daily loss limit sticky latch relies on file state — not atomic across processes | needs_restart |
| 15 | 🟠 high | CONFIRMED | CRED-001 | secrets-creds | Telegram 봇 토큰 평문으로 로그 파일에 노출 | needs_restart |
| 16 | 🟡 medium | CONFIRMED | UI-001 | network-exposure | 프론트엔드 로그인 페이지 또는 인증 guard 없음 | needs_restart |
| 17 | 🟡 medium | CONFIRMED | BAR-OPS-17-ENV-ONLY | order-safety | KIWOOM_BASE_URL is sole API endpoint control — no code-level enforcement | needs_restart |
| 18 | 🟡 medium | CONFIRMED | BAR-OPS-17-DRY-RUN-INVERSION | order-safety | dry_run flag derived inversely from LIVE_TRADING_ENABLED — ambiguous semantics | needs_restart |
| 19 | 🟡 medium | CONFIRMED | BAR-OPS-17-GATE-WRAPPER-BYPASSED | order-safety | LiveOrderGate does not wrap all order paths — cancel_order bypasses gate | needs_restart |
| 20 | 🟡 medium | CONFIRMED | BAR-OPS-17-MOCK-INFRASTRUCTURE-DEPENDENT | order-safety | Mock API traffic assumes mockapi.kiwoom.com remains separate infrastructure | risky_hitl |
| 21 | 🟡 medium | PLAUSIBLE | BAR-OPS-17-NO-ACCOUNT-VALIDATION | order-safety | No runtime account number validation — wrong account can trade if oauth token valid | safe_auto |
| 22 | 🟡 medium | CONFIRMED | CRED-002 | secrets-creds | HTTP 요청 실패 시 전체 URL이 예외에 포함되어 로그 노출 위험 | safe_auto |
| 23 | 🟡 medium | CONFIRMED | CRED-004 | secrets-creds | ngrok 공개 도메인이 코드와 로그에 평문으로 기록되어 있음 | needs_restart |
| 24 | 🟡 medium | CONFIRMED | CRED-006 | secrets-creds | .env.example에 예제 값이 없지만 Postgres 기본 패스워드 'barro'가 명시되어 있음 | safe_auto |
| 25 | 🔵 low | CONFIRMED | TELEGRAM-002 | api-authz | 텔레그램 봇 chat_id 화이트리스트 검증 있음(OK) | safe_auto |
| 26 | 🔵 low | PLAUSIBLE | SECRETS-001 | api-authz | 텔레그램 봇 토큰 + 채팅 ID .env.local 평문 저장 | safe_auto |
| 27 | 🔵 low | CONFIRMED | INJ-001 | code-injection-deps | Unhandled ValueError in telegram command parameter parsing | needs_restart |
| 28 | 🔵 low | CONFIRMED | INJ-003 | code-injection-deps | Git branch name injection via unsanitized stock symbol | safe_auto |
| 29 | 🔵 low | CONFIRMED | INJ-004 | code-injection-deps | Missing input validation in telegram /cancel_order command - symbol and order_no not validated | safe_auto |
| 30 | 🔵 low | CONFIRMED | INJ-005 | code-injection-deps | Large JSON deserialization without size limit in position fetching | safe_auto |
| 31 | 🔵 low | CONFIRMED | BAR-OPS-17-KILL-SWITCH-NOT-ENFORCED | order-safety | KillSwitch trips but manual telegram commands bypass RiskEngine.is_active check | needs_restart |
| 32 | 🔵 low | CONFIRMED | CRED-003 | secrets-creds | 환경 파일 백업(.env.local.bak, .env.local.backup-*) 권한은 정상이나 다수 백업 파일로 인한 관리 복잡성 | safe_auto |
| 33 | 🔵 low | CONFIRMED | CRED-005 | secrets-creds | Telegram 및 Kiwoom 토큰이 아직 SecretStr로 관리되지 않음 (리팩터링 미완) | needs_restart |
| 34 | ⚪ info | CONFIRMED | TELEGRAM-001 | api-authz | 텔레그램 봇 /sim_execute, /sell_execute 명령의 2FA 토큰 검증 있음(OK) | safe_auto |
| 35 | ⚪ info | CONFIRMED | ADMIN-001 | api-authz | Admin 라우터(/api/admin/*)는 JWT + ADMIN role 검증 있음(OK) | safe_auto |
| 36 | ⚪ info | CONFIRMED | DEP-001 | code-injection-deps | PyYAML version requirement | safe_auto |
| 37 | ⚪ info | CONFIRMED | CRED-007 | secrets-creds | 파일 권한 및 git 설정이 전반적으로 양호함 | safe_auto |

## 상세 (critical · high)

### 🔴 [CRITICAL] AUTH-001 — 인증 엔드포인트(/api/auth/*, /api/auth/v2/*) 완전 미등록
- **차원/판정**: api-authz / CONFIRMED · fix_safety=`risky_hitl`
- **근거**: backend/main.py:78-105 — auth.py와 auth_v2.py 라우터가 include_router 호출에 포함되지 않음. /api/auth/login, /api/auth/refresh, /api/auth/v2/register 등 모든 인증 엔드포인트가 앱에 등록되지 않아 호출 불가능. 동시에 JWT_SECRET이 .env.local에 미설정 상태.
- **영향**: JWT 기반 인증 체계가 아예 작동하지 않음. 타 모든 민감 엔드포인트가 토큰 검증 없이 노출됨.
- **수정안**: main.py에 auth/auth_v2 라우터 include_router 추가: from backend.api.routes.auth import router as auth_router / from backend.api.routes.auth_v2 import router as auth_v2_router / app.include_router(auth_router) / app.include_router(auth_v2_router). .env.local에 JWT_SECRET=<16chars+> 추가. backend/main.py 또는 lifespan에서 auth_v2.configure() 호출.
- **검증**: All aspects of the AUTH-001 finding are verified as factually accurate: (1) auth.py and auth_v2.py routers exist and define FastAPI APIRouter instances with /api/auth and /api/auth/v2 prefixes respectively, but (2) main.py lines 78-105 register 12 other routers while completely omitting auth/auth_v2 imports and include_router calls. (3) Both routers export configure() functions that are never call

### 🔴 [CRITICAL] ENDPOINTS-001 — GET 조회 엔드포인트도 인증 미 적용
- **차원/판정**: api-authz / CONFIRMED · fix_safety=`needs_restart`
- **근거**: backend/api/routes/positions.py:51-182 (/api/accounts/balance, /api/positions), backend/api/routes/trading.py:248-287 (/api/trading/orders), backend/api/routes/risk.py:46-131 (/api/risk/status, /api/risk/events, /api/risk/audit) — 계좌 잔고, 포지션, 감시종목, 주문 이력, 리스크 상태 조회 엔드포인트 모두 인증 없음. 민감한 계좌 정보 노출.
- **영향**: 누구든 계좌 잔고, 포지션, 주문 이력, 리스크 상태 조회 가능. 정보 기반 시세 조종이나 거래 패턴 분석 공격 가능.
- **수정안**: 모든 /api/* 엔드포인트에 최소한 read_only 토큰 검증 추가. backend/api/routes/admin.py:46-59처럼 요청 헤더에서 Bearer 토큰 추출 후 JWTService.decode() 검증.
- **검증**: All GET/POST/PUT/DELETE endpoints in positions.py, trading.py, and risk.py lack authentication checks. Direct code inspection shows no Bearer token validation, no JWT decoding, and no auth middleware in main.py. Admin.py implements authentication pattern via _check_admin_token(), but this pattern is absent from all other routes. TenantContextMiddleware exists but is not registered in main.py. The 

### 🟠 [HIGH] AUTH-002 — 주요 mutating 엔드포인트 인증 미들웨어 없음
- **차원/판정**: api-authz / CONFIRMED · fix_safety=`needs_restart`
- **근거**: backend/api/routes/trading.py:89-167 (/api/trading/order POST, DELETE 주문 실행/취소), backend/api/routes/risk.py:134-170 (/api/risk/limits PUT), backend/api/routes/config.py:58-107 (/api/config PUT), backend/api/routes/positions.py:51-93 (모든 엔드포인트) — Depends(require_token) 또는 FastAPI 보안 의존성이 없음. 무누구나 주문, 리스크 한도 변경, 설정 변경 가능.
- **영향**: 인증 없이 누구든 포지션 조회, 주문 실행/취소, 리스크 한도 변경, 설정 변경 가능. 실거래 환경에서 무단 주문/청산 위험.
- **수정안**: 각 라우터에 FastAPI Depends 추가: from fastapi import Depends / async def require_token(token: str = Header(...)) → 토큰 검증 로직. 또는 미들웨어 레벨에서 인증 게이트. 모든 mutating 엔드포인트에 Depends(require_token) 추가. /api/admin/* 처럼 admin.py:46-59 _check_admin_token 패턴 참고.
- **검증**: Comprehensive code analysis confirms all claimed mutating endpoints (POST /api/trading/order, DELETE /api/trading/order/{id}, PUT /api/risk/limits, PUT /api/config, etc.) completely lack authentication dependencies or decorators. TenantContextMiddleware exists but is not registered in main.py. Admin routes demonstrate the proper _check_admin_token pattern, proving the codebase supports auth but ch

### 🟠 [HIGH] NET-001 — 백엔드 API 0.0.0.0:8000 전인터페이스 바인딩 (인증 없을 때)
- **차원/판정**: api-authz / CONFIRMED · fix_safety=`needs_restart`
- **근거**: 프로세스 확인: uvicorn backend.main:app --host 0.0.0.0 --port 8000. 로컬호스트뿐 아니라 모든 네트워크 인터페이스(외부 IP 포함)에서 uvicorn 서버 접근 가능. CORS는 localhost:3000/3001로 제한되지만, 인증 미들웨어 부재로 CORS 우회 가능.
- **영향**: 인증 없는 API가 인터넷/회사 네트워크 전체에 노출. 누구든 외부에서 주문, 잔고 조회, 청산 가능.
- **수정안**: uvicorn 실행 명령 또는 fastapi 앱 설정에서 --host 127.0.0.1로 제한: uvicorn backend.main:app --host 127.0.0.1 --port 8000. 또는 reverse proxy(nginx) 앞에서만 접근 허용.
- **검증**: Verified multiple concrete facts: (1) `/Users/beye82/Workspace/BarroAiTrade/scripts/start-local.sh` hardcodes `--host 0.0.0.0`; (2) uvicorn process actively running with 0.0.0.0 binding confirmed via ps; (3) `/Users/beye82/Workspace/BarroAiTrade/backend/main.py` registers only CORSMiddleware, not TenantContextMiddleware; (4) All trading endpoints (trading.py, positions.py, risk.py) have zero authe

### 🟠 [HIGH] CORS-001 — CORS allow_origins 설정은 localhost로 제한(OK)
- **차원/판정**: api-authz / CONFIRMED · fix_safety=`needs_restart`
- **근거**: backend/main.py:69-75 — CORSMiddleware(allow_origins=['http://localhost:3000', 'http://localhost:3001'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
- **영향**: 긍정적: CORS 정책 자체는 localhost만 허용. 다만 인증 미들웨어 부재로 CORS 우회 의미 희석. 0.0.0.0 바인딩 때문에 네트워크 경계에서 외부 IP로도 직접 접근 가능.
- **수정안**: CORS 설정은 양호하나 인증 미들웨어와 함께 작동해야 실효. AUTH-001, AUTH-002, NET-001 해결 시 함께 작동.
- **검증**: Code inspection verified all claims: CORS allows only localhost origins (backend/main.py:69-75), backend binds to 0.0.0.0:8000 (scripts/start-local.sh:29), no global authentication middleware is registered (only CORSMiddleware present in main.py), and endpoints like /api/trading/start and /api/status have zero auth requirements. Direct HTTP access (verified via curl) bypasses CORS entirely. The co

### 🟠 [HIGH] INJ-002 — Prompt injection via API-sourced position data in claude CLI invocation
- **차원/판정**: code-injection-deps / CONFIRMED · fix_safety=`safe_auto`
- **근거**: /Users/beye82/Workspace/BarroAiTrade/scripts/loss_watch_agent.py:468-500 — Stock symbol (`sym`), name (`name`), and finding evidence from API/file are interpolated directly into a prompt string at lines 473-477, then passed to claude CLI subprocess at line 494-500: `prompt = f"""...{name} ({sym})...{finding['evidence']}...{fix_hint}..."""` followed by `subprocess.run([CLAUDE_BIN, "-p", prompt, ...])`

Position data comes from `/api/positions` (line 184-186) which is untrusted external input. While subprocess.run with list form prevents shell injection, the prompt string itself can be weaponized to inject instructions into the LLM via prompt injection techniques (e.g., closing the docstring, injecting new instructions).
- **영향**: Attacker can craft API response with malicious stock names/symbols containing LLM instruction injection payloads (e.g., 'Ignore previous instructions...' or similar). This could cause the claude fix-agent to perform unintended actions, bypass safety guidelines, or reveal system information. Since fix-agent creates git branches and modifies code, this is a serious escalation vector.
- **수정안**: Sanitize position data before interpolating into prompts:
```python
import re
def sanitize_for_prompt(s: str) -> str:
    """Remove special prompt injection characters."""
    # Remove triple quotes, newlines, and suspicious patterns
    s = str(s or '').replace('"""', '').replace('\n', ' ').strip()
    return re.sub(r'[^\w\s()\-.]', '', s)[:100]  # Max 100 chars

prompt = f"""...
- 종목: {sanitize_for_prompt(name)} ({sanitize_for_prompt(sym)})
- 증거: {sanitize_for_prompt(finding['evidence'])}
..."""
```
- **검증**: Prompt injection vulnerability is real and exploitable. Data flows: API endpoint (/api/positions, lines 184-186) → position name/symbol extracted unfiltered (lines 189, 194) → classify() creates finding['evidence'] without sanitization (lines 355, 363, 370, 377) → dispatch_fix_agent() interpolates directly into f-string prompt (lines 473-478) → passed to subprocess.run with --permission-mode bypas

### 🟠 [HIGH] NET-001 — Backend uvicorn 전 인터페이스 바인딩(0.0.0.0:8000)
- **차원/판정**: network-exposure / CONFIRMED · fix_safety=`needs_restart`
- **근거**: /Users/beye82/Workspace/BarroAiTrade/scripts/start-local.sh:28-31 — 'exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000'. /Users/beye82/Library/LaunchAgents/com.barroaitrade.backend.plist:10 에서 start-local.sh 호출
- **영향**: 시스템 네트워크의 모든 인터페이스(WiFi, Ethernet 등)에서 백엔드 API에 접근 가능. 라우터 설정에 따라 WAN에도 노출될 수 있음. mock-live이지만 계좌정보 직접 유출.
- **수정안**: start-local.sh에서 --host 0.0.0.0 을 --host 127.0.0.1로 변경하거나, 환경변수로 HOST=127.0.0.1 설정. 또는 로컬 방화벽(pfctl) 또는 reverse proxy(nginx:127.0.0.1:8080 → unix://backend.sock) 적용.
- **검증**: Start-local.sh explicitly contains hardcoded --host 0.0.0.0 for uvicorn (line 28-31). Runtime netstat confirms binding to *.8000 (all interfaces). No HOST environment variable override in .env.local. Current process (PID 95095) demonstrates actual 0.0.0.0 binding. Backend API is accessible from any network interface without authentication. While mock environment mitigates financial risk, account c

### 🟠 [HIGH] AUTH-001 — 계좌/포지션 엔드포인트 인증 없음
- **차원/판정**: network-exposure / CONFIRMED · fix_safety=`needs_restart`
- **근거**: /Users/beye82/Workspace/BarroAiTrade/backend/api/routes/positions.py:51-92 (@router.get('/accounts/balance')) — 인증 체크 없음. 동일 파일 95라인 (@router.get('/positions')) 동일. curl -s http://localhost:8000/api/accounts/balance 실행 결과 실제 계좌 잔고(42,430,072 KRW), 보유종목(테스 72주, 원익IPS 164주), PnL 데이터 반환.
- **영향**: 인증 없이 GET 요청만으로 계좌 잔고, 보유 포지션, 수익률 등 민감 금융정보 노출. /api/trading/start, /api/trading/stop, /api/trading/order 도 동일하게 보호 안됨 (trading.py:33, 56, 89). 공개 URL(ngrok)을 통해 누구나 접근 가능.
- **수정안**: FastAPI Depends 또는 미들웨어로 route-level JWT 인증 추가. positions.py의 @router.get('/accounts/balance') 및 trading.py의 모든 POST/DELETE 엔드포인트에 @require_auth_jwt 데코레이터 적용. 또는 backend.api.middleware:TenantContextMiddleware를 enforce 모드로 변경(현재는 경고만 함).
- **검증**: Verified at exact code locations: positions.py (lines 51-92, 95) and trading.py (lines 33, 56, 89, 170, 210, 248) contain no authentication checks. TenantContextMiddleware is defined but not registered in main.py. Zero occurrences of authentication patterns (Depends, Bearer, authorization headers) in these route files. Contrast with admin.py which explicitly implements _check_admin_token and calls

### 🟠 [HIGH] AUTH-002 — 주문 실행 엔드포인트(/api/trading/order) 보호 없음
- **차원/판정**: network-exposure / CONFIRMED · fix_safety=`needs_restart`
- **근거**: /Users/beye82/Workspace/BarroAiTrade/backend/api/routes/trading.py:89-99 — POST /api/trading/order 에 @require_auth 또는 Depends(get_current_user) 없음. line 33-53의 /api/trading/start, line 56-75의 /api/trading/stop 도 동일.
- **영향**: 인증 없이 누구나 POST /api/trading/order 로 실거래 주문 시뮬레이션 가능. 현재는 mock-api(mockapi.kiwoom.com)이지만, KIWOOM_BASE_URL을 실거래 서버로 변경하면 무단 매매 가능. trading_state 제어(/trading/start, /trading/stop)도 보호 불가.
- **수정안**: trading.py의 place_order(), start_trading(), stop_trading() 함수에 @require_auth_jwt 데코레이터 추가. JWTService를 security/auth.py에서 로드하고, 토큰 검증 실패 시 401 반환.
- **검증**: All trading endpoints (POST /trading/start, /trading/stop, /trading/order, DELETE /trading/order/{id}, GET /trading/order/{id}, /trading/orders) confirmed to lack authentication decorators or token validation. JWTService and RBACPolicy infrastructure exists and is used in admin.py, but trading.py does not utilize these mechanisms. Current mock API configuration prevents actual financial loss, but 

### 🟠 [HIGH] CORS-001 — CORS allow_origins가 localhost만으로 제한되었지만 ngrok rewrites로 우회 가능
- **차원/판정**: network-exposure / CONFIRMED · fix_safety=`needs_restart`
- **근거**: /Users/beye82/Workspace/BarroAiTrade/backend/main.py:69-75 — CORSMiddleware allow_origins=['http://localhost:3000', 'http://localhost:3001']. 그러나 /Users/beye82/Workspace/BarroAiTrade/frontend/next.config.js:8-16 에서 'async rewrites() { source: /api/:path*, destination: ${backendUrl}/api/:path* }' 설정. 결과: 브라우저는 프론트엔드만 접근하고, 프론트엔드 서버가 백엔드로 프록시(서버-투-서버). CORS 브라우저 SOP 우회.
- **영향**: CORS 제한은 크로스도메인 브라우저 요청만 차단. 프론트엔드의 서버사이드 rewrites는 브라우저 정책이 아니므로 제한 받지 않음. 따라서 ngrok(https://myspace-wagon-elephant.ngrok-free.dev)으로 들어온 요청이 Next.js에서 백엔드로 자동 프록시되어 CORS 우회. 결과: 인증 없이 민감 데이터 조회 가능 (실제 확인: curl https://myspace-wagon-elephant.ngrok-free.dev/api/accounts/balance 실행 시 실제 계좌정보 반환).
- **수정안**: next.config.js에서 rewrites 제거하거나, 조건부 rewrites(요청이 localhost에서만 올 때만) 추가. 또는 Next.js API routes로 서버컴포넌트화 + JWT 토큰 검증. 더 나은 방안: 프론트엔드도 .env NEXT_PUBLIC_API_URL을 localhost:8000으로 설정하되, 개발 환경에서만 rewrites 사용.
- **검증**: The CORS bypass vulnerability is fully confirmed. The backend restricts CORS to localhost:3000/3001, but Next.js next.config.js has server-side rewrites that proxy /api/* requests to the backend. When accessed via the ngrok domain (https://myspace-wagon-elephant.ngrok-free.dev), the browser's Same-Origin Policy applies to that domain, but the CORS restriction is bypassed because the Next.js server

### 🟠 [HIGH] NGROK-001 — ngrok 대시보드 공개 터널(https://myspace-wagon-elephant.ngrok-free.dev) 인증 없음
- **차원/판정**: network-exposure / CONFIRMED · fix_safety=`needs_restart`
- **근거**: /Users/beye82/Library/LaunchAgents/com.barroaitrade.dashboard-ngrok.plist:8-15 — '<!-- ngrok 고정 도메인 터널: myspace-wagon-elephant.ngrok-free.dev -> :3000 --> ... /usr/local/bin/ngrok http --url=https://myspace-wagon-elephant.ngrok-free.dev 3000'. 동 plist에 basicAuth, OAuth, IP whitelist 설정 없음. ~/Library/Application\ Support/ngrok/ngrok.yml 에서 authtoken만 있고 basicAuth/oauth 미설정. 검증: curl https://myspace-wagon-elephant.ngrok-free.dev/api/accounts/balance 실행 → HTTP 200 + 민감 데이터 반환.
- **영향**: ngrok free tier 고정 URL이 인터넷 공개. 누구나 URL만 알면 (또는 소셜엔지니어링으로 URL 탈취) 계좌 잔고, 보유 포지션, 주문 이력 직접 조회 가능. POST /api/trading/order 로 주문도 가능. ngrok 대시보드(http://localhost:4040)도 로컬호스트에서만 보호(방화벽 의존).
- **수정안**: ngrok 명령어에 --basic-auth 또는 --oauth 추가. plist 수정: '/usr/local/bin/ngrok http --url=https://myspace-wagon-elephant.ngrok-free.dev --basic-auth=username:password 3000' 또는 ~/Library/Application\ Support/ngrok/ngrok.yml에 'http_tunnels:\n  dashboard:\n    addr: 3000\n    basic_auth: username:password' 추가. 더 나은 방안: ngrok OAuth(GitHub/Google) 또는 클라우드 서명 검증.
- **검증**: Verified that ngrok tunnel (myspace-wagon-elephant.ngrok-free.dev) exposes port 3000 which proxies API requests to unauthenticated backend endpoints. Confirmed: (1) plist has no --basic-auth/--oauth flags; (2) backend /api/accounts/balance and /api/trading/order have no auth decorators; (3) TenantContextMiddleware not applied globally; (4) frontend middleware only protects /admin UI, not /api rout

### 🟠 [HIGH] MONITOR-001 — launchd 로그 파일 권한(logs/*.log) 확인 필요
- **차원/판정**: network-exposure / CONFIRMED · fix_safety=`safe_auto`
- **근거**: /Users/beye82/Workspace/BarroAiTrade/logs/launchd.log (plist StandardOutPath), frontend.log, ngrok.log 등의 권한 미확인. 만약 644 이상이면 다른 사용자/프로세스가 로그(API 응답, 에러 메시지, 토큰 등) 읽기 가능.
- **영향**: 로그에 민감 정보(API 응답, 에러 메시지) 노출 가능성.
- **수정안**: logs 디렉토리를 chmod 700, 로그 파일을 chmod 600 으로 설정. 또는 logrotate에서 create 권한 지정.
- **검증**: Direct verification of /Users/beye82/Workspace/BarroAiTrade/logs shows: (1) logs directory is 755 (drwxr-xr-x), permitting world read access; (2) 39 of 40 log files are 644 (-rw-r--r--), world-readable; (3) Sensitive data confirmed present—Telegram bot token (bot8704522743:AAGHZbQ6KL-BQXax6UCCTA4f4MaLpx-tsJg) plaintext in telegram_bot.log, trading positions/symbols/prices in closing.log/barro.log,

### 🟠 [HIGH] BAR-OPS-17-LIVE-FLAG-SINGLE — LIVE_TRADING_ENABLED check is single-point-of-failure in LiveOrderGate._preflight()
- **차원/판정**: order-safety / CONFIRMED · fix_safety=`needs_restart`
- **근거**: backend/core/risk/live_order_gate.py:157-163
  if self._policy.require_env_flag and not self._executor._dry_run:
    flag = os.environ.get(self._policy.env_flag_name, '')
    if flag not in {'1', 'true', 'yes', 'on'}:
      raise TradingDisabled(...)
GatePolicy default: require_env_flag=True (defined line 108)
All main script instantiations use GatePolicy() with defaults (no override observed)
Check runs only when _dry_run=False AND require_env_flag=True
- **영향**: LIVE_TRADING_ENABLED validation only happens at gate execution time, not process startup. If dry_run is somehow toggled to False at runtime, no re-check of env flag occurs until next order. TradingDisabled exception is caught and logged; no system-level kill-switch prevents subsequent order retry by different path.
- **수정안**: 1) Move env flag check to process startup before any order execution. 2) Make require_env_flag non-optional (cannot be set to False in code). 3) Add code-level assertion in KiwoomNativeOrderExecutor constructor: validate LIVE_TRADING_ENABLED early.
- **검증**: Verification of the live_order_gate.py code confirms the core claims:

1. **Env flag check timing (CONFIRMED)**: The LIVE_TRADING_ENABLED check occurs exclusively at gate execution time (lines 157-163), not at process startup. The check via `os.environ.get()` is performed in _preflight() called from _gated() during each order placement.

2. **GatePolicy default (CONFIRMED)**: Line 108 shows `requi

### 🟠 [HIGH] BAR-OPS-35-LOSS-LATCH-STICKY — Daily loss limit sticky latch relies on file state — not atomic across processes
- **차원/판정**: order-safety / CONFIRMED · fix_safety=`needs_restart`
- **근거**: backend/core/risk/live_order_gate.py:170-188
  daily_loss_latch=_env_truthy('SUPERTREND_AUTO_LOSS_LATCH', '1') [default ON]
  latch_state_path=str(_DATA_DIR / 'daily_gate_state.json')
Multiple daemons (intraday_buy_daemon, supertrend_auto_trader, closing_bet_daemon) create independent gate instances each cycle.
Race condition: Daemon A reads is_latched()=False at 09:00, loss limit triggers at 09:05, persists latch. Daemon B started at 09:03 has separate gate instance, _loss_latch_date=None (memory), may allow re-entry if loss recovers before file read.
- **영향**: Loss limit sticky latch can have gaps across daemon cycle restarts. If daemon restarts between loss trigger and next order attempt, gate reloads with _loss_latch_date=None (memory only), allowing one more buy before file latch is checked.
- **수정안**: 1) Load latch state from file at gate.__init__() and restore _loss_latch_date immediately. 2) Use atomic file locking (fcntl on UNIX) for latch updates. 3) Unify gate instance singleton per process instead of recreating per cycle.
- **검증**: The daily loss limit sticky latch can be bypassed through a realistic failure scenario: (1) Gate instance detects loss and attempts to write latch state to file, (2) File write fails (OSError caught and logged), (3) Daemon process restarts, creating a new gate instance with _loss_latch_date=None in memory and empty file state, (4) If market recovers above loss limit before next order, the new gate

### 🟠 [HIGH] CRED-001 — Telegram 봇 토큰 평문으로 로그 파일에 노출
- **차원/판정**: secrets-creds / CONFIRMED · fix_safety=`needs_restart`
- **근거**: logs/telegram_bot.err(17,335회), logs/cb_alert.log(2회), logs/telegram_bot.log(1회)에서 HTTPStatusError 예외 메시지를 그대로 로깅. 토큰: bot8704522743:AAGHZbQ6 (마스킹됨), bot8702111866:AAGMbGB2 (마스킹됨). 원인: backend/core/notify/telegram_bot.py:133 라인에서 `logger.warning('poll cycle failed: %s: %s', type(e).__name__, e)` - 예외 객체 e가 전체 URL을 포함한 HTTPStatusError 문자열로 변환되어 로깅됨.
- **영향**: Telegram 봇 토큰이 평문 로그에 기록되어 있어, 로그 파일에 접근 가능한 공격자가 토큰을 탈취하여 봇을 제어할 수 있음. 로그 파일들은 644(공개) 권한으로 설정되어 있지 않지만, 백업, 모니터링, 분석 도구 등을 통해 노출 가능.
- **수정안**: backend/core/notify/telegram_bot.py:133에서 예외를 안전하게 로깅하기: `logger.warning('poll cycle failed: %s — retrying in %ds', type(e).__name__, _backoff)` (e 제거). httpx.HTTPStatusError를 명시적으로 캐치하여 URL 없이 상태 코드와 이유만 로깅. 또한 기존 로그 파일들(telegram_bot.err, telegram_bot.log, cb_alert.log)의 토큰 항목들을 정리해야 함.
- **검증**: The code at backend/core/notify/telegram_bot.py:133 does log the exception object containing full HTTPStatusError URLs with bot tokens. Verification found 17,328 token instances in telegram_bot.err, 1 in telegram_bot.log, and 2 current-active-token instances in cb_alert.log. Log files are world-readable (644). The old bot token (bot8704522743) was exposed via commit 889cb85 (2026-05-20) which adde

## 양호 확인 (positive)

- **CORS-001**: CORS allow_origins 설정은 localhost로 제한(OK)
- **TELEGRAM-002**: 텔레그램 봇 chat_id 화이트리스트 검증 있음(OK)
- **TELEGRAM-001**: 텔레그램 봇 /sim_execute, /sell_execute 명령의 2FA 토큰 검증 있음(OK)
- **ADMIN-001**: Admin 라우터(/api/admin/*)는 JWT + ADMIN role 검증 있음(OK)
- **DEP-001**: PyYAML version requirement
- **CRED-007**: 파일 권한 및 git 설정이 전반적으로 양호함

---

*이 리포트는 멀티에이전트 보안 워크플로우(run `wf_6501b2a8-45e`)의 적대적 검증 통과 결과다. 모든 발견은 mock 환경 현실 영향으로 보정됨. 실거래 주문 엔드포인트는 조사 중 호출되지 않았다.*