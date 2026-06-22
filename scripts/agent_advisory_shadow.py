#!/usr/bin/env python3
"""에이전트 자문(advisory) shadow 분석기 — Phase 2 측정 (read-only, 라이브 무영향).

게이트를 켜기 전에 "advisory가 NO-GO로 게이트했을 매수"를 **진실원천 realized** 로
반사실(counterfactual) 비교한다. 데몬을 건드리지 않고, 이미 기록된 산출물만 읽는다.

입력(전부 기존 산출물):
  · logs/decisions/<date>.jsonl       — writer 가 남긴 verdict(GO/WAIT/NO-GO + ts)
  · data/order_audit.csv              — 데몬 실제 매수(side=buy, symbol, strategy_id, ts)
  · reports/strategy_audit_<date>.json — per_symbol.realized (= 비용차감 진실원천,
                                          scripts/_daily_strategy_audit.py --save 산출)

방법: 각 실제 매수에 대해, 그 시점(ts) 이전 TTL 이내의 **신선한 NO-GO**(--block-wait 시 WAIT 도)
verdict 가 있었으면 "advisory 가 게이트했을 매수"로 본다. 게이트되는 종목의 realized 를
제거한 net 과 실제 net 을 비교 → advisory 가 손실을 피했는지(개선) 승자를 죽였는지(손해).

한계(명시):
  · realized 는 **종목 단위 집계**(그날 해당 종목 전체 매수+매도). 종목의 한 매수라도
    게이트되면 그 종목 realized 전체를 제거하는 근사다.
  · 자본 재배분(게이트로 빈 슬롯이 다른 종목으로) 효과는 모델링하지 않는다.
  · 따라서 shadow 는 방향성 신호일 뿐, ≥1~2주 누적으로 보고 게이트 활성(HITL)을 판단한다.

사용:
  python scripts/agent_advisory_shadow.py --date 2026-06-15
  python scripts/agent_advisory_shadow.py --date 2026-06-15 --ttl 180 --block-wait --save
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _ROOT / "data"
_LOGS_DIR = _ROOT / "logs"
_REPORTS_DIR = _ROOT / "reports"

NOGO = "NO-GO"
WAIT = "WAIT"


def parse_iso(s) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def load_decisions(path: Path) -> list[dict]:
    """logs/decisions/<date>.jsonl → verdict 리스트(ts 파싱 포함). 부재/오류 → []."""
    out: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict) or not rec.get("symbol"):
            continue
        rec["_ts"] = parse_iso(rec.get("ts"))
        rec["action"] = str(rec.get("action", "")).strip().upper().replace("NOGO", "NO-GO")
        out.append(rec)
    return out


def load_buys(csv_path: Path, date: str) -> list[dict]:
    """order_audit.csv → 해당 date 의 매수 행. ts(UTC)·symbol·strategy_id."""
    out: list[dict] = []
    try:
        f = Path(csv_path).open("r", encoding="utf-8")
    except OSError:
        return out
    with f:
        for row in csv.DictReader(f):
            if (row.get("side") or "").strip().lower() != "buy":
                continue
            ts = parse_iso(row.get("ts"))
            if ts is None:
                continue
            # 장중(00:00~06:30 UTC) = KST 동일 일자. ts 의 UTC date 로 필터.
            if ts.strftime("%Y-%m-%d") != date:
                continue
            out.append({"symbol": str(row.get("symbol", "")).strip(),
                        "ts": ts, "strategy": (row.get("strategy_id") or "").strip(),
                        "action": (row.get("action") or "").strip()})
    return out


def load_realized(json_path: Path) -> dict:
    """strategy_audit_<date>.json → {symbol: {realized, strategy}}. 부재/오류 → {}."""
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    ps = data.get("per_symbol", {})
    if not isinstance(ps, dict):
        return {}
    return {str(s): {"realized": float(d.get("realized", 0.0) or 0.0),
                     "strategy": d.get("strategy", "")}
            for s, d in ps.items() if isinstance(d, dict)}


def gating_verdict(symbol: str, buy_ts: datetime, decisions: list[dict],
                   ttl_sec: int, block_wait: bool) -> dict | None:
    """매수 시점에 advisory 가 차단했을 verdict(가장 최근 신선 NO-GO/WAIT). 없으면 None."""
    block_actions = {NOGO, WAIT} if block_wait else {NOGO}
    best = None
    for v in decisions:
        if v.get("symbol") != symbol or v["action"] not in block_actions:
            continue
        vts = v.get("_ts")
        if vts is None:
            continue
        age = (buy_ts - vts).total_seconds()
        if 0 <= age <= ttl_sec:                       # 매수 이전 + TTL 이내(신선)
            if best is None or vts > best["_ts"]:     # 가장 최근 verdict 우선
                best = v
    return best


def analyze(buys: list[dict], decisions: list[dict], realized: dict,
            ttl_sec: int, block_wait: bool) -> dict:
    """반사실 분석. 게이트되는 종목 집합·realized 효과 산출."""
    blocked: dict[str, dict] = {}     # symbol → {verdict, realized, strategy}
    bought_symbols = set()
    for b in buys:
        sym = b["symbol"]
        bought_symbols.add(sym)
        v = gating_verdict(sym, b["ts"], decisions, ttl_sec, block_wait)
        if v is not None and sym not in blocked:
            blocked[sym] = {
                "action": v["action"], "reason": v.get("reason", ""),
                "confidence": v.get("confidence"),
                "realized": realized.get(sym, {}).get("realized", 0.0),
                "strategy": realized.get(sym, {}).get("strategy", b.get("strategy", "")),
            }
    actual_total = sum(d.get("realized", 0.0) for d in realized.values())
    blocked_realized = sum(d["realized"] for d in blocked.values())
    counterfactual_total = actual_total - blocked_realized
    avoided_loss = sum(d["realized"] for d in blocked.values() if d["realized"] < 0)
    killed_win = sum(d["realized"] for d in blocked.values() if d["realized"] > 0)
    return {
        "ttl_sec": ttl_sec, "block_wait": block_wait,
        "n_buys": len(buys), "n_bought_symbols": len(bought_symbols),
        "n_blocked_symbols": len(blocked),
        "actual_total_realized": round(actual_total, 0),
        "blocked_realized": round(blocked_realized, 0),
        "counterfactual_total_realized": round(counterfactual_total, 0),
        "improvement": round(-blocked_realized, 0),     # 게이트로 인한 net 변화(+면 개선)
        "avoided_loss": round(avoided_loss, 0),         # 차단된 손실종목 realized 합(음수)
        "killed_win": round(killed_win, 0),             # 차단된 승자종목 realized 합(양수)
        "blocked_detail": [
            {"symbol": s, **d, "realized": round(d["realized"], 0)}
            for s, d in sorted(blocked.items(), key=lambda kv: kv[1]["realized"])
        ],
    }


def render_md(summary: dict, date: str) -> str:
    s = summary
    verdict_dir = "개선(손실 회피 우위)" if s["improvement"] > 0 else (
        "악화(승자 제거 우위)" if s["improvement"] < 0 else "중립")
    lines = [
        f"# 에이전트 자문 shadow 분석 — {date}",
        "",
        f"> 진실원천: `reports/strategy_audit_{date}.json` (per_symbol.realized, 비용차감)",
        f"> 파라미터: TTL={s['ttl_sec']}s · block_wait={s['block_wait']} · **게이트 미적용(측정용)**",
        "",
        "## 요약",
        "",
        "| 항목 | 값 |",
        "|---|--:|",
        f"| 실제 매수(행/종목) | {s['n_buys']} / {s['n_bought_symbols']} |",
        f"| advisory 게이트될 종목 | {s['n_blocked_symbols']} |",
        f"| 실제 net(원) | {s['actual_total_realized']:+,.0f} |",
        f"| 반사실 net(게이트 시) | {s['counterfactual_total_realized']:+,.0f} |",
        f"| **게이트 효과(개선)** | **{s['improvement']:+,.0f}** |",
        f"| └ 차단된 손실종목 합 | {s['avoided_loss']:+,.0f} |",
        f"| └ 차단된 승자종목 합 | {s['killed_win']:+,.0f} |",
        "",
        f"**판정**: {verdict_dir} (improvement {s['improvement']:+,.0f}원)",
        "",
        "## 게이트될 종목 상세 (realized 오름차순)",
        "",
        "| 종목 | verdict | 전략 | realized(원) | 사유 |",
        "|---|---|---|--:|---|",
    ]
    for d in s["blocked_detail"]:
        lines.append(f"| {d['symbol']} | {d['action']} | {d.get('strategy','')} | "
                     f"{d['realized']:+,.0f} | {str(d.get('reason',''))[:40]} |")
    if not s["blocked_detail"]:
        lines.append("| (없음) | | | | |")
    lines += [
        "",
        "## 한계",
        "- realized 는 종목 단위 집계(매수 1건이라도 게이트되면 종목 realized 전체 제거하는 근사).",
        "- 자본 재배분 효과 미반영. shadow 는 방향성 신호 — ≥1~2주 누적으로 게이트 활성(HITL) 판단.",
        "- verdict 는 writer backend(mock/claude-cli)에 의존. claude-cli 실판단 누적이 가장 의미 있음.",
    ]
    return "\n".join(lines) + "\n"


def run(date: str, *, ttl_sec: int, block_wait: bool,
        data_dir: Path = _DATA_DIR, logs_dir: Path = _LOGS_DIR,
        reports_dir: Path = _REPORTS_DIR, audit_path: Path | None = None) -> dict:
    decisions = load_decisions(logs_dir / "decisions" / f"{date}.jsonl")
    buys = load_buys(data_dir / "order_audit.csv", date)
    realized = load_realized(audit_path or (reports_dir / f"strategy_audit_{date}.json"))
    return analyze(buys, decisions, realized, ttl_sec, block_wait)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="advisory shadow 분석 (read-only)")
    ap.add_argument("--date", required=True, help="대상 영업일 YYYY-MM-DD")
    ap.add_argument("--ttl", type=int, default=180, help="verdict 신선도(초, 데몬과 일치)")
    ap.add_argument("--block-wait", action="store_true", help="WAIT 도 차단으로 간주")
    ap.add_argument("--audit", default=None, help="strategy_audit json 경로 override")
    ap.add_argument("--save", action="store_true",
                    help="reports/advisory_shadow_<date>.md 저장")
    args = ap.parse_args(argv)

    summary = run(args.date, ttl_sec=args.ttl, block_wait=args.block_wait,
                  audit_path=Path(args.audit) if args.audit else None)
    md = render_md(summary, args.date)
    print(md)
    if args.save:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = _REPORTS_DIR / f"advisory_shadow_{args.date}.md"
        out.write_text(md, encoding="utf-8")
        (_REPORTS_DIR / f"advisory_shadow_{args.date}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
