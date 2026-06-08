"""BAR-OPS-09 — ActivePositionStore 무결성 인프라.

5/28 swing_38 sync-loss 인시던트 재발 방지: atomic write + load_all 손상 복원 + 자동 백업.
근본원인 회귀 테스트(test_corruption_does_not_cascade_key_loss)가 핵심.
"""
from __future__ import annotations

import json

from backend.core.journal.active_positions import ActivePosition, ActivePositionStore


def _pos(symbol: str, strategy: str = "swing_38", qty: int = 10) -> ActivePosition:
    return ActivePosition(
        symbol=symbol,
        name=f"종목{symbol}",
        strategy=strategy,
        entry_price=10000.0,
        entry_time="2026-05-28T00:00:00+00:00",
        total_recommended_qty=qty,
    )


def _store(tmp_path) -> ActivePositionStore:
    return ActivePositionStore(path=tmp_path / "active_positions.json")


class TestRoundTrip:
    def test_save_and_load_preserves_data(self, tmp_path):
        store = _store(tmp_path)
        store.save_all({"001820": _pos("001820"), "006660": _pos("006660")})
        loaded = store.load_all()
        assert set(loaded) == {"001820", "006660"}
        assert loaded["001820"].strategy == "swing_38"

    def test_missing_file_returns_empty(self, tmp_path):
        assert _store(tmp_path).load_all() == {}

    def test_upsert_and_remove(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(_pos("001820"))
        store.upsert(_pos("006660"))
        store.remove("001820")
        assert set(store.load_all()) == {"006660"}


class TestAtomicWrite:
    def test_no_tmp_file_leftover(self, tmp_path):
        store = _store(tmp_path)
        store.save_all({"001820": _pos("001820")})
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_valid_json_on_disk(self, tmp_path):
        store = _store(tmp_path)
        store.save_all({"001820": _pos("001820")})
        on_disk = json.loads((tmp_path / "active_positions.json").read_text("utf-8"))
        assert on_disk["001820"]["symbol"] == "001820"


class TestBackup:
    def test_backup_created_on_save(self, tmp_path):
        store = _store(tmp_path)
        store.save_all({"001820": _pos("001820")})       # 백업 없음(첫 저장 전 파일 없음)
        store.save_all({"001820": _pos("001820"), "006660": _pos("006660")})
        backups = list((tmp_path / "_active_positions_history").glob("active_positions_*.json"))
        assert len(backups) >= 1  # 두 번째 save 직전 첫 상태가 백업됨

    def test_empty_dict_not_backed_up(self, tmp_path):
        store = _store(tmp_path)
        store.save_all({})            # 빈 {} 저장
        store.save_all({})            # 다시 빈 {} → 직전이 {} 라 백업 가치 없음
        backup_dir = tmp_path / "_active_positions_history"
        backups = list(backup_dir.glob("active_positions_*.json")) if backup_dir.is_dir() else []
        assert backups == []


class TestCorruptionRecovery:
    def test_corruption_does_not_cascade_key_loss(self, tmp_path):
        """★ 근본원인 회귀: 손상된 파일에서 upsert 해도 기존 키가 소실되지 않는다.

        구버전 동작: load_all()→{} → upsert가 1키만 save → 나머지 영구 소실(인시던트).
        신버전: load_all()이 백업에서 복원 → upsert가 전 키 보존.
        """
        store = _store(tmp_path)
        # 1) swing_38 4종목 정상 저장 (백업 소스 생성을 위해 한 번 더 save)
        four = {s: _pos(s) for s in ("001820", "006660", "012330", "034220")}
        store.save_all(four)
        store.save_all(four)  # 직전 4종목 상태가 _active_positions_history 에 백업됨
        # 2) 파일 손상 시뮬레이션 (write 중단 등)
        store.path.write_text('{"001820": {"symbol": "001', encoding="utf-8")  # 깨진 JSON
        # 3) 손상 상태에서 신규 종목 upsert (인시던트 재현 시나리오)
        store.upsert(_pos("139480"))
        # 4) 검증: 기존 4종목 + 신규 1종목 모두 보존 (캐스케이드 소실 없음)
        loaded = store.load_all()
        assert set(loaded) == {"001820", "006660", "012330", "034220", "139480"}

    def test_corrupt_file_quarantined(self, tmp_path):
        store = _store(tmp_path)
        store.save_all({"001820": _pos("001820")})
        store.save_all({"001820": _pos("001820")})  # 백업 생성
        store.path.write_text("{broken", encoding="utf-8")
        store.load_all()  # 복원 트리거
        quarantined = list(tmp_path.glob("active_positions.json.corrupt-*"))
        assert len(quarantined) == 1

    def test_corrupt_without_backup_returns_empty(self, tmp_path):
        store = _store(tmp_path)
        store.path.write_text("{broken json", encoding="utf-8")  # 백업 없음
        assert store.load_all() == {}
        # 손상본은 격리됐어야 함 (조용한 무시 금지)
        assert len(list(tmp_path.glob("active_positions.json.corrupt-*"))) == 1

    def test_load_heals_file_after_restore(self, tmp_path):
        """복원 후 디스크 파일이 정상 JSON 으로 재기록되어 다음 save 가 안전해야 한다."""
        store = _store(tmp_path)
        store.save_all({"001820": _pos("001820"), "006660": _pos("006660")})
        store.save_all({"001820": _pos("001820"), "006660": _pos("006660")})  # 백업
        store.path.write_text("CORRUPT", encoding="utf-8")
        store.load_all()  # 복원 + 재기록
        healed = json.loads(store.path.read_text("utf-8"))  # 정상 JSON 이어야 함
        assert set(healed) == {"001820", "006660"}


# ════════════════════════════════════════════════════════════════════════════
# BAR-OPS-35 — create_from_order(single_tranche=True): sync-loss 방지.
# supertrend 전량 단일주문 진입에서 178/118 분할 모델링이 broker 보유와 어긋나는
# 2026-06-08 001740(audit 296 vs filled 178) 문제 방지 — 전량을 단일 filled tranche 로.
# ════════════════════════════════════════════════════════════════════════════
def test_single_tranche_marks_full_qty_filled(tmp_path):
    """single_tranche=True → 전량 1개 filled tranche, pending 없음, filled_qty()=total."""
    store = ActivePositionStore(path=tmp_path / "ap.json")
    pos = store.create_from_order(
        symbol="001740", name="SK네트웍스", strategy="supertrend",
        entry_price=13500.0, total_recommended_qty=296, order_no="0079949",
        single_tranche=True,
    )
    assert len(pos.tranches) == 1
    assert pos.tranches[0].status == "filled"
    assert pos.tranches[0].qty == 296
    assert pos.filled_qty() == 296          # broker 보유와 일치 (sync-loss 없음)
    assert pos.pending_tranches() == []


def test_default_still_splits_dca_tranches(tmp_path):
    """기본(single_tranche=False) → 기존 60/40 분할 유지(회귀 보호)."""
    store = ActivePositionStore(path=tmp_path / "ap.json")
    pos = store.create_from_order(
        symbol="001740", name="SK네트웍스", strategy="supertrend",
        entry_price=13500.0, total_recommended_qty=296, order_no="0079949",
    )
    assert len(pos.tranches) == 2
    assert pos.tranches[0].qty == 178 and pos.tranches[0].status == "filled"   # round(296*0.6)
    assert pos.tranches[1].qty == 118 and pos.tranches[1].status == "pending"
    assert pos.filled_qty() == 178
