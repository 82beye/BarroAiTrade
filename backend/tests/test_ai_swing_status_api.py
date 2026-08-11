"""GET /api/ai-swing/status 계약 테스트 — 읽기 전용 대시보드 라우트.

고정하는 계약:
  1. default-OFF: 플래그 미설정이면 status="disabled" 이고 파일을 읽지 않는다.
  2. 자격증명 미노출: `.env.local` 에 키/토큰이 있어도 응답 어디에도 나타나지 않는다.
  3. 게이트 판정이 scripts/intraday_buy_daemon.py 의 5중 default-OFF 와 일치한다.
  4. 설정 진실원천은 `.env.local` 파일이며, 프로세스 env 와 다르면 config_mismatch 로 보고한다.
  5. 데이터 부재는 날조 대신 no_data 강등이다(§8).
  6. 소스에 주문·체결 심볼이 없다(§2 S2).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import ai_swing as mod
from backend.main import app

ENDPOINT = "/api/ai-swing/status"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """프로세스 env 의 실 운영값이 테스트 판정에 새지 않게 격리한다."""
    for key in (
        "BARRO_DAEMON_STRATEGIES",
        "BARRO_AI_SWING_ENABLED",
        "BARRO_AI_SWING_ENTRY_ENABLED",
        "BARRO_AI_SWING_BUDGET_RATIO",
        "BARRO_AI_SWING_MAX_POSITIONS",
        "BARRO_AI_TRADE_DIR",
        "BARRO_DATA_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


ENV_FULL_ON = """\
# 주석 줄
BARRO_DAEMON_STRATEGIES=ai_swing   # [2026-08-12] ai_swing 단독. 되돌리기=f_zone,sf_zone
BARRO_AI_SWING_ENABLED=1   # 마스터
BARRO_AI_SWING_ENTRY_ENABLED=1
BARRO_AI_SWING_BUDGET_RATIO=0.10
BARRO_AI_SWING_MAX_POSITIONS=1
BARRO_AI_SWING_MAX_AGE_H=12
BARRO_AI_SWING_ALLOW_STALE=0
BARRO_AI_SWING_FALLBACK=
LIVE_TRADING_ENABLED=true
KIWOOM_BASE_URL=https://mockapi.kiwoom.com
KIWOOM_APP_KEY=SUPER_SECRET_APP_KEY_VALUE
KIWOOM_APP_SECRET=SUPER_SECRET_APP_SECRET_VALUE
TELEGRAM_BOT_TOKEN=0000000000:SECRET_TELEGRAM_TOKEN_FIXTURE
"""

SECRETS = (
    "SUPER_SECRET_APP_KEY_VALUE",
    "SUPER_SECRET_APP_SECRET_VALUE",
    "SECRET_TELEGRAM_TOKEN_FIXTURE",
    "KIWOOM_APP_KEY",
    "TELEGRAM_BOT_TOKEN",
)


def _write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env.local"
    path.write_text(body, encoding="utf-8")
    return path


def _point_at(monkeypatch: pytest.MonkeyPatch, env_path: Path, data_dir: Path) -> None:
    monkeypatch.setattr(mod, "_ENV_FILE", env_path)
    monkeypatch.setattr(mod, "_DATA_DIR", data_dir)


# ── 1. default-OFF ───────────────────────────────────────────────────────────
def test_disabled_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv(mod.ENV_DASHBOARD, raising=False)
    env_path = _write_env(tmp_path, ENV_FULL_ON)
    _point_at(monkeypatch, env_path, tmp_path / "data")

    body = client.get(ENDPOINT).json()

    assert body["status"] == "disabled"
    assert mod.ENV_DASHBOARD in body["reason"]
    # 비활성 시에는 어떤 데이터 블록도 만들지 않는다
    assert "gates" not in body and "universe" not in body


def test_disabled_does_not_read_env_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """플래그 OFF 면 `.env.local` 을 아예 열지 않는다(자격증명 접근 0)."""
    monkeypatch.delenv(mod.ENV_DASHBOARD, raising=False)
    _point_at(monkeypatch, tmp_path / ".env.local", tmp_path / "data")

    def _boom(*_args, **_kwargs):  # pragma: no cover - 호출되면 실패
        raise AssertionError("disabled 상태에서 env 파일을 읽었다")

    monkeypatch.setattr(mod, "_read_env_file", _boom)
    assert client.get(ENDPOINT).json()["status"] == "disabled"


@pytest.mark.parametrize("raw", ["0", "", "off", "no", "false"])
def test_falsy_flag_stays_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, raw: str
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, raw)
    assert client.get(ENDPOINT).json()["status"] == "disabled"


# ── 2. 자격증명 미노출 ───────────────────────────────────────────────────────
def test_no_credentials_in_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    env_path = _write_env(tmp_path, ENV_FULL_ON)
    _point_at(monkeypatch, env_path, tmp_path / "data")

    raw = client.get(ENDPOINT).text
    for secret in SECRETS:
        assert secret not in raw, f"응답에 {secret} 노출"
    # base_url 원문도 내보내지 않고 mock/real 라벨만 쓴다
    assert "mockapi.kiwoom.com" not in raw
    assert json.loads(raw)["config"]["broker_mode"] == "mock"


def test_env_parser_drops_non_allowlisted_keys(tmp_path: Path):
    env_path = _write_env(tmp_path, ENV_FULL_ON)
    values, as_of, reason = mod._read_env_file(env_path)

    assert reason == ""
    assert as_of  # mtime 기반 KST ISO
    assert set(values) <= set(mod._ALLOWED_ENV_KEYS)
    assert "KIWOOM_APP_KEY" not in values
    assert "TELEGRAM_BOT_TOKEN" not in values
    # 인라인 주석은 값에서 제거된다
    assert values["BARRO_DAEMON_STRATEGIES"] == "ai_swing"
    assert values["BARRO_AI_SWING_ENABLED"] == "1"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # 2026-08-12 D4 실측 버그 — 빈 값 + 인라인 주석이 주석을 값으로 삼았다
        ("BARRO_AI_SWING_FALLBACK=   # 빈값=완전 교집합만. scan_only 는 실진입 불가", ""),
        ("BARRO_AI_SWING_FALLBACK=#바로주석", ""),
        ("BARRO_AI_SWING_FALLBACK=scan_only   # 스캔 단독", "scan_only"),
        ("BARRO_AI_SWING_FALLBACK=scan_only", "scan_only"),
        ('BARRO_AI_SWING_FALLBACK="a#b"   # 따옴표 안 # 는 값', "a#b"),
        ("BARRO_AI_SWING_FALLBACK=", ""),
    ],
)
def test_inline_comment_stripping(tmp_path: Path, line: str, expected: str):
    env_path = _write_env(tmp_path, line + "\n")
    values, _, reason = mod._read_env_file(env_path)

    assert reason == ""
    assert values["BARRO_AI_SWING_FALLBACK"] == expected


def test_fallback_empty_means_full_intersection_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """`FALLBACK=` (빈값) 은 완전 교집합만 허용 — 주석이 값으로 새면 안 된다."""
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    env_path = _write_env(
        tmp_path,
        ENV_FULL_ON.replace(
            "BARRO_AI_SWING_FALLBACK=",
            "BARRO_AI_SWING_FALLBACK=   # 빈값=완전 교집합만",
        ),
    )
    _point_at(monkeypatch, env_path, tmp_path / "data")

    assert client.get(ENDPOINT).json()["config"]["fallback"] == ""


def test_allowlist_has_no_credential_keys():
    for key in mod._ALLOWED_ENV_KEYS:
        assert not any(t in key for t in ("KEY", "SECRET", "TOKEN", "PASSWORD")), key


# ── 3. 게이트 판정 ───────────────────────────────────────────────────────────
def test_gates_all_open_when_fully_activated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    source = tmp_path / "ai-trade-logs"
    source.mkdir()
    env_path = _write_env(
        tmp_path, ENV_FULL_ON + f"BARRO_AI_TRADE_DIR={source}\n"
    )
    _point_at(monkeypatch, env_path, tmp_path / "data")

    body = client.get(ENDPOINT).json()

    assert body["status"] == "ok"
    assert {g["id"] for g in body["gates"]} == {
        "strategy_included", "master", "entry", "budget", "source",
    }
    assert all(g["ok"] for g in body["gates"])
    assert body["entry_active"] is True
    assert body["config"]["budget_ratio"] == pytest.approx(0.10)
    assert body["config"]["max_positions"] == 1


@pytest.mark.parametrize(
    ("line", "gate_id"),
    [
        ("BARRO_DAEMON_STRATEGIES=f_zone,sf_zone", "strategy_included"),
        ("BARRO_AI_SWING_ENABLED=0", "master"),
        ("BARRO_AI_SWING_ENTRY_ENABLED=0", "entry"),
        ("BARRO_AI_SWING_BUDGET_RATIO=0", "budget"),
    ],
)
def test_single_closed_gate_blocks_entry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, line: str, gate_id: str
):
    """5중 게이트 중 하나만 닫혀도 entry_active 는 False 다."""
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    source = tmp_path / "ai-trade-logs"
    source.mkdir()
    env_path = _write_env(
        tmp_path, ENV_FULL_ON + f"BARRO_AI_TRADE_DIR={source}\n" + line + "\n"
    )
    _point_at(monkeypatch, env_path, tmp_path / "data")

    body = client.get(ENDPOINT).json()
    closed = {g["id"] for g in body["gates"] if not g["ok"]}

    assert gate_id in closed
    assert body["entry_active"] is False


def test_live_trading_off_blocks_entry_active(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    source = tmp_path / "ai-trade-logs"
    source.mkdir()
    env_path = _write_env(
        tmp_path,
        ENV_FULL_ON + f"BARRO_AI_TRADE_DIR={source}\nLIVE_TRADING_ENABLED=false\n",
    )
    _point_at(monkeypatch, env_path, tmp_path / "data")

    body = client.get(ENDPOINT).json()
    assert all(g["ok"] for g in body["gates"])   # 5중 게이트는 열려 있어도
    assert body["entry_active"] is False         # 실주문 마스터가 닫히면 비활성


# ── 4. 진실원천 = .env.local, 프로세스 env 불일치 보고 ───────────────────────
def test_env_file_beats_process_env_and_reports_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """운영 실측 재현 — 파일은 ai_swing, 기동 시점 프로세스 env 는 f_zone."""
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    monkeypatch.setenv("BARRO_DAEMON_STRATEGIES", "f_zone,sf_zone")
    source = tmp_path / "ai-trade-logs"
    source.mkdir()
    env_path = _write_env(tmp_path, ENV_FULL_ON + f"BARRO_AI_TRADE_DIR={source}\n")
    _point_at(monkeypatch, env_path, tmp_path / "data")

    body = client.get(ENDPOINT).json()

    gate = next(g for g in body["gates"] if g["id"] == "strategy_included")
    assert gate["ok"] is True and gate["value"] == "ai_swing"      # 파일 기준
    mismatch = {m["env"]: m for m in body["config_mismatch"]}
    assert mismatch["BARRO_DAEMON_STRATEGIES"]["env_local"] == "ai_swing"
    assert mismatch["BARRO_DAEMON_STRATEGIES"]["process"] == "f_zone,sf_zone"


# ── 5. 데이터 부재 강등 ──────────────────────────────────────────────────────
def test_missing_env_file_degrades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    _point_at(monkeypatch, tmp_path / "absent.env", tmp_path / "data")

    body = client.get(ENDPOINT).json()
    assert body["status"] == "no_data"
    assert body["config_source"]["reason"] == "env_file_missing"
    assert body["entry_active"] is False


def test_missing_artifacts_degrade_not_fabricate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    env_path = _write_env(tmp_path, ENV_FULL_ON)   # BARRO_AI_TRADE_DIR 없음
    _point_at(monkeypatch, env_path, tmp_path / "data")

    body = client.get(ENDPOINT).json()

    assert body["universe"]["status"] == "no_data"
    assert body["universe"]["reason"] == "ai_trade_dir_unset"
    assert body["universe"]["items"] == []
    assert body["entry_ready"] == {"ok": False, "reason": "ai_trade_dir_unset"}
    assert body["positions"]["status"] == "no_data"
    assert body["shadow"]["status"] == "no_data"
    assert body["shadow"]["reason"] == "shadow_never_run"
    assert body["shadow_history_days"] == 0


def test_universe_and_positions_when_present(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    source = tmp_path / "ai-trade-logs"
    source.mkdir()
    today = mod.datetime.now(mod._KST).date().isoformat()
    (source / f"watchlist_{today}.json").write_text(json.dumps({
        "date": today,
        "stocks": [{"code": "160190", "name": "하이젠알앤엠", "score": 87.78,
                    "blue_line_status": "above", "watermelon_signal": True,
                    "volume_ratio": 1.92}],
    }), encoding="utf-8")
    (source / f"predictions_{today}.json").write_text(json.dumps({
        "date": today,
        "stocks": [{"code": "160190", "name": "하이젠알앤엠", "rank": 1,
                    "total_score": 63.52, "confidence": 1.0,
                    "consensus_level": "만장일치"}],
    }), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "active_positions.json").write_text(json.dumps({
        "160190": {"symbol": "160190", "name": "하이젠알앤엠", "strategy": "ai_swing",
                   "entry_price": 12000.0, "entry_time": "2026-08-12T09:10:00+00:00",
                   "total_recommended_qty": 10, "sl_pct": -5.0,
                   "tranches": [{"qty": 6, "status": "filled"},
                                {"qty": 4, "status": "pending"}]},
        "062040": {"symbol": "062040", "name": "산일전기", "strategy": "f_zone",
                   "entry_price": 184700.0, "tranches": []},
    }), encoding="utf-8")

    env_path = _write_env(tmp_path, ENV_FULL_ON + f"BARRO_AI_TRADE_DIR={source}\n")
    _point_at(monkeypatch, env_path, data_dir)

    body = client.get(ENDPOINT).json()

    assert body["universe"]["status"] == "ok"
    assert body["universe"]["intersect_count"] == 1
    item = body["universe"]["items"][0]
    assert item["symbol"] == "160190" and item["consensus_level"] == "만장일치"
    assert body["entry_ready"]["ok"] is True

    # f_zone 은 섞이지 않고 ai_swing 만, 체결분(6)만 집계한다
    assert body["positions"]["status"] == "ok"
    assert [p["symbol"] for p in body["positions"]["items"]] == ["160190"]
    assert body["positions"]["items"][0]["filled_qty"] == 6


def test_corrupt_positions_file_degrades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """손상 파일에도 500 을 내지 않고, 저널 복구 쓰기도 하지 않는다."""
    monkeypatch.setenv(mod.ENV_DASHBOARD, "1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    broken = data_dir / "active_positions.json"
    broken.write_text("{ not json", encoding="utf-8")
    before = broken.read_bytes()
    env_path = _write_env(tmp_path, ENV_FULL_ON)
    _point_at(monkeypatch, env_path, data_dir)

    res = client.get(ENDPOINT)
    assert res.status_code == 200
    assert res.json()["positions"]["status"] == "no_data"
    assert broken.read_bytes() == before          # 읽기 전용 — 파일 무변경
    assert list(data_dir.iterdir()) == [broken]   # 격리/백업 파일 생성 없음


# ── 6. 주문 경로 미접촉 (§2 S2) ──────────────────────────────────────────────
def test_source_has_no_execution_symbols():
    """AST 로 **실제 참조**만 본다 — 문서·주석의 언급(왜 안 쓰는지 설명)은 허용."""
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            referenced.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            referenced.add(node.module or "")
            referenced.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)

    joined = " ".join(sorted(referenced))
    for banned in (
        "place_order", "send_order", "cancel_order", "kiwoom_native_orders",
        "core.execution", "ActivePositionStore", "supertrend_auto_trader",
    ):
        assert banned not in joined, f"읽기 전용 라우트가 {banned} 를 참조한다"

    # 파일 쓰기 계열 호출이 아예 없어야 한다
    for writer in ("write_text", "write_bytes", "replace", "unlink", "mkdir", "rename"):
        assert writer not in referenced, f"읽기 전용 라우트에 쓰기 호출 {writer}"
