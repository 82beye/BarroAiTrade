from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scripts import ops_subagent_monitor as monitor


def test_load_env_local_parses_simple_shell_assignments(tmp_path: Path):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join([
            "# comment",
            "TELEGRAM_CHAT_ID='12345'",
            'LIVE_TRADING_ENABLED="true"',
            "SUPERTREND_AUTO_ENABLED=1 # enabled",
            "BAD_LINE",
        ]),
        encoding="utf-8",
    )

    cfg = monitor.load_env_local(env_file)

    assert cfg["TELEGRAM_CHAT_ID"] == "12345"
    assert cfg["LIVE_TRADING_ENABLED"] == "true"
    assert cfg["SUPERTREND_AUTO_ENABLED"] == "1"
    assert "BAD_LINE" not in cfg


def test_parse_process_specs_supports_default_and_custom_values():
    specs = monitor.parse_process_specs(["backend=uvicorn backend.main,bot=scripts/run_telegram_bot.py"])

    assert specs == [
        monitor.ProcessSpec("backend", "uvicorn backend.main"),
        monitor.ProcessSpec("bot", "scripts/run_telegram_bot.py"),
    ]


def test_match_processes_returns_pid_and_memory():
    ps_output = "\n".join([
        "  100  20480 /usr/bin/python -m uvicorn backend.main:app",
        "  101  10240 /usr/bin/python scripts/run_telegram_bot.py",
        "  102   5120 /usr/bin/python other.py",
    ])
    specs = monitor.parse_process_specs(["backend=uvicorn backend.main", "telegram_bot=scripts/run_telegram_bot.py"])

    statuses = monitor.match_processes(ps_output, specs)

    assert statuses[0].running is True
    assert statuses[0].pids == (100,)
    assert statuses[0].rss_mb == 20
    assert statuses[1].running is True
    assert statuses[1].pids == (101,)


def test_classify_snapshot_critical_when_backend_down():
    snapshot = {
        "system": {"state": "idle"},
        "risk": {"status": "ok"},
        "logs": {"logs": []},
        "flags": {},
        "processes": [
            monitor.ProcessStatus("backend", "uvicorn backend.main", False),
        ],
    }

    level, problems = monitor.classify_snapshot(snapshot)

    assert level == "CRITICAL"
    assert "backend process down" in problems


def test_notification_window_open_only_in_regular_session():
    regular = datetime(2026, 6, 30, 10, 0, tzinfo=monitor.KST)
    closing_auction = datetime(2026, 6, 30, 15, 25, tzinfo=monitor.KST)
    after_hours = datetime(2026, 6, 30, 16, 0, tzinfo=monitor.KST)
    weekend = datetime(2026, 7, 4, 10, 0, tzinfo=monitor.KST)

    assert monitor.notification_window_open(regular) is True
    assert monitor.notification_window_open(closing_auction) is False
    assert monitor.notification_window_open(after_hours) is False
    assert monitor.notification_window_open(weekend) is False
    assert monitor.notification_window_open(after_hours, all_hours=True) is True


def test_parse_notify_sessions_defaults_to_regular():
    assert monitor.parse_notify_sessions(None) == {"regular"}
    assert monitor.parse_notify_sessions("") == {"regular"}
    assert monitor.parse_notify_sessions("regular,krx_closing_auction") == {
        "regular",
        "krx_closing_auction",
    }


def test_format_snapshot_reuses_existing_status_fields():
    snapshot = {
        "ts": "2026-06-30T10:00:00+09:00",
        "api_base": "http://127.0.0.1:8000/api",
        "system": {"state": "running", "mode": "simulation", "position_count": 2, "total_pnl": 1234},
        "risk": {"status": "ok", "position_count": 2, "current_exposure_pct": 0.12, "daily_pnl_pct": 1.5},
        "logs": {"logs": [{"key": "morning", "label": "매수 로그", "healthy": True, "age_sec": 10, "last_line": "ok"}]},
        "flags": {
            "LIVE_TRADING_ENABLED": "true",
            "KIWOOM_BASE_URL": "https://mockapi.kiwoom.com",
            "SUPERTREND_AUTO_ENABLED": "1",
            "SUPERTREND_AUTO_DRYRUN": "0",
            "LIMIT_UP_CHASE_ENABLED": "0",
            "LIMIT_UP_CHASE_DRYRUN": "1",
            "BARRO_CB_AUTOEXEC": "1",
            "EOD_FORCE_CLOSE_DISABLED": "1",
        },
        "processes": [
            monitor.ProcessStatus("backend", "uvicorn backend.main", True, (100,), 20.0),
            monitor.ProcessStatus("telegram_bot", "scripts/run_telegram_bot.py", True, (101,), 10.0),
        ],
        "active_positions": {"count": 2, "strategies": {"swing_38": 1, "strategy_blank": 1}},
    }

    message = monitor.format_snapshot(snapshot)

    assert "state=running" in message
    assert "live=ON" in message
    assert "kiwoom=mock" in message
    assert "supertrend=ON" in message
    assert "strategies=strategy_blank:1, swing_38:1" in message


def test_format_snapshot_markdown_escapes_dynamic_underscores():
    snapshot = {
        "ts": "2026-06-30T10:00:00+09:00",
        "api_base": "http://127.0.0.1:8000/api",
        "system": {"state": "running", "mode": "simulation", "position_count": 1, "total_pnl": 0},
        "risk": {"status": "ok", "position_count": 1, "current_exposure_pct": 0, "daily_pnl_pct": 0},
        "logs": {"logs": []},
        "flags": {"SUPERTREND_AUTO_ENABLED": "1", "LIMIT_UP_CHASE_ENABLED": "1"},
        "processes": [
            monitor.ProcessStatus("telegram_bot", "scripts/run_telegram_bot.py", True, (101,), 10.0),
        ],
        "active_positions": {"count": 1, "strategies": {"strategy_blank": 1}},
    }

    message = monitor.format_snapshot_markdown(snapshot)

    assert "<b>" not in message
    assert "telegram\\_bot" in message
    assert "strategy\\_blank:1" in message
