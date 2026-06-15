#!/usr/bin/env python3
"""verify_eod_data.py — EOD 데이터 무결성 자가검증 (BAR-OPS).

배경: 2026-06-09~06-15 동안 (1) BAR-OPS-39 코드가 운영에 미배포된 채 라이브가 돌고,
  (2) 이브닝 파이프라인(_eod_fill_backfill·_eod_buy_snapshot·_save_balance_snapshot)이
  침묵해 6/15엔 fill_audit(브로커 체결)·EOD balance·buy_audit 가 통째로 누락됐다.
  매수/매도 주문 자체는 정상이었으나 '브로커 실측 손익 원천'이 사라져 6/15 매매복기가
  추정으로 떨어졌다. 이 스크립트는 EOD 직후 그 누락을 자동 감지해 알린다(조용한 회귀 차단).

검증(당일 거래가 있었던 거래일 한정):
  1) fill_audit.csv  — 매도 발생 시 ka10073 브로커 체결행이 있어야 함
  2) balance_history — 장 마감 후(>=14시 KST) EOD 정산 엔트리가 있어야 함(아침 스냅샷만이면 NG)
  3) buy_audit.csv   — EOD 보유 종목이 있으면 매수평단 스냅샷(BAR-OPS-39 P1)이 있어야 함

사용:
  ./.venv/bin/python scripts/verify_eod_data.py            # 오늘(KST)
  ./.venv/bin/python scripts/verify_eod_data.py 2026-06-15 # 특정일(과거일은 _active_positions_history 사용)
  bash scripts/verify_eod_data.sh [날짜]                    # venv 자동선택 래퍼

종료코드 = NG 건수 (cron/알림 연동용). 비거래일(주문 0건)은 자동 스킵.
"""
import csv, json, os, sys, glob
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BARRO_DATA_DIR") or os.path.join(BASE, "data")

DAY = (sys.argv[1].replace("-", "") if len(sys.argv) > 1
       else datetime.now(KST).strftime("%Y%m%d"))          # YYYYMMDD
if len(DAY) != 8 or not DAY.isdigit():
    print(f"[ERR] 날짜 인자 형식 오류: {sys.argv[1]!r} (YYYYMMDD 또는 YYYY-MM-DD)")
    sys.exit(9)
DAY_ISO = f"{DAY[:4]}-{DAY[4:6]}-{DAY[6:8]}"               # YYYY-MM-DD
TODAY = datetime.now(KST).strftime("%Y%m%d")

pass_n = fail_n = warn_n = 0
def ok(m):
    global pass_n; pass_n += 1; print(f"  [OK] {m}")
def ng(m):
    global fail_n; fail_n += 1; print(f"  [NG] {m}")
def wn(m):
    global warn_n; warn_n += 1; print(f"  [!!] {m}")
def info(m): print(f"  [..] {m}")

print(f"==== EOD 데이터 무결성 검증 — {DAY_ISO} ({datetime.now(KST):%F %T} 실행) ====")
print(f"DATA={DATA}")

# ── 0) order_audit: 당일 거래 여부 ──
oa_path = os.path.join(DATA, "order_audit.csv")
orders = []
if os.path.exists(oa_path):
    with open(oa_path, newline="", encoding="utf-8") as f:
        orders = [r for r in csv.DictReader(f) if r.get("ts", "").startswith(DAY_ISO)]
ordered = [r for r in orders if r.get("action") == "ORDERED"]
sells = [r for r in ordered if r.get("side") == "sell"]
buys = [r for r in ordered if r.get("side") == "buy"]
failed = [r for r in orders if r.get("action") == "FAILED"]

if not orders:
    wd = datetime.strptime(DAY, "%Y%m%d").weekday()       # 0=월..6=일
    if wd >= 5:
        info(f"주말(요일={wd}) · order_audit 주문 0건 → 비거래일, 검증 스킵.")
    else:
        wn(f"평일인데 order_audit {DAY_ISO} 주문 0건 — 데몬 미실행/데이터 미반영(또는 공휴일) 점검.")
    print(f"\n==== 결과: PASS {pass_n} / FAIL {fail_n} / WARN {warn_n} ====")
    sys.exit(fail_n)

info(f"order_audit {DAY_ISO}: ORDERED {len(ordered)} (매수 {len(buys)}·매도 {len(sells)}) · FAILED {len(failed)}")

# ── 1) fill_audit: 브로커 실측 체결 (ka10073, 매도 실현) ──
fa_path = os.path.join(DATA, "fill_audit.csv")
fa_rows = []
if os.path.exists(fa_path):
    with open(fa_path, newline="", encoding="utf-8") as f:
        fa_rows = [r for r in csv.DictReader(f) if r.get("date") == DAY]
if sells:
    if fa_rows:
        ok(f"fill_audit {DAY}: {len(fa_rows)}행 (매도 {len(sells)}건 대응) — 브로커 실측 손익 존재")
    else:
        ng(f"fill_audit {DAY}: 0행인데 매도 {len(sells)}건 발생 → 이브닝 파이프라인(_eod_fill_backfill/ka10073) "
           f"미실행. 매매복기 실현손익 원천 부재. D+2 내 ka10073 재수집 필요.")
