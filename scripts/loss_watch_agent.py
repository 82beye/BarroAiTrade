#!/usr/bin/env python3
"""loss_watch_agent.py — 라이브 손실 감시 + 상세분석 + 버그픽스 제안 에이전트 (BAR-OPS).

장중(09:00–15:30 KST) 보유 포지션과 당일 매매이력을 주기적으로 감시한다.
손실(미실현 ≤ LOSSWATCH_UNREAL_PCT, 기본 -3% 또는 당일 실현손실)이 발생하면 손실종목을
상세 분석하고, '실행/로직 버그'로 의심되는 경우 별도 git 워크트리·브랜치에 패치 초안을 만드는
Claude 픽스 에이전트를 디스패치한 뒤 텔레그램으로 사람에게 알린다.

자율도 = **제안+알림(propose)**: 라이브 코드/설정(.env.local·policy.json)/서비스 재기동은
절대 건드리지 않는다. 픽스 에이전트는 격리 워크트리 안에서만 동작하고 main 커밋·push 금지.
사람이 브랜치를 검토·머지하는 게이트가 항상 존재한다.

버그 분류 신뢰도:
  HIGH(→픽스에이전트 자동 디스패치):
    EXIT_FAILED  매도/청산 주문이 FAILED(체결 못함, 429 등) — 보유분 청산 막힘(가장 위험)
    SELL_BLOCKED 매도가 게이트에 의해 차단
    ETF_LEAK     EXCLUDE_ETF=1 인데 ETF 보유 — 탐지기 필터 누수
    QTY_ANOMALY  동전주 수량 폭주/비정상 노셔널 — 사이징 버그
  REVIEW(알림만, 자동수정 안 함 — 정상 보유일 수 있음):
    STOP_DEEP    스탑 한참 below 인데 미청산 — 미발동 의심 OR 정상(min_hold/RF/시간단계 SL)
    BIG_UNREAL   미실현 ≤ 트리거(-3%) 일반 손실(시장 손실)
    REALIZED     당일 실현손실 마감(시장 손실)

사용:
  ./.venv/bin/python scripts/loss_watch_agent.py                 # 장중 상시 감시(텔레그램+픽스에이전트)
  LOSSWATCH_ONCE=1 LOSSWATCH_DRYRUN=1 ... scripts/loss_watch_agent.py   # 1회 스캔, 무발송(테스트)

주요 env (기본값):
  LOSSWATCH_INTERVAL=90   스캔 주기(초)
  LOSSWATCH_UNREAL_PCT=-3.0  미실현 손실 트리거(%)
  LOSSWATCH_STOP_MARGIN=3.0  스탑 below 마진(pp) — STOP_DEEP 판정
  LOSSWATCH_STOP_GRACE=3     STOP_DEEP 지속 사이클 수
  LOSSWATCH_TELEGRAM=1   텔레그램 알림
  LOSSWATCH_FIX_AGENT=1  HIGH 버그시 Claude 픽스에이전트 디스패치
  LOSSWATCH_FIX_MAX=3    하루 픽스에이전트 최대 디스패치
  LOSSWATCH_FIX_TIMEOUT=900  픽스에이전트 타임아웃(초)
  LOSSWATCH_DRYRUN=0     1이면 텔레그램·에이전트 미발동(분석만 출력)
  LOSSWATCH_ONCE=0       1이면 1회 스캔 후 종료
  LOSSWATCH_MARKET_HOURS=1  1이면 09:00–15:35 KST 에만 동작
  LOSSWATCH_API=http://127.0.0.1:8000/api/positions
  BARRO_DATA_DIR / BARRO_REPO  라이브 데이터·레포 경로(워크트리 실행 시 메인레포로 지정)
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _envi(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _envb(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── 경로 ──────────────────────────────────────────────────────────────
SELF = os.path.abspath(__file__)
DEFAULT_REPO = os.path.dirname(os.path.dirname(SELF))
REPO = os.environ.get("BARRO_REPO") or DEFAULT_REPO
DATA = os.environ.get("BARRO_DATA_DIR") or os.path.join(REPO, "data")
ENV_LOCAL = os.path.join(REPO, ".env.local")

# ── 설정 ──────────────────────────────────────────────────────────────
INTERVAL = _envi("LOSSWATCH_INTERVAL", 90)
UNREAL_PCT = _envf("LOSSWATCH_UNREAL_PCT", -3.0)
STOP_MARGIN = _envf("LOSSWATCH_STOP_MARGIN", 3.0)
STOP_GRACE = _envi("LOSSWATCH_STOP_GRACE", 3)
DO_TELEGRAM = _envb("LOSSWATCH_TELEGRAM", True)
DO_FIX_AGENT = _envb("LOSSWATCH_FIX_AGENT", True)
FIX_MAX = _envi("LOSSWATCH_FIX_MAX", 3)
FIX_TIMEOUT = _envi("LOSSWATCH_FIX_TIMEOUT", 900)
DRYRUN = _envb("LOSSWATCH_DRYRUN", False)
ONCE = _envb("LOSSWATCH_ONCE", False)
MARKET_HOURS = _envb("LOSSWATCH_MARKET_HOURS", True)
API = os.environ.get("LOSSWATCH_API", "http://127.0.0.1:8000/api/positions")
# cron 의 최소 PATH 에서도 픽스에이전트가 claude 를 찾도록 절대경로 폴백.
CLAUDE_BIN = (os.environ.get("LOSSWATCH_CLAUDE_BIN") or shutil.which("claude")
              or "/Applications/cmux.app/Contents/Resources/bin/claude")

STATE_PATH = os.path.join(DATA, ".loss_watch_state.json")
REPORT_DIR = os.path.join(REPO, "reports", "loss_incidents")

ETF_KEYWORDS = (
    "ETF", "ETN", "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KOSEF", "HANARO",
    "SOL ", "ACE ", "PLUS ", "RISE ", "TIMEFOLIO", "히어로즈", "마이티",
    "레버리지", "인버스", "선물", "채권", "국고채", "단기통안",
)


def now_kst():
    return datetime.now(KST)


def log(msg):
    print(f"[{now_kst():%H:%M:%S}] {msg}", flush=True)


# ── .env.local 파서 (텔레그램 토큰 등) ────────────────────────────────
def load_env_local():
    cfg = {}
    if not os.path.exists(ENV_LOCAL):
        return cfg
    try:
        with open(ENV_LOCAL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.split("#", 1)[0].strip().strip('"').strip("'")
                cfg[k.strip()] = v
    except Exception as e:
        log(f"[WARN] .env.local 파싱 실패: {e}")
    return cfg


ENVCFG = load_env_local()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ENVCFG.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID") or ENVCFG.get("TELEGRAM_CHAT_ID", "")


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg_send(text):
    """텔레그램 sendMessage (HTML). DRYRUN/미설정이면 로그만."""
    if DRYRUN or not DO_TELEGRAM:
        log(f"[TG-SKIP] {text[:120].replace(chr(10),' ')}")
        return False
    if not TG_TOKEN or not TG_CHAT:
        log("[TG-WARN] 토큰/챗ID 없음 — 전송 생략")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for chunk_start in range(0, len(text), 3900):
        chunk = text[chunk_start:chunk_start + 3900]
        data = json.dumps({
            "chat_id": TG_CHAT, "text": chunk,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            log(f"[TG-ERR] {type(e).__name__}: {e}")
            return False
    return True


# ── 데이터 로더 ───────────────────────────────────────────────────────
def fetch_positions():
    """라이브 보유 포지션 (/api/positions). 실패 시 active_positions.json 폴백(미실현 없음)."""
    try:
        with urllib.request.urlopen(API, timeout=6) as r:
            d = json.loads(r.read().decode())
        rows = d if isinstance(d, list) else d.get("positions", d.get("data", []))
        out = {}
        for p in rows:
            sym = str(p.get("symbol") or p.get("code") or "").zfill(6)
            if not sym or sym == "000000":
                continue
            out[sym] = {
                "symbol": sym,
                "name": p.get("name", ""),
                "qty": float(p.get("quantity", p.get("qty", 0)) or 0),
                "avg_price": float(p.get("avg_price", p.get("entry_price", 0)) or 0),
                "cur_price": float(p.get("cur_price", p.get("current_price", 0)) or 0),
                "pnl_rate": float(p.get("pnl_rate", 0) or 0),
                "strategy": p.get("strategy", ""),
            }
        return out, True
    except Exception as e:
        log(f"[API-WARN] /api/positions 실패({type(e).__name__}) — active_positions 폴백")
        ap = read_active_positions()
        return {s: {"symbol": s, "name": v.get("name", ""), "qty": 0,
                    "avg_price": float(v.get("entry_price", 0) or 0),
                    "cur_price": 0.0, "pnl_rate": 0.0,
                    "strategy": v.get("strategy", "")} for s, v in ap.items()}, False


def read_active_positions():
    path = os.path.join(DATA, "active_positions.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def read_orders_today():
    """order_audit.csv 의 당일 행. symbol(6자리) 키로 그룹."""
    path = os.path.join(DATA, "order_audit.csv")
    day = now_kst().strftime("%Y-%m-%d")
    by_sym = {}
    if not os.path.exists(path):
        return by_sym
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not (r.get("ts", "") or "").startswith(day):
                    continue
                sym = str(r.get("symbol", "")).zfill(6)
                by_sym.setdefault(sym, []).append(r)
    except Exception as e:
        log(f"[WARN] order_audit 읽기 실패: {e}")
    return by_sym


def read_fills_today():
    path = os.path.join(DATA, "fill_audit.csv")
    day = now_kst().strftime("%Y%m%d")
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") == day]
    except Exception:
        pass
    return rows


def is_etf(name):
    n = (name or "").upper()
    return any(k.upper() in n for k in ETF_KEYWORDS)


# ── 상태 (dedup / 직전 스냅샷 / 픽스 카운트) ──────────────────────────
def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {"date": "", "seen": [], "fix_count": 0, "last_positions": {}}


def save_state(st):
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"[WARN] state 저장 실패: {e}")


def roll_state_for_day(st):
    today = now_kst().strftime("%Y%m%d")
    if st.get("date") != today:
        st["date"] = today
        st["seen"] = []
        st["fix_count"] = 0
        # last_positions 는 유지(전일 마감→당일 시초 비교 의미 적음, 그대로 둠)
    return st


# ── 효과적 스탑 추정 ──────────────────────────────────────────────────
def effective_stop(pos, ap_entry):
    """포지션의 의도된 스탑(%) 근사. active_positions.sl_pct 우선, 없으면 -4."""
    try:
        sl = ap_entry.get("sl_pct")
        if sl is not None:
            return float(sl)
    except Exception:
        pass
    return -4.0


def hold_minutes(entry_time):
    if not entry_time:
        return None
    try:
        et = datetime.fromisoformat(entry_time)
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - et).total_seconds() / 60.0
    except Exception:
        return None


# 버그 유형별 의심 코드영역 + 픽스 힌트 (픽스에이전트 프롬프트용)
SUSPECT = {
    "EXIT_FAILED": (
        "scripts/intraday_buy_daemon.py (매도/청산 경로), backend/core 의 키움 주문 클라이언트"
        "(429 재시도/백오프), backend/core/risk/live_order_gate.py",
        "매도 주문 실패가 429/HTTPStatusError 면 청산 경로에 지수백오프 재시도·레이트리미터·"
        "치명 시 텔레그램 강알림이 있는지 확인. 보유분 청산이 영구 실패하지 않도록 보장.",
    ),
    "SELL_BLOCKED": (
        "backend/core/risk/live_order_gate.py (게이트), 호출부",
        "매도(side=sell)가 매수용 한도/게이트에 잘못 걸려 차단되는지 확인. 청산은 차단 예외여야 함.",
    ),
    "ETF_LEAK": (
        "backend/legacy_scalping/scanner/daily_screener.py(_is_etf), "
        "backend/core/limit_up_chase_trader.py(_is_etf_or_etn), 각 피커의 EXCLUDE_ETF 배선",
        "EXCLUDE_ETF=1 인데 ETF 가 진입된 경로(피커/탐지기)를 찾아 ETF 필터가 그 경로에도 "
        "적용되는지 확인. 키워드 누락 가능성도 점검.",
    ),
    "QTY_ANOMALY": (
        "매수 수량 사이징 로직(전략별 picker/simulate), min_price 필터(c5991b2 참조)",
        "동전주/저가주에 수량이 폭주하는 사이징 버그인지 확인. min_price·노셔널 캡이 해당 경로에 "
        "적용되는지 점검.",
    ),
}


def classify(sym, pos, ap_entry, orders, streak):
    """포지션 1종목의 손실 findings 목록 반환. 각 finding: dict."""
    findings = []
    name = pos.get("name", "")
    rate = pos.get("pnl_rate", 0.0)
    qty = pos.get("qty", 0.0)
    cur = pos.get("cur_price", 0.0)
    strat = pos.get("strategy", "") or ap_entry.get("strategy", "")

    sells_failed = [o for o in orders if o.get("side") == "sell" and o.get("action") == "FAILED"]
    sells_blocked = [o for o in orders if o.get("side") == "sell"
                     and (str(o.get("blocked", "")).lower() in ("1", "true", "yes")
                          or o.get("action") == "BLOCKED")]
    sells_ordered = [o for o in orders if o.get("side") == "sell" and o.get("action") == "ORDERED"]

    # ── HIGH: 청산 주문 실패 (보유분 청산 막힘) ──
    if sells_failed:
        last = sells_failed[-1]
        findings.append(dict(
            type="EXIT_FAILED", confidence="HIGH", auto_fix=True, severity="critical",
            title=f"청산 주문 실패 {len(sells_failed)}건 — 보유분 매도 막힘",
            evidence=f"rc={last.get('return_code')} reason={(last.get('reason') or '')[:120]}",
        ))
    # ── HIGH: 매도 차단 ──
    if sells_blocked:
        last = sells_blocked[-1]
        findings.append(dict(
            type="SELL_BLOCKED", confidence="HIGH", auto_fix=True, severity="high",
            title=f"매도 게이트 차단 {len(sells_blocked)}건",
            evidence=f"reason={(last.get('reason') or '')[:120]}",
        ))
    # ── HIGH: ETF 누수 ──
    if is_etf(name):
        findings.append(dict(
            type="ETF_LEAK", confidence="HIGH", auto_fix=True, severity="high",
            title=f"ETF 보유(필터 누수): {name}",
            evidence=f"EXCLUDE_ETF=1 정책 위반 추정, 전략={strat}",
        ))
    # ── HIGH: 수량 폭주 ──
    if cur and cur < 1000 and qty >= 1000:
        findings.append(dict(
            type="QTY_ANOMALY", confidence="HIGH", auto_fix=True, severity="high",
            title=f"저가주 수량 폭주 의심: {int(qty)}주 @ {int(cur)}원",
            evidence=f"노셔널 {int(qty*cur):,}원, 전략={strat}",
        ))

    # ── REVIEW: 스탑 한참 below 인데 미청산 (자동수정 안 함) ──
    estop = effective_stop(pos, ap_entry)
    if rate <= estop - STOP_MARGIN and not sells_ordered and not sells_failed:
        hm = hold_minutes(ap_entry.get("entry_time"))
        findings.append(dict(
            type="STOP_DEEP", confidence="REVIEW", auto_fix=False, severity="high",
            title=f"스탑({estop:.1f}%) 한참 below 인데 미청산: {rate:.2f}%",
            evidence=(f"보유 {hm:.0f}분, 매도시도 0건. ⚠️미발동 의심 OR 정상보유"
                      f"(min_hold/RF지지/시간단계SL). 사람 판단 필요." if hm is not None
                      else "매도시도 0건. 미발동 의심 OR 정상보유. 사람 판단 필요."),
            streak=streak,
        ))

    # ── REVIEW: 일반 미실현 손실 (시장 손실, 트리거 충족) ──
    if rate <= UNREAL_PCT and not any(f["type"] == "STOP_DEEP" for f in findings):
        findings.append(dict(
            type="BIG_UNREAL", confidence="REVIEW", auto_fix=False, severity="info",
            title=f"미실현 손실 {rate:.2f}% (스탑 {estop:.1f}% 이내)",
            evidence=f"전략={strat}, 정상 손실구간(스탑 대기). 모니터링.",
        ))
    return findings


# ── 인시던트 리포트 ───────────────────────────────────────────────────
def write_report(day_dir, sym, pos, ap_entry, orders, findings):
    os.makedirs(day_dir, exist_ok=True)
    ts = now_kst().strftime("%H%M%S")
    path = os.path.join(day_dir, f"{sym}_{ts}.md")
    pnl = pos.get("pnl_rate", 0.0)
    lines = [
        f"# 손실 인시던트 — {pos.get('name','')} ({sym})",
        f"- 시각: {now_kst():%F %T} KST",
        f"- 전략: {pos.get('strategy') or ap_entry.get('strategy','')}",
        f"- 수량: {pos.get('qty')}  진입가: {ap_entry.get('entry_price', pos.get('avg_price'))}",
        f"- 현재가: {pos.get('cur_price')}  미실현: {pnl:.2f}%",
        f"- 의도 스탑(sl_pct): {ap_entry.get('sl_pct')}  peak: {ap_entry.get('peak_pnl_rate')}  trough: {ap_entry.get('trough_pnl_rate')}",
        f"- 진입시각: {ap_entry.get('entry_time')}",
        "",
        "## 당일 주문 이력",
    ]
    if orders:
        for o in orders[-12:]:
            lines.append(
                f"- {o.get('ts','')[-8:]} {o.get('action')} {o.get('side')} "
                f"qty={o.get('qty')} px={o.get('price')} rc={o.get('return_code')} "
                f"blocked={o.get('blocked')} reason={(o.get('reason') or '')[:80]}")
    else:
        lines.append("- (당일 주문 없음)")
    lines += ["", "## findings"]
    for f in findings:
        lines.append(f"- **[{f['confidence']}] {f['type']}** — {f['title']}")
        lines.append(f"    - {f['evidence']}")
    with open(path, "w", encoding="utf-8") as w:
        w.write("\n".join(lines) + "\n")
    return path


# ── 픽스 에이전트 디스패치 (격리 워크트리·브랜치, propose) ─────────────
def dispatch_fix_agent(sym, name, finding, report_path, day_dir):
    """HIGH 버그에 대해 Claude 픽스에이전트를 격리 워크트리에서 실행. 패치는 브랜치에만."""
    btype = finding["type"]
    day = now_kst().strftime("%Y%m%d")
    branch = f"auto/lossfix-{sym}-{btype.lower()}-{day}"
    wt = os.path.join(REPO, ".claude", "worktrees", f"lossfix-{sym}-{day}")
    agent_log = os.path.join(day_dir, f"{sym}_{btype}_agent.log")
    suspect_area, fix_hint = SUSPECT.get(btype, ("(미지정)", "(미지정)"))

    if DRYRUN or not DO_FIX_AGENT:
        log(f"[FIX-SKIP] {sym} {btype} (dryrun/off) → 브랜치 {branch} 예정")
        return None, branch, "(dryrun)"

    # 1) 격리 워크트리 생성 (origin/main 기준 — 코드+테스트만, 라이브 data 불요)
    try:
        if not os.path.isdir(wt):
            subprocess.run(["git", "-C", REPO, "worktree", "add", "-b", branch, wt, "origin/main"],
                           capture_output=True, text=True, timeout=120, check=True)
    except subprocess.CalledProcessError as e:
        # 브랜치가 이미 있으면 재사용 시도
        try:
            subprocess.run(["git", "-C", REPO, "worktree", "add", wt, branch],
                           capture_output=True, text=True, timeout=120, check=True)
        except Exception:
            log(f"[FIX-ERR] 워크트리 생성 실패: {e.stderr[:200] if e.stderr else e}")
            return None, branch, f"worktree-fail: {e}"
    except Exception as e:
        log(f"[FIX-ERR] 워크트리 생성 예외: {e}")
        return None, branch, f"worktree-exc: {e}"

    prompt = f"""너는 BarroAiTrade 실거래 시스템의 '버그픽스 제안' 에이전트다. 지금 격리된 git 워크트리
