#!/usr/bin/env python3
"""에이전트 자문(advisory) writer — 생산자 측 (Phase 1~2).

`data/refined_signals.json`(데몬이 매 사이클 기록한 탐지 신호)을 폴링해, 각 신호에
quick-decider verdict(GO/WAIT/NO-GO)를 산출하고 `data/advisory.json` + `logs/decisions/<date>.jsonl`
에 기록한다.

설계 원칙(불변):
  · 데몬과 **분리된 프로세스** — Hermes/cron/launchd 가 스케줄. 데몬은 advisory.json 을 읽기만.
  · **LLM은 여기서만 호출** — 라이브 데몬 주문 경로엔 LLM 없음. 출력 부재/오류 시 데몬 fail-open.
  · advisory.json 의 소비자 계약 = backend/core/risk/agent_advisory.load_advisory.
  · git pull → 바로 사용: 런타임 디렉터리 자동 생성, 비밀값은 env(.env.example 참조).

백엔드(--backend):
  · mock       : 결정적 룰 기반 verdict (토큰 0). 스모크/테스트/Phase0 검증용.
  · claude-cli : `claude -p` 헤드리스 호출 (barrotrade-quick-decider 계약). 실 LLM 판단.

사용:
  python scripts/agent_advisory_writer.py --once --backend mock
  python scripts/agent_advisory_writer.py --interval 30 --backend claude-cli --telegram
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.risk.agent_advisory import GO, NOGO, WAIT  # noqa: E402
from backend.core.risk.theme_map import (  # noqa: E402
    hot_themes, load_theme_map, theme_exposure,
)

_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _ROOT / "data"
_LOGS_DIR = _ROOT / "logs"

_ACTIONS = {GO, WAIT, NOGO}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_refined_signals(path: Path) -> list[dict]:
    """refined_signals.json → signals 리스트. 부재/오류 시 [](fail-safe)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [s for s in (data.get("signals") or []) if isinstance(s, dict) and s.get("symbol")]


# ── verdict 백엔드 ────────────────────────────────────────────────────────────

def mock_verdict(sig: dict) -> dict:
    """결정적 mock — quick-decider 의도 모사(과열 추격 차단). 토큰 0, 테스트용.

    flu_rate(등락률) 과열·고점추격이면 NO-GO, 점수 낮으면 WAIT, 그 외 GO.
    """
    flu = float(sig.get("flu_rate", 0.0) or 0.0)
    score = float(sig.get("score", 0.0) or 0.0)
    if flu >= 25.0:
        return {"action": NOGO, "confidence": 0.8, "reason": f"등락률 {flu:.1f}% 과열·고점추격 위험"}
    if score < 4.0:
        return {"action": WAIT, "confidence": 0.5, "reason": f"점수 {score:.1f} 낮음 — 추가확인 권장"}
    return {"action": GO, "confidence": 0.7, "reason": f"점수 {score:.1f}·등락률 {flu:.1f}% 양호"}


_QUICK_DECIDER_PROMPT = """\
너는 한국주식 단타 트레이딩의 장중 의사결정 보조(quick-decider)다. 아래 매수 후보 신호에 대해
10초 내 GO / WAIT / NO-GO 를 결정한다. 고점추격·과열(등락률 과대)·점수 미달은 보수적으로 본다.
출력은 JSON 한 줄만: {{"action":"GO|WAIT|NO-GO","confidence":0~1,"reason":"한국어 한 문장"}}

신호: 종목={symbol}({name}) 전략={strategy} 점수={score} 등락률={flu_rate}% 현재가={cur_price}
"""


def _claude_bin() -> str:
    """claude CLI 실행 경로. headless(launchd/cron)에서 PATH 가 cmux 래퍼만
    가리키면 래퍼가 실 바이너리를 PATH 에서 못 찾아 exit 127 → 합성 실패.
    따라서 CLAUDE_CLI_BIN(절대경로) env 를 우선 사용하고, 없으면 PATH 의 claude."""
    env_bin = (os.environ.get("CLAUDE_CLI_BIN") or "").strip()
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    return shutil.which("claude") or "claude"