else:
    info("fill_audit: 당일 매도 0건 → 체결 실현 없음(전량 이월 가능, 정상).")

# ── 2) EOD balance: 장 마감 후 정산 잔고 ──
bh_path = os.path.join(DATA, "balance_history.json")
bal = None
if os.path.exists(bh_path):
    try:
        bh = json.load(open(bh_path, encoding="utf-8"))
        cands = [r for r in bh if r.get("date") == DAY_ISO]
        bal = cands[-1] if cands else None
    except Exception as e:
        wn(f"balance_history 파싱 실패: {e}")
if bal is None:
    ng(f"balance_history {DAY_ISO}: 엔트리 없음 → EOD 정산 스냅샷 미기록(_save_balance_snapshot 미실행).")
else:
    ua = bal.get("updated_at", "") or ""
    hr = None
    try:
        hr = datetime.fromisoformat(ua).astimezone(KST).hour
    except Exception:
        pass
    if hr is None:
        wn(f"balance_history {DAY_ISO}: updated_at 파싱불가({ua!r}) — 시각 수동확인 필요.")
    elif hr < 14:
        ng(f"balance_history {DAY_ISO}: updated_at={ua[:19]}(오전 {hr}시) — 아침 스냅샷만 존재, "
           f"EOD(장 마감 후) 정산 누락.")
    else:
        ok(f"balance_history {DAY_ISO}: EOD 정산 존재 (updated_at {hr}시, cash {bal.get('cash',0):,.0f}).")

# ── 3) buy_audit: EOD 보유 종목 매수평단 (BAR-OPS-39 P1) ──
def eod_holdings(day):
    """당일 EOD 보유: 오늘이면 active_positions.json, 과거면 _active_positions_history 마지막 스냅샷."""
    ap_path = os.path.join(DATA, "active_positions.json")
    if day == TODAY and os.path.exists(ap_path):
        try:
            ap = json.load(open(ap_path, encoding="utf-8"))
            return ap if isinstance(ap, dict) else {}
        except Exception:
            return {}
    hist = sorted(glob.glob(os.path.join(DATA, "_active_positions_history",
                                         f"active_positions_{day}T*.json")))
    if hist:
        try:
            ap = json.load(open(hist[-1], encoding="utf-8"))
            return ap if isinstance(ap, dict) else {}
        except Exception:
            return {}
    if os.path.exists(ap_path):                            # 폴백
        try:
            ap = json.load(open(ap_path, encoding="utf-8"))
            return ap if isinstance(ap, dict) else {}
        except Exception:
            return {}
    return {}

holds = eod_holdings(DAY)
ba_path = os.path.join(DATA, "buy_audit.csv")
ba_rows = []
if os.path.exists(ba_path):
    with open(ba_path, newline="", encoding="utf-8") as f:
        ba_rows = [r for r in csv.DictReader(f) if r.get("date") == DAY]
approx = "" if DAY == TODAY else " (과거일: 장중 마지막 스냅샷 기준 근사 — EOD 전 청산분이 보유로 잡힐 수 있음)"
if holds:
    if ba_rows:
        ok(f"buy_audit {DAY}: {len(ba_rows)}행 (EOD 보유 {len(holds)}종목 대응) — BAR-OPS-39 매수 스냅샷 존재")
    elif DAY == TODAY:
        ng(f"buy_audit {DAY}: 0행인데 EOD 보유 {len(holds)}종목({', '.join(list(holds)[:5])}) → "
           f"_eod_buy_snapshot 미실행(BAR-OPS-39 미배포 신호). 매수평단 독립 감사 소스 부재.")
    else:
        wn(f"buy_audit {DAY}: 0행, EOD 보유 추정 {len(holds)}종목({', '.join(list(holds)[:5])}){approx}. "
           f"당일(EOD 직후) 재검증 시 hard-NG. 매수평단 스냅샷 부재 가능.")
else:
    info(f"buy_audit: EOD 보유 0종목 → 매수 스냅샷 불필요(정상){approx}.")

# ── 4) 종합 진단 ──
if fail_n:
    print()
    info("진단: NG 1건 이상 → '이브닝 파이프라인 미실행'이 유력. 운영 머신에서 EOD 시퀀스 로그"
         "(_eod_fill_backfill·_eod_buy_snapshot·_save_balance_snapshot) 점검 후, ka10073/kt00018"
         " 재수집으로 당일 실측 복구(브로커 D+2 조회기간 내). 코드 미배포면 verify_deploy.sh 도 함께 확인.")

print(f"\n==== 결과: PASS {pass_n} / FAIL {fail_n} / WARN {warn_n} ====")
if fail_n == 0:
    print("[OK] EOD 데이터 무결성 정상 — 브로커 실측 손익 원천 확보.")
else:
    print(f"[NG] 데이터 누락 {fail_n}건 — 위 조치 후 재수집/재검증.")
sys.exit(fail_n)