({wt}, 브랜치 {branch}) 안에 있다. 아래 라이브 손실 인시던트의 **실행/로직 버그**를 진단하고,
실제 버그라면 **최소 패치**를 이 브랜치에만 만들어라.

[인시던트]
- 종목: {name} ({sym})
- 버그유형: {btype} — {finding['title']}
- 증거: {finding['evidence']}
- 의심 코드영역: {suspect_area}
- 점검 지침: {fix_hint}
- 상세 리포트: {report_path}

[필수 규칙 — 위반 금지]
1. 진단을 먼저 하고, **진짜 버그일 때만** 코드를 수정한다(추정이면 수정 말고 진단만 보고).
2. 수정은 이 워크트리/브랜치 안에서만. **main 커밋·git push·태그 금지.**
3. `.env.local`, `policy.json`, `data/`, crontab, launchd plist, 어떤 서비스도 **수정·재기동 금지.**
4. 관련 테스트만 실행해 회귀 확인(예: `./.venv/bin/python -m pytest backend/tests/ -q -k "관련키워드"`).
   .venv 없으면 python3. 전체 테스트 장시간 실행 금지.
5. 변경은 최소·국소. 무관한 리팩터링 금지.

[출력(마지막에 요약)]
- 진단: 한 줄 결론(REAL_BUG / NOT_A_BUG / NEEDS_DATA)
- 변경파일: 목록(없으면 '없음')
- 테스트: 통과/실패 요약
- 사람 조치: 머지 권장 여부 + 주의사항
"""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "text",
           "--permission-mode", "bypassPermissions"]
    log(f"[FIX] 디스패치 {sym} {btype} → {branch} (timeout {FIX_TIMEOUT}s)")
    try:
        with open(agent_log, "w", encoding="utf-8") as lf:
            lf.write(f"# fix-agent {sym} {btype} {branch}\n# {now_kst():%F %T}\n\n")
            p = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=FIX_TIMEOUT)
            lf.write("=== STDOUT ===\n" + (p.stdout or "") + "\n=== STDERR ===\n" + (p.stderr or ""))
        out = (p.stdout or "").strip()
        summary = out[-600:] if out else f"(빈 출력, rc={p.returncode})"
        # 변경 여부
        diff = subprocess.run(["git", "-C", wt, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        changed = bool(diff)
        return changed, branch, summary
    except subprocess.TimeoutExpired:
        log(f"[FIX-ERR] {sym} {btype} 타임아웃 {FIX_TIMEOUT}s")
        return None, branch, f"timeout {FIX_TIMEOUT}s"
    except FileNotFoundError:
        log("[FIX-ERR] claude CLI 없음 — 디스패치 불가")
        return None, branch, "claude-cli-not-found"
    except Exception as e:
        log(f"[FIX-ERR] {sym} {btype} {type(e).__name__}: {e}")
        return None, branch, f"{type(e).__name__}: {e}"


# ── 1회 스캔 ──────────────────────────────────────────────────────────
def scan_once(st):
    positions, live = fetch_positions()
    active = read_active_positions()
    orders = read_orders_today()
    roll_state_for_day(st)
    streaks = st.setdefault("streaks", {})
    last_pos = st.get("last_positions", {})

    losers = 0
    bugs = 0
    day_dir = os.path.join(REPORT_DIR, now_kst().strftime("%Y-%m-%d"))

    # ── 1) 보유 종목 손실 검사 ──
    for sym, pos in positions.items():
        ap_entry = active.get(sym, {})
        rate = pos.get("pnl_rate", 0.0)
        estop = effective_stop(pos, ap_entry)
        # STOP_DEEP streak 관리
        if rate <= estop - STOP_MARGIN:
            streaks[sym] = streaks.get(sym, 0) + 1
        else:
            streaks[sym] = 0
        streak = streaks[sym]

        # 트리거: 미실현 ≤ -3% OR 구조적 버그흔적(매도 실패/차단 · ETF 보유 · 수량 폭주)
        sym_orders = orders.get(sym, [])
        cur = pos.get("cur_price", 0.0)
        qty = pos.get("qty", 0.0)
        qty_anom = bool(cur and cur < 1000 and qty >= 1000)
        has_struct_flag = (
            any(o.get("side") == "sell" and o.get("action") in ("FAILED", "BLOCKED")
                for o in sym_orders)
            or is_etf(pos.get("name", ""))
            or qty_anom
        )
        if rate > UNREAL_PCT and not has_struct_flag:
            continue

        findings = classify(sym, pos, ap_entry, sym_orders, streak)
        # STOP_DEEP 는 grace 미충족이면 보류
        findings = [f for f in findings
                    if not (f["type"] == "STOP_DEEP" and streak < STOP_GRACE)]
        if not findings:
            continue
        losers += 1

        report = write_report(day_dir, sym, pos, ap_entry, sym_orders, findings)

        for f in findings:
            key = f"{now_kst():%Y%m%d}:{sym}:{f['type']}"
            if key in st["seen"]:
                continue  # 이미 알린 인시던트
            st["seen"].append(key)

            badge = {"critical": "🔴", "high": "🟠", "info": "🟡"}.get(f["severity"], "⚪")
            head = (f"{badge} <b>손실감시</b> [{f['confidence']}] {f['type']}\n"
                    f"{_esc(pos.get('name',''))} ({sym}) · 미실현 {rate:.2f}% · 전략 {_esc(pos.get('strategy') or ap_entry.get('strategy',''))}\n"
                    f"{_esc(f['title'])}\n<code>{_esc(f['evidence'])}</code>")

            if f.get("auto_fix") and f["confidence"] == "HIGH":
                bugs += 1
                if st.get("fix_count", 0) >= FIX_MAX:
                    tg_send(head + f"\n⚠️ 픽스에이전트 일일한도({FIX_MAX}) 초과 — 디스패치 보류, 수동 검토 요망\n리포트: {report}")
                    continue
                st["fix_count"] = st.get("fix_count", 0) + 1
                save_state(st)  # 디스패치 전 카운트 확정
                changed, branch, summary = dispatch_fix_agent(sym, pos.get("name", ""), f, report, day_dir)
                if changed is True:
                    verdict = f"🛠 패치 초안 생성됨 → 브랜치 <code>{branch}</code> (검토·머지 게이트)"
                elif changed is False:
                    verdict = f"🔎 진단만(코드변경 없음) → 브랜치 <code>{branch}</code>"
                else:
                    verdict = f"⚠️ 픽스에이전트 미완({_esc(str(summary)[:80])}) — 수동 검토"
                tg_send(f"{head}\n{verdict}\n<code>{_esc(str(summary)[-500:])}</code>\n리포트: {report}")
            else:
                tg_send(f"{head}\n리포트: {report}")

    # ── 2) 당일 실현손실 (직전 스냅샷 대비 청산된 종목) ──
    closed = set(last_pos) - set(positions)
    for sym in closed:
        prev = last_pos.get(sym, {})
        sym_orders = orders.get(sym, [])
        sell = [o for o in sym_orders if o.get("side") == "sell" and o.get("action") == "ORDERED"]
        if not sell:
            continue
        entry = float(prev.get("avg_price", 0) or 0)
        # 체결가: order_audit 는 시장가 주문(price='MKT')이라 체결 직후엔 avg_fill_price 가
        #   비어있다 → float('MKT') 파싱불가. avg_fill_price 가 숫자일 때만 정확값,
        #   아니면 직전 스캔의 미실현률(시장가 청산 ≈ 그 부근)로 폴백 추정한다.
        #   (정확한 실현손익은 EOD fill_audit/ka10073 가 별도 확보.)
        sell_px = 0.0
        try:
            _afp = sell[-1].get("avg_fill_price")
            sell_px = float(_afp) if _afp not in (None, "") else 0.0
        except (TypeError, ValueError):
            sell_px = 0.0
        approx = False
        if entry > 0 and sell_px > 0:
            rpct = (sell_px - entry) / entry * 100
        else:
            try:
                _lp = prev.get("pnl")
                rpct = float(_lp) if _lp is not None else None
            except (TypeError, ValueError):
                rpct = None
            approx = True
        if rpct is not None and rpct < 0:
            key = f"{now_kst():%Y%m%d}:{sym}:REALIZED"
            if key not in st["seen"]:
                st["seen"].append(key)
                losers += 1
                tail = (f"(진입 {int(entry):,} → 매도 {int(sell_px):,})" if not approx
                        else f"(진입 {int(entry):,}, 직전스캔 미실현 기준 추정)")
                tg_send(f"🟡 <b>손실감시</b> [REALIZED] 실현손실 청산{' ~추정' if approx else ''}\n"
                        f"{_esc(prev.get('name',''))} ({sym}) · {rpct:+.2f}% {tail}")

    st["last_positions"] = {s: {"name": p.get("name", ""), "avg_price": p.get("avg_price", 0),
                                "pnl": p.get("pnl_rate", 0), "cur": p.get("cur_price", 0)}
                            for s, p in positions.items()}
    save_state(st)
    src = "live" if live else "fallback"
    log(f"스캔: 보유 {len(positions)} · 손실인시던트 {losers} · HIGH버그 {bugs} (src={src}, fix누적 {st.get('fix_count',0)})")
    return losers, bugs


# ── 메인 루프 ─────────────────────────────────────────────────────────
def in_market_hours():
    t = now_kst().time()
    wd = now_kst().weekday()
    return wd < 5 and dtime(9, 0) <= t <= dtime(15, 35)


def main():
    st = load_state()
    log(f"loss_watch_agent 시작 — REPO={REPO} DATA={DATA}")
    log(f"설정: interval={INTERVAL}s unreal={UNREAL_PCT}% stop_margin={STOP_MARGIN}pp "
        f"telegram={DO_TELEGRAM} fix_agent={DO_FIX_AGENT}(max {FIX_MAX}) dryrun={DRYRUN} "
        f"once={ONCE} market_hours={MARKET_HOURS}")
    if not (TG_TOKEN and TG_CHAT):
        log("[WARN] 텔레그램 토큰/챗ID 미확보 — 알림은 로그로만 남습니다")

    if ONCE:
        scan_once(st)
        return 0

    banner = (f"🟢 <b>손실감시 에이전트 가동</b> ({now_kst():%F %H:%M})\n"
              f"미실현 ≤ {UNREAL_PCT}% 또는 실현손실 시 상세분석 · HIGH버그(청산실패/차단/ETF/수량)는 "
              f"격리 브랜치에 패치 초안+알림(자율도=제안). 한도 {FIX_MAX}/일.")
    tg_send(banner)

    waited_open = False
    while True:
        try:
            if MARKET_HOURS and not in_market_hours():
                t = now_kst()
                if t.weekday() < 5 and t.time() < dtime(9, 0):
                    if not waited_open:
                        log("장 시작(09:00) 대기 중…")
                        waited_open = True
                    time.sleep(min(INTERVAL, 60))
                    continue
                # 장 마감 후: 마지막 1회 스캔(실현손실 확정) 후 종료
                log("장 마감(15:35↑) — 마감 스캔 후 종료")
                losers, bugs = scan_once(st)
                tg_send(f"🔵 <b>손실감시 종료</b> ({now_kst():%H:%M}) — 당일 손실인시던트 누적 기록 완료. "
                        f"픽스 디스패치 {st.get('fix_count',0)}건. 브랜치는 검토 후 머지/삭제하세요.")
                return 0
            scan_once(st)
        except KeyboardInterrupt:
            log("중단(KeyboardInterrupt)")
            return 0
        except Exception as e:
            log(f"[LOOP-ERR] {type(e).__name__}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
