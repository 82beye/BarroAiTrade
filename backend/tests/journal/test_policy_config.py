"""BAR-OPS-31 — PolicyConfigStore 테스트."""
from __future__ import annotations

import json

from backend.core.journal.policy_config import (
    PolicyConfig,
    PolicyConfigStore,
)
from backend.core.journal.policy_tuner import PolicyRecommendation


def test_load_missing_returns_defaults(tmp_path):
    store = PolicyConfigStore(tmp_path / "policy.json")
    cfg = store.load()
    assert cfg.min_score == 0.5
    assert cfg.stop_loss_pct == -2.0
    assert cfg.max_per_position == 0.30


def test_save_load_round_trip(tmp_path):
    store = PolicyConfigStore(tmp_path / "policy.json")
    cfg = PolicyConfig(min_score=0.7, stop_loss_pct=-1.5)
    store.save(cfg)
    loaded = store.load()
    assert loaded.min_score == 0.7
    assert loaded.stop_loss_pct == -1.5


def test_load_corrupt_json_returns_defaults(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text("{not valid json")
    cfg = PolicyConfigStore(p).load()
    assert cfg.min_score == 0.5


def test_apply_records_history(tmp_path):
    store = PolicyConfigStore(tmp_path / "policy.json")
    rec = PolicyRecommendation(
        field="min_score", current=0.5, recommended=0.6,
        reason="과대 시뮬 67%", severity="warn",
    )
    cfg, changes = store.apply([rec], source="tune")
    assert len(changes) == 1
    assert changes[0]["field"] == "min_score"
    assert changes[0]["old"] == 0.5
    assert changes[0]["new"] == 0.6
    assert cfg.min_score == 0.6
    # history 기록
    assert len(cfg.history) == 1
    entry = cfg.history[0]
    assert entry["source"] == "tune"
    assert entry["changes"][0]["field"] == "min_score"


def test_apply_no_change_skips(tmp_path):
    """recommended == current 일 때 history 추가 안 됨."""
    store = PolicyConfigStore(tmp_path / "policy.json")
    rec = PolicyRecommendation(
        field="min_score", current=0.5, recommended=0.5,    # 동일
        reason="", severity="info",
    )
    cfg, changes = store.apply([rec])
    assert changes == []
    assert cfg.history == []


def test_apply_multiple_recommendations(tmp_path):
    store = PolicyConfigStore(tmp_path / "policy.json")
    recs = [
        PolicyRecommendation(field="min_score", current=0.5, recommended=0.6,
                              reason="x", severity="warn"),
        PolicyRecommendation(field="stop_loss_pct", current=-2.0, recommended=-1.5,
                              reason="y", severity="critical"),
    ]
    cfg, changes = store.apply(recs)
    assert len(changes) == 2
    assert cfg.min_score == 0.6
    assert cfg.stop_loss_pct == -1.5
    assert len(cfg.history) == 1                # 한 호출 = 한 history entry
    assert len(cfg.history[0]["changes"]) == 2


def test_apply_unknown_field_ignored(tmp_path):
    store = PolicyConfigStore(tmp_path / "policy.json")
    rec = PolicyRecommendation(
        field="nonexistent_field", current=1.0, recommended=2.0,
        reason="", severity="info",
    )
    cfg, changes = store.apply([rec])
    assert changes == []


def test_history_caps_at_50(tmp_path):
    store = PolicyConfigStore(tmp_path / "policy.json")
    for i in range(60):
        rec = PolicyRecommendation(
            field="min_score",
            current=round(0.3 + 0.001 * i, 4),
            recommended=round(0.3 + 0.001 * (i + 1), 4),
            reason="", severity="info",
        )
        store.apply([rec])
    cfg = store.load()
    assert len(cfg.history) == 50