def claude_cli_verdict(sig: dict, *, timeout: float = 25.0) -> dict | None:
    """`claude -p` 헤드리스 호출 → verdict. 실패/타임아웃 → None(해당 종목 fail-open)."""
    prompt = _QUICK_DECIDER_PROMPT.format(
        symbol=sig.get("symbol", ""), name=sig.get("name", ""),
        strategy=sig.get("strategy", ""), score=sig.get("score", ""),
        flu_rate=sig.get("flu_rate", ""), cur_price=sig.get("cur_price", ""),
    )
    try:
        proc = subprocess.run(
            [_claude_bin(), "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_verdict_text(proc.stdout)


def _extract_json(text: str) -> dict | None:
    """claude -p --output-format json 출력 → 본문 첫 {...} JSON dict. 실패 None.

    (verdict·market 오버레이 공통 추출부.)
    """
    if not text:
        return None
    raw = text.strip()
    # claude -p --output-format json → {"result": "<본문>", ...}
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict) and "result" in outer:
            raw = str(outer["result"]).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _run_claude_cli(prompt: str, timeout: float, model: str | None = None) -> dict | None:
    """`claude -p` 헤드리스 호출 → JSON dict. 실패/타임아웃/비0 → None. model 지정 시 --model."""
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", str(model)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _extract_json(proc.stdout)


def _parse_verdict_text(text: str) -> dict | None:
    """LLM 출력 → verdict({action,confidence,reason}). 잘못된 action → None."""
    obj = _extract_json(text)
    if obj is None:
        return None
    action = str(obj.get("action", "")).strip().upper().replace("NOGO", "NO-GO")
    if action not in _ACTIONS:
        return None
    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {"action": action, "confidence": conf, "reason": str(obj.get("reason", ""))}


_BACKENDS = {"mock": mock_verdict, "claude-cli": claude_cli_verdict}


# ── advisory.json 병합/기록 ───────────────────────────────────────────────────

def merge_advisory(existing: dict | None, new: list[dict], now: datetime, keep_sec: int) -> dict:
    """기존 advisory + 신규 verdict 병합. symbol 당 최신 1건, keep_sec 초과 stale 제거.

    반환 스키마: {"updated_at": ISO, "verdicts": [{symbol,action,confidence,reason,ts,strategy}, ...]}
    (소비자 load_advisory 가 마지막 항목 우선이므로 ts 오름차순 정렬 출력.)
    """
    by_symbol: dict[str, dict] = {}
    for v in ((existing or {}).get("verdicts") or []):
        if isinstance(v, dict) and v.get("symbol"):
            by_symbol[str(v["symbol"])] = v
    for v in new:
        by_symbol[str(v["symbol"])] = v
    cutoff = now.timestamp() - keep_sec
    kept = []
    for v in by_symbol.values():
        ts = v.get("ts")
        try:
            ts_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
        except (ValueError, TypeError):
            ts_dt = None
        if ts_dt is not None and ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        if ts_dt is None or ts_dt.timestamp() >= cutoff:
            kept.append(v)
    kept.sort(key=lambda v: str(v.get("ts", "")))
    return {"updated_at": _iso(now), "verdicts": kept}


def write_json_atomic(path: Path, data: dict) -> None:
    """원자적 쓰기(temp → rename) — 데몬이 중간 상태를 읽지 않도록."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_decision_log(logs_dir: Path, record: dict, now: datetime) -> None:
    """logs/decisions/<date>.jsonl append (감사). 디렉터리 자동 생성."""
    d = logs_dir / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with (d / f"{now.astimezone().strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


_REGIME_RISK_ON = {"bull": True, "sideways": True, "bearish": False}

_MARKET_LLM_PROMPT = """\
너는 한국주식 장중 시장국면 판단 보조다. 아래 결정적 신호를 종합해 현재 시장이 위험선호(risk-on)인지
위험회피(risk-off)인지, 활성 단타 전략별 매수 허용 여부를 판단한다. 결정적 regime 을 존중하되,
거래대금 집중(특정 테마 과열 쏠림)·보유 테마 편중을 고려해 보수/공격을 조정한다.
출력은 JSON 한 줄만:
{{"risk_on":true|false,"confidence":0~1,"strategy_gates":{{"f_zone":true,"sf_zone":true,"gold_zone":true,"swing_38":true}},"reason":"한국어 한 문장"}}
strategy_gates 는 각 전략 매수 허용(true)/차단(false).

결정적 신호: regime={regime}
오늘 거래대금 집중 테마(상위): {hot}
보유 테마 노출: {exposure}
"""


def market_context_llm_overlay(base: dict, *, hot, exposure, regime,
                               timeout: float = 30.0, llm_fn=None) -> dict:
    """결정적 market_context base 에 LLM 판단(risk_on/confidence/strategy_gates/reason) 오버레이.

    실패/응답불가 → base 그대로(fail-open). llm_fn 주입 시 테스트(실 claude 호출 회피).
    결정적 regime/ts 는 보존 — LLM 은 soft 신호(risk_on/게이트/사유)만 덮어쓴다.
    """
    hot_str = ", ".join(f"{h.get('theme')}({h.get('turnover_pct', 0):.0%})"
                        for h in (hot or [])[:5]) or "(없음)"
    exp_str = ", ".join(f"{t}:{p:.0%}" for t, p in
                        sorted((exposure or {}).items(), key=lambda kv: -kv[1])[:5]) or "(없음)"
    prompt = _MARKET_LLM_PROMPT.format(regime=regime, hot=hot_str, exposure=exp_str)
    fn = llm_fn or (lambda p: _run_claude_cli(p, timeout))
    obj = fn(prompt)
    if not isinstance(obj, dict):
        return base                                   # fail-open
    out = dict(base)
    if obj.get("risk_on") is not None:
        out["risk_on"] = bool(obj["risk_on"])
    try:
        out["confidence"] = float(obj.get("confidence", base.get("confidence", 0.5)))
    except (TypeError, ValueError):
        pass
    gates = obj.get("strategy_gates")
    if isinstance(gates, dict):
        out["strategy_gates"] = {str(k): bool(v) for k, v in gates.items()}
    if obj.get("reason"):
        out["reason"] = str(obj["reason"])
    out["source"] = "snapshot+llm"
    return out


def produce_market_sections(snapshot: dict, theme_map: dict, now: datetime, *,
                            llm: bool = False, llm_fn=None, llm_timeout: float = 30.0) -> dict:
    """결정적 시장-맥락 섹션 생산 (market_snapshot.json + theme_map → advisory 섹션).

    결정적 1차 신호(국면 risk_on·거래대금 집중 테마·포트폴리오 노출/집중). `llm=True` 면
    market_context 에 LLM 오버레이(opt-in) — 실패 시 결정적 base 로 fail-open.
    """
    if not isinstance(snapshot, dict):
        return {}
    ts = _iso(now)
    regime = str(snapshot.get("regime", "unknown")).lower()
    leaders = snapshot.get("leaders") or []
    positions = snapshot.get("positions") or []

    exposure = theme_exposure(positions, theme_map)
    concentration = max(exposure.values(), default=0.0)
    hot = hot_themes(leaders, theme_map, top=10)
    market_ctx = {
        "regime": regime,
        "risk_on": _REGIME_RISK_ON.get(regime),
        "confidence": 0.5,                           # 결정적 base
        "strategy_gates": {},
        "reason": f"regime={regime}(결정적)",
        "ts": ts, "source": "snapshot",
    }
    if llm:
        market_ctx = market_context_llm_overlay(
            market_ctx, hot=hot, exposure=exposure, regime=regime,
            timeout=llm_timeout, llm_fn=llm_fn)
    return {
        "market_context": market_ctx,
        "sector_themes": {"hot": hot, "ts": ts, "source": "snapshot"},
        "portfolio_signals": {
            "theme_exposure": exposure,
            "concentration_pct": round(concentration, 4),
            "leverage_warn": False,                  # leverage 판정은 후속(balance 필요)
            "ts": ts, "source": "snapshot",
        },
    }


def run_once(*, backend: str, data_dir: Path, logs_dir: Path, ttl_sec: int,
             keep_sec: int, top: int, now: datetime | None = None,
             verdict_fn=None, market_llm: bool = False, market_llm_fn=None) -> list[dict]:
    """1회: refined_signals 읽기 → verdict 산출 → advisory.json/decisions 기록. 신규 verdict 반환.

    market_llm=True 면 market_context 에 LLM 오버레이(opt-in, 실패 시 결정적 fail-open).
    """
    now = now or _now()
    fn = verdict_fn or _BACKENDS[backend]
    signals = read_refined_signals(data_dir / "refined_signals.json")
    if top > 0:
        signals = signals[:top]
    new_verdicts: list[dict] = []
    for sig in signals:
        v = fn(sig)
        if not v:                       # 백엔드 실패 → 해당 종목 verdict 없음(데몬 fail-open)
            continue
        rec = {
            "symbol": sig["symbol"],
            "action": v["action"],
            "confidence": round(float(v.get("confidence", 0.0)), 3),
            "reason": v.get("reason", ""),
            "ts": _iso(now),
            "strategy": sig.get("strategy"),
        }
        new_verdicts.append(rec)
        append_decision_log(logs_dir, {**rec, "backend": backend,
                                       "score": sig.get("score"), "flu_rate": sig.get("flu_rate")}, now)
    adv_path = data_dir / "advisory.json"
    try:
        existing = json.loads(adv_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        existing = None
    merged = merge_advisory(existing, new_verdicts, now, keep_sec)
    # 시장-맥락 섹션 carry-forward(없으면 기존 유지) + market_snapshot 있으면 결정적 갱신.
    for _k in ("market_context", "sector_themes", "portfolio_signals"):
        if isinstance(existing, dict) and _k in existing:
            merged[_k] = existing[_k]
    try:
        snap = json.loads((data_dir / "market_snapshot.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        snap = None
    if snap:
        merged.update(produce_market_sections(
            snap, load_theme_map(data_dir / "theme_map.json"), now,
            llm=market_llm, llm_fn=market_llm_fn))
    write_json_atomic(adv_path, merged)
    return new_verdicts


def _maybe_telegram(verdicts: list[dict]) -> None:
    """verdict 를 텔레그램으로 실시간 표시(Phase 1). 실패는 무시(표시 채널)."""
    if not verdicts:
        return
    try:
        import asyncio

        from backend.core.notify.telegram import TelegramNotifier
        notifier = TelegramNotifier.from_env()
        if notifier is None:
            return
        lines = [f"[ADVISORY] {v['symbol']} {v['action']} ({v['confidence']:.0%}) — {v['reason']}"
                 for v in verdicts]
        asyncio.run(notifier.send("\n".join(lines)))
    except Exception:
        pass


_last_market_msg: str | None = None
_last_market_ts: float = 0.0  # [6/25] 시장국면 텔레그램 throttle(monotonic)


def build_market_message(data_dir: Path) -> str:
    """advisory.json 의 market-context 섹션 → 텔레그램 메시지(빈 섹션 생략). 없으면 ''."""
    from backend.core.notify.telegram import (
        format_macro_alert, format_portfolio_alert, format_sector_alert,
    )
    try:
        data = json.loads((data_dir / "advisory.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
    parts = [m for m in (format_macro_alert(data.get("market_context")),
                         format_sector_alert(data.get("sector_themes")),
                         format_portfolio_alert(data.get("portfolio_signals"))) if m]
    return "\n\n".join(parts)


def _maybe_telegram_market(data_dir: Path) -> None:
    """market-context 섹션을 텔레그램 표시. 변경 없으면 재전송 안 함(스팸 방지). 실패 무시."""
    global _last_market_msg, _last_market_ts
    import time as _t
    msg = build_market_message(data_dir)
    if not msg or msg == _last_market_msg:
        return
    # [6/25] 시장국면 텔레그램 최소 간격(default 15분) — 1분 스팸 방지. 0=무제한.
    _iv = float(os.environ.get('BARRO_MARKET_TG_INTERVAL_MIN', '15') or 15) * 60
    if _iv > 0 and _last_market_ts and (_t.monotonic() - _last_market_ts) < _iv:
        return
    try:
        import asyncio

        from backend.core.notify.telegram import TelegramNotifier
        notifier = TelegramNotifier.from_env()
        if notifier is None:
            return
        asyncio.run(notifier.send(msg))
        _last_market_msg = msg
        _last_market_ts = _t.monotonic()
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 자문 writer (advisory.json 생산자)")
    ap.add_argument("--backend", choices=list(_BACKENDS), default="claude-cli")
    ap.add_argument("--interval", type=float, default=0.0, help="루프 주기(초). 0이면 1회.")
    ap.add_argument("--once", action="store_true", help="1회만 실행(--interval 0 동치)")
    ap.add_argument("--ttl", type=int, default=180, help="verdict 신선도(데몬과 일치 권장)")
    ap.add_argument("--keep", type=int, default=900, help="advisory.json 보관 한도(초)")
    ap.add_argument("--top", type=int, default=0, help="상위 N 신호만(0=전체)")
    ap.add_argument("--telegram", action="store_true", help="verdict 텔레그램 실시간 표시")
    ap.add_argument("--market-llm", action="store_true",
                    help="market_context 에 LLM 오버레이(claude-cli). 미설정=결정적만. env BARRO_MARKET_LLM")
    ap.add_argument("--data-dir", default=str(_DATA_DIR))
    ap.add_argument("--logs-dir", default=str(_LOGS_DIR))
    args = ap.parse_args(argv)

    data_dir, logs_dir = Path(args.data_dir), Path(args.logs_dir)
    one_shot = args.once or args.interval <= 0
    market_llm = args.market_llm or os.environ.get("BARRO_MARKET_LLM", "").strip().lower() in {"1", "true", "yes", "on"}

    def _tick():
        verdicts = run_once(backend=args.backend, data_dir=data_dir, logs_dir=logs_dir,
                            ttl_sec=args.ttl, keep_sec=args.keep, top=args.top,
                            market_llm=market_llm)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] advisory writer: {len(verdicts)}건 verdict (backend={args.backend})")
        for v in verdicts:
            print(f"   {v['symbol']} {v['action']} ({v['confidence']:.0%}) {v['reason']}")
        if args.telegram:
            _maybe_telegram(verdicts)
            _maybe_telegram_market(data_dir)   # 시장국면·핫테마·포트폴리오 표시
        # [2026-06-23] 에이전트 협업 방(@barroAiTrade_agents_bot) 공유 — default-OFF·fail-open.
        #   BARRO_AGENT_ROOM_ENABLED=1 일 때만 게시(post 내부 게이트). 실패 무시(거래 무영향).
        try:
            from backend.core.agents import room_bus
            if verdicts:
                room_bus.post("advisory-writer", "finding", "verdict",
                              {"text": f"{len(verdicts)}건 verdict: " + ", ".join(
                                  f"{v['symbol']} {v['action']}({v['confidence']:.0%})"
                                  for v in verdicts[:5])})
        except Exception:  # noqa: BLE001 — fail-open
            pass

    if one_shot:
        _tick()
        return 0
    print(f"advisory writer 루프 시작 (interval={args.interval}s, backend={args.backend})")
    while True:
        try:
            _tick()
        except Exception as e:   # 루프 회복력 — 1회 실패가 프로세스를 죽이지 않게
            print(f"[writer] tick 오류(무시): {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
