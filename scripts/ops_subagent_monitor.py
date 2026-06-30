#!/usr/bin/env python3
"""운영 머신 상태를 읽어 텔레그램으로 보고하는 read-only 서브에이전트.

주문/매매 실행 모듈은 호출하지 않는다. 기존 운영 상태 정보만 재사용한다.

사용:
  ./.venv/bin/python scripts/ops_subagent_monitor.py --once
  ./.venv/bin/python scripts/ops_subagent_monitor.py --interval 300
  ./.venv/bin/python scripts/ops_subagent_monitor.py --once --no-telegram
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

KST = timezone(timedelta(hours=9))
ENV_LOCAL = REPO / ".env.local"
DATA_DIR = Path(os.environ.get("BARRO_DATA_DIR", str(REPO / "data")))

TRUTHY = {"1", "true", "yes", "on"}
DEFAULT_API_BASE = "http://127.0.0.1:8000/api"
DEFAULT_NOTIFY_SESSIONS = "regular"

ENV_FLAGS = (
    "TRADING_MODE",
    "KIWOOM_BASE_URL",
    "LIVE_TRADING_ENABLED",
    "SUPERTREND_AUTO_ENABLED",
    "SUPERTREND_AUTO_DRYRUN",
    "LIMIT_UP_CHASE_ENABLED",
    "LIMIT_UP_CHASE_DRYRUN",
    "BARRO_CB_AUTOEXEC",
    "EOD_FORCE_CLOSE_DISABLED",
)

DEFAULT_PROCESSES = (
    "backend=uvicorn backend.main",
    "telegram_bot=scripts/run_telegram_bot.py",
    "intraday_daemon=scripts/intraday_buy_daemon.py",
    "closing_bet=scripts/closing_bet_alert_daemon.py",
    "frontend=next dev",
    "ngrok=ngrok",
)


@dataclass(frozen=True)
class ProcessSpec:
    label: str
    pattern: str


@dataclass(frozen=True)
class ProcessStatus:
    label: str
    pattern: str
    running: bool
    pids: tuple[int, ...] = ()
    rss_mb: float = 0.0


def now_kst() -> datetime:
    return datetime.now(KST)


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY


def parse_notify_sessions(value: str | None) -> set[str]:
    raw = value if value is not None else DEFAULT_NOTIFY_SESSIONS
    sessions = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return sessions or {"regular"}


def current_market_session(now: datetime | None = None) -> str:
    """현재 KST 거래 세션명. 공통 MarketSessionService 를 우선 사용."""
    try:
        from backend.core.market_session.service import MarketSessionService

        session = MarketSessionService().get_session(now)
        return getattr(session, "value", str(session)).lower()
    except Exception:
        current = now or now_kst()
        if current.tzinfo is None:
            current = current.replace(tzinfo=KST)
        else:
            current = current.astimezone(KST)
        if current.weekday() >= 5:
            return "closed"
        t = current.time()
        if t >= datetime.strptime("09:00", "%H:%M").time() and t < datetime.strptime("15:20", "%H:%M").time():
            return "regular"
        return "closed"


def notification_window_open(
    now: datetime | None = None,
    *,
    allowed_sessions: set[str] | None = None,
    all_hours: bool = False,
) -> bool:
    if all_hours:
        return True
    return current_market_session(now) in (allowed_sessions or {"regular"})


def load_env_local(path: Path = ENV_LOCAL) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split("#", 1)[0].strip().strip('"').strip("'")
        cfg[key.strip()] = value
    return cfg


def apply_env_local(path: Path = ENV_LOCAL) -> dict[str, str]:
    cfg = load_env_local(path)
    for key, value in cfg.items():
        os.environ.setdefault(key, value)
    return cfg


def parse_process_specs(values: list[str] | None = None) -> list[ProcessSpec]:
    raw_values = values or list(DEFAULT_PROCESSES)
    specs: list[ProcessSpec] = []
    for value in raw_values:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                label, _, pattern = item.partition("=")
            else:
                label, pattern = item.replace(" ", "_"), item
            label = label.strip()
            pattern = pattern.strip()
            if label and pattern:
                specs.append(ProcessSpec(label=label, pattern=pattern))
    return specs


def _ps_output() -> str:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,rss=,command="],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return proc.stdout


def match_processes(ps_output: str, specs: list[ProcessSpec]) -> list[ProcessStatus]:
    rows: list[tuple[int, int, str]] = []
    for line in ps_output.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            rss_kb = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, rss_kb, parts[2]))

    statuses: list[ProcessStatus] = []
    for spec in specs:
        needle = spec.pattern.lower()
        matches = [(pid, rss) for pid, rss, cmd in rows if needle in cmd.lower()]
        pids = tuple(pid for pid, _rss in matches)
        rss_mb = sum(rss for _pid, rss in matches) / 1024 if matches else 0.0
        statuses.append(
            ProcessStatus(
                label=spec.label,
                pattern=spec.pattern,
                running=bool(matches),
                pids=pids,
                rss_mb=rss_mb,
            )
        )
    return statuses


def collect_process_status(specs: list[ProcessSpec]) -> list[ProcessStatus]:
    try:
        return match_processes(_ps_output(), specs)
    except Exception:
        return [
            ProcessStatus(label=s.label, pattern=s.pattern, running=False)
            for s in specs
        ]


def fetch_json(url: str, timeout: float = 5.0) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}


def load_active_position_summary(path: Path | None = None) -> dict[str, Any]:
    target = path or DATA_DIR / "active_positions.json"
    if not target.exists():
        return {"count": 0, "strategies": {}, "detail": "file_missing"}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        return {"count": 0, "strategies": {}, "detail": f"json_error:{type(exc).__name__}"}
    if not isinstance(raw, dict):
        return {"count": 0, "strategies": {}, "detail": "invalid_shape"}

    strategies: dict[str, int] = {}
    for pos in raw.values():
        strategy = ""
        if isinstance(pos, dict):
            strategy = str(pos.get("strategy") or "").strip()
        strategies[strategy or "strategy_blank"] = strategies.get(strategy or "strategy_blank", 0) + 1
    return {"count": len(raw), "strategies": strategies, "detail": "ok"}


def collect_snapshot(api_base: str, processes: list[ProcessSpec]) -> dict[str, Any]:
    base = api_base.rstrip("/")
    flags = {key: os.environ.get(key, "") for key in ENV_FLAGS}
    return {
        "ts": now_kst().isoformat(timespec="seconds"),
        "api_base": base,
        "market_session": current_market_session(),
        "system": fetch_json(f"{base}/status"),
        "risk": fetch_json(f"{base}/risk/status"),
        "logs": fetch_json(f"{base}/logs/status"),
        "processes": collect_process_status(processes),
        "flags": flags,
        "active_positions": load_active_position_summary(),
    }


def classify_snapshot(snapshot: dict[str, Any]) -> tuple[str, list[str]]:
    problems: list[str] = []
    level = "OK"

    system = snapshot.get("system") or {}
    if system.get("status") == "error" or system.get("detail"):
        level = "CRITICAL"
        problems.append("backend status API unreachable")

    process_map = {p.label: p for p in snapshot.get("processes", [])}
    backend = process_map.get("backend")
    if backend and not backend.running:
        level = "CRITICAL"
        problems.append("backend process down")

    flags = snapshot.get("flags") or {}
    bot_expected = (
        is_truthy(flags.get("SUPERTREND_AUTO_ENABLED"))
        or is_truthy(flags.get("LIMIT_UP_CHASE_ENABLED"))
    )
    bot = process_map.get("telegram_bot")
    if bot_expected and bot and not bot.running and level != "CRITICAL":
        level = "WARN"
        problems.append("telegram bot strategy loop down")

    risk = snapshot.get("risk") or {}
    if risk.get("status") == "error" and level != "CRITICAL":
        level = "WARN"
        problems.append("risk status fallback/error")

    logs = snapshot.get("logs") or {}
    stale = [
        item.get("key", item.get("file", "?"))
        for item in logs.get("logs", [])
        if not item.get("healthy", False)
    ] if isinstance(logs, dict) else []
    if stale and level == "OK":
        level = "WARN"
        problems.append("stale logs: " + ", ".join(map(str, stale[:5])))

    return level, problems


def _fmt_bool(value: str | None) -> str:
    return "ON" if is_truthy(value) else "OFF"


def _fmt_money(value: Any) -> str:
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "0"


def _h(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _md(value: Any) -> str:
    text = str(value)
    for ch in ("_", "*", "[", "]", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_snapshot(snapshot: dict[str, Any]) -> str:
    level, problems = classify_snapshot(snapshot)
    system = snapshot.get("system") or {}
    risk = snapshot.get("risk") or {}
    flags = snapshot.get("flags") or {}
    active = snapshot.get("active_positions") or {}

    lines = [
        f"<b>BarroAiTrade 운영 서브에이전트</b>",
        f"상태: <b>{_h(level)}</b> | session={_h(snapshot.get('market_session', '?'))} | {_h(snapshot.get('ts', ''))}",
    ]
    if problems:
        lines.append("이슈: " + _h("; ".join(problems)))

    lines.extend([
        "",
        "<b>시스템</b>",
        (
            f"API={_h(snapshot.get('api_base', ''))} "
            f"state={_h(system.get('state', system.get('status', 'unknown')))} "
            f"mode={_h(system.get('mode', flags.get('TRADING_MODE', '')))} "
            f"positions={_h(system.get('position_count', '?'))} "
            f"pnl={_h(_fmt_money(system.get('total_pnl', 0)))}"
        ),
        (
            f"risk_status={_h(risk.get('status', 'unknown'))} "
            f"risk_positions={_h(risk.get('position_count', '?'))} "
            f"exposure={float(risk.get('current_exposure_pct') or 0) * 100:.1f}% "
            f"daily_pnl={float(risk.get('daily_pnl_pct') or 0):+.2f}%"
        ),
        "",
        "<b>전략/운영 플래그</b>",
        (
            f"live={_fmt_bool(flags.get('LIVE_TRADING_ENABLED'))} "
            f"kiwoom={'mock' if 'mockapi' in (flags.get('KIWOOM_BASE_URL') or '') else 'real'} "
            f"supertrend={_fmt_bool(flags.get('SUPERTREND_AUTO_ENABLED'))}"
            f"(dry={_h(flags.get('SUPERTREND_AUTO_DRYRUN', ''))}) "
            f"limit_up={_fmt_bool(flags.get('LIMIT_UP_CHASE_ENABLED'))}"
            f"(dry={_h(flags.get('LIMIT_UP_CHASE_DRYRUN', ''))})"
        ),
        (
            f"closing_bet={_fmt_bool(flags.get('BARRO_CB_AUTOEXEC'))} "
            f"eod_force_close_disabled={_fmt_bool(flags.get('EOD_FORCE_CLOSE_DISABLED'))}"
        ),
        "",
        "<b>프로세스</b>",
    ])

    for proc in snapshot.get("processes", []):
        state = "UP" if proc.running else "DOWN"
        pid_text = ",".join(str(p) for p in proc.pids[:3]) if proc.pids else "-"
        more = "+" if len(proc.pids) > 3 else ""
        lines.append(
            f"{_h(proc.label)}: {state} pid={_h(pid_text + more)} rss={proc.rss_mb:.0f}MB"
        )

    strategy_counts = active.get("strategies") or {}
    strategy_text = ", ".join(
        f"{name}:{count}" for name, count in sorted(strategy_counts.items())
    ) or "-"
    lines.extend([
        "",
        "<b>Active Positions</b>",
        f"count={_h(active.get('count', 0))} strategies={_h(strategy_text)}",
    ])

    logs = snapshot.get("logs") or {}
    log_rows = logs.get("logs", []) if isinstance(logs, dict) else []
    if log_rows:
        lines.extend(["", "<b>로그 상태</b>"])
        for row in log_rows[:8]:
            state = "OK" if row.get("healthy") else "STALE"
            age = int(row.get("age_sec") or 0)
            last_line = str(row.get("last_line") or "")[:90]
            lines.append(
                f"{_h(row.get('label', row.get('key', '?')))}: {state} "
                f"age={age}s last={_h(last_line)}"
            )

    return "\n".join(lines)


def format_snapshot_markdown(snapshot: dict[str, Any]) -> str:
    """기존 run_telegram_bot.py 의 Markdown notifier 에 맞춘 상태 요약."""
    level, problems = classify_snapshot(snapshot)
    system = snapshot.get("system") or {}
    risk = snapshot.get("risk") or {}
    flags = snapshot.get("flags") or {}
    active = snapshot.get("active_positions") or {}

    lines = [
        "*BarroAiTrade 운영 서브에이전트*",
        f"상태: *{_md(level)}* | session={_md(snapshot.get('market_session', '?'))} | {_md(snapshot.get('ts', ''))}",
    ]
    if problems:
        lines.append("이슈: " + _md("; ".join(problems)))

    lines.extend([
        "",
        "*시스템*",
        (
            f"API={_md(snapshot.get('api_base', ''))} "
            f"state={_md(system.get('state', system.get('status', 'unknown')))} "
            f"mode={_md(system.get('mode', flags.get('TRADING_MODE', '')))} "
            f"positions={_md(system.get('position_count', '?'))} "
            f"pnl={_md(_fmt_money(system.get('total_pnl', 0)))}"
        ),
        (
            f"risk-status={_md(risk.get('status', 'unknown'))} "
            f"risk-positions={_md(risk.get('position_count', '?'))} "
            f"exposure={float(risk.get('current_exposure_pct') or 0) * 100:.1f}% "
            f"daily-pnl={float(risk.get('daily_pnl_pct') or 0):+.2f}%"
        ),
        "",
        "*전략/운영 플래그*",
        (
            f"live={_fmt_bool(flags.get('LIVE_TRADING_ENABLED'))} "
            f"kiwoom={'mock' if 'mockapi' in (flags.get('KIWOOM_BASE_URL') or '') else 'real'} "
            f"supertrend={_fmt_bool(flags.get('SUPERTREND_AUTO_ENABLED'))}"
            f"(dry={_md(flags.get('SUPERTREND_AUTO_DRYRUN', ''))}) "
            f"limit-up={_fmt_bool(flags.get('LIMIT_UP_CHASE_ENABLED'))}"
            f"(dry={_md(flags.get('LIMIT_UP_CHASE_DRYRUN', ''))})"
        ),
        (
            f"closing-bet={_fmt_bool(flags.get('BARRO_CB_AUTOEXEC'))} "
            f"eod-force-close-disabled={_fmt_bool(flags.get('EOD_FORCE_CLOSE_DISABLED'))}"
        ),
        "",
        "*프로세스*",
    ])

    for proc in snapshot.get("processes", []):
        state = "UP" if proc.running else "DOWN"
        pid_text = ",".join(str(p) for p in proc.pids[:3]) if proc.pids else "-"
        more = "+" if len(proc.pids) > 3 else ""
        lines.append(
            f"{_md(proc.label)}: {state} pid={_md(pid_text + more)} rss={proc.rss_mb:.0f}MB"
        )

    strategy_counts = active.get("strategies") or {}
    strategy_text = ", ".join(
        f"{name}:{count}" for name, count in sorted(strategy_counts.items())
    ) or "-"
    lines.extend([
        "",
        "*Active Positions*",
        f"count={_md(active.get('count', 0))} strategies={_md(strategy_text)}",
    ])

    logs = snapshot.get("logs") or {}
    log_rows = logs.get("logs", []) if isinstance(logs, dict) else []
    if log_rows:
        lines.extend(["", "*로그 상태*"])
        for row in log_rows[:8]:
            state = "OK" if row.get("healthy") else "STALE"
            age = int(row.get("age_sec") or 0)
            last_line = str(row.get("last_line") or "")[:90]
            lines.append(
                f"{_md(row.get('label', row.get('key', '?')))}: {state} "
                f"age={age}s last={_md(last_line)}"
            )

    return "\n".join(lines)


def status_signature(snapshot: dict[str, Any]) -> str:
    level, problems = classify_snapshot(snapshot)
    process_bits = [
        f"{p.label}:{int(p.running)}"
        for p in snapshot.get("processes", [])
    ]
    logs = snapshot.get("logs") or {}
    log_bits = [
        f"{row.get('key')}:{int(bool(row.get('healthy')))}"
        for row in logs.get("logs", [])
    ] if isinstance(logs, dict) else []
    return "|".join([level, ";".join(problems), ",".join(process_bits), ",".join(log_bits)])


async def send_telegram(message: str) -> None:
    from backend.core.notify.telegram import TelegramNotifier

    notifier = TelegramNotifier.from_env(parse_mode="HTML")
    await notifier.send_chunks(message)


async def run(args: argparse.Namespace) -> None:
    apply_env_local()
    specs = parse_process_specs(args.process or None)
    notify_sessions = parse_notify_sessions(args.notify_sessions)
    prev_signature = ""
    cycle = 0

    while True:
        snapshot = collect_snapshot(args.api_base, specs)
        message = format_snapshot(snapshot)
        signature = status_signature(snapshot)
        heartbeat = args.heartbeat_every > 0 and cycle % args.heartbeat_every == 0
        should_send = args.once or cycle == 0 or signature != prev_signature or heartbeat
        notify_open = notification_window_open(
            allowed_sessions=notify_sessions,
            all_hours=args.all_hours,
        )

        if args.no_telegram:
            print(message)
        elif should_send and notify_open:
            await send_telegram(message)

        if args.once:
            return
        if notify_open:
            prev_signature = signature
        cycle += 1
        await asyncio.sleep(args.interval)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="BarroAiTrade 운영 머신 상태 서브에이전트")
    ap.add_argument("--api-base", default=os.environ.get("OPS_MONITOR_API_BASE", DEFAULT_API_BASE))
    ap.add_argument("--interval", type=int, default=int(os.environ.get("OPS_MONITOR_INTERVAL", "300")))
    ap.add_argument("--heartbeat-every", type=int, default=int(os.environ.get("OPS_MONITOR_HEARTBEAT_EVERY", "6")),
                    help="N회마다 상태 변화가 없어도 heartbeat 전송. 0이면 변화 시에만 전송")
    ap.add_argument("--notify-sessions", default=os.environ.get("OPS_MONITOR_NOTIFY_SESSIONS", DEFAULT_NOTIFY_SESSIONS),
                    help="텔레그램 알림 허용 세션 콤마 목록. 기본 regular(정규장)")
    ap.add_argument("--all-hours", action="store_true",
                    help="정규장 게이트 해제. 수동 점검 때만 사용")
    ap.add_argument("--process", action="append",
                    help="감시 프로세스. label=pattern 형식, 여러 번 지정 가능")
    ap.add_argument("--once", action="store_true", help="1회 보고 후 종료")
    ap.add_argument("--no-telegram", action="store_true", help="텔레그램 전송 없이 stdout 출력")
    return ap


def main() -> None:
    args = build_parser().parse_args()
    if args.interval <= 0:
        raise SystemExit("--interval must be > 0")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
