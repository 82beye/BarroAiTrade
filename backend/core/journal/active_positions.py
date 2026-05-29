"""활성 포지션 메타 정보 영속화.

매수 주문 시 전략·진입가·분할 정보를 JSON 파일로 저장.
evaluate_holdings 에서 로드 → DCA 트리거 판단 + 전략 기반 손절 기준 적용.

구조:
  data/active_positions.json
  {
    "319400": {
      "symbol": "319400",
      "name": "현대무벡스",
      "strategy": "swing_38",
      "entry_price": 43300,
      "entry_time": "...",
      "total_recommended_qty": 339,
      "sl_pct": -4.0,
      "flu_rate": 11.89,
      "score": 0.865,
      "tranches": [
        {"tranche": 1, "ratio": 0.5, "qty": 170, "trigger_drop_pct": 0.0,
         "status": "filled", "order_no": "0128139", "filled_price": 43300, "filled_at": "..."},
        {"tranche": 2, "ratio": 0.25, "qty": 85, "trigger_drop_pct": -2.0,
         "status": "pending", "order_no": "", "filled_price": 0.0, "filled_at": ""},
        {"tranche": 3, "ratio": 0.25, "qty": 84, "trigger_drop_pct": -4.0,
         "status": "pending", "order_no": "", "filled_price": 0.0, "filled_at": ""}
      ]
    }
  }
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# BAR-OPS-09 — 5/28 swing_38 sync-loss 인시던트 재발 방지 (active_positions 무결성 인프라).
# 근본원인: 비원자적 write + 파싱 실패 시 조용한 {} 반환 + load→overwrite 패턴이 결합해
# 단 한 번의 {} 반환으로 살아있던 전 포지션 키가 영구 소실됨.
_BACKUP_RETENTION_DAYS = 7    # 백업 보존 기간 (일)
_BACKUP_MIN_KEEP = 5          # 나이와 무관하게 항상 보존할 최신 백업 수
_BACKUP_MAX_KEEP = 50         # 백업 디렉토리 하드 캡 (이를 넘으면 오래된 것부터 삭제)


@dataclass
class Tranche:
    tranche: int              # 1, 2, 3
    ratio: float              # 0.5 / 0.25 / 0.25
    qty: int
    trigger_drop_pct: float   # 0.0(즉시), -2.0(2% 하락 시), -4.0(4% 하락 시)
    status: str               # pending / filled / skipped
    order_no: str = ""
    filled_price: float = 0.0
    filled_at: str = ""


@dataclass
class ActivePosition:
    symbol: str
    name: str
    strategy: str             # swing_38 등 시뮬에서 채택된 전략
    entry_price: float        # 1분할 진입가
    entry_time: str           # ISO 8601
    total_recommended_qty: int
    sl_pct: float = -4.0
    flu_rate: float = 0.0
    score: float = 0.0
    tranches: list[Tranche] = field(default_factory=list)
    # 적응형 매도 정책용 추적 필드
    peak_pnl_rate: float = 0.0            # 보유 기간 중 최고 수익률
    peak_updated_at: str = ""             # peak 갱신 시점
    partial_tp_done: bool = False         # 1차 분할 익절 완료 여부
    # 2026-05-19 P2 fix — DCA 폴링 사이 일중 low 도달 추적용.
    # daemon 60s 폴링이 cur_price 만 보면 폴링 사이 low 놓침 → DCA trigger 미발동
    # (5/18 069500 L -6.4% 도달했어도 T2 pending 영구).
    trough_pnl_rate: float = 0.0          # 보유 기간 중 최저 수익률 (음수)
    trough_updated_at: str = ""           # trough 갱신 시점

    # ── 계산 헬퍼 ───────────────────────────────────────────────

    def avg_filled_price(self) -> float:
        filled = [t for t in self.tranches if t.status == "filled" and t.filled_price > 0]
        if not filled:
            return self.entry_price
        total_value = sum(t.filled_price * t.qty for t in filled)
        total_qty = sum(t.qty for t in filled)
        return total_value / total_qty if total_qty > 0 else self.entry_price

    def filled_qty(self) -> int:
        return sum(t.qty for t in self.tranches if t.status == "filled")

    def pending_tranches(self) -> list[Tranche]:
        return [t for t in self.tranches if t.status == "pending"]

    def sl_price(self) -> float:
        """평균 매수가 기준 SL 가격."""
        return self.avg_filled_price() * (1 + self.sl_pct / 100)

    def best_strategy(self) -> str:
        return self.strategy


def _build_tranches(total_qty: int, entry_price: float, order_no: str, filled_at: str) -> list[Tranche]:
    """2분할 트랜치 생성 — T1 60% 즉시, T2 40% at -1.5% (SL -4% 전 완료)."""
    q1 = round(total_qty * 0.6)
    q2 = total_qty - q1  # 나머지 전부 T2로

    now = filled_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [
        Tranche(
            tranche=1, ratio=0.6, qty=q1,
            trigger_drop_pct=0.0, status="filled",
            order_no=order_no, filled_price=entry_price, filled_at=now,
        ),
        Tranche(
            tranche=2, ratio=0.4, qty=q2,
            trigger_drop_pct=-1.5, status="pending",
        ),
    ]


class ActivePositionStore:
    """JSON 파일 기반 활성 포지션 영속."""

    def __init__(self, path: str | Path = "data/active_positions.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 덮어쓰기 전 유효 상태를 timestamped 로 보존하는 백업 디렉토리 (load_all 복원 소스).
        self._backup_dir = self.path.parent / "_active_positions_history"

    def load_all(self) -> dict[str, ActivePosition]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            # ★ 손상 시 조용한 {} 반환 금지 — 손상본 격리 + 백업 복원 시도.
            #   {} 를 그대로 반환하면 후속 upsert/remove 의 save_all 이 전 키를 영구 소실시킴.
            data = self._recover_corrupt(exc)
            if data is None:
                return {}
        return self._deserialize(data)

    def _deserialize(self, data: dict) -> dict[str, ActivePosition]:
        result: dict[str, ActivePosition] = {}
        for symbol, d in data.items():
            tranches = [Tranche(**t) for t in d.get("tranches", [])]
            pos = ActivePosition(
                symbol=d["symbol"],
                name=d.get("name", ""),
                strategy=d.get("strategy", ""),
                entry_price=float(d.get("entry_price", 0)),
                entry_time=d.get("entry_time", ""),
                total_recommended_qty=int(d.get("total_recommended_qty", 0)),
                sl_pct=float(d.get("sl_pct", -4.0)),
                flu_rate=float(d.get("flu_rate", 0)),
                score=float(d.get("score", 0)),
                tranches=tranches,
                # 2026-05-19 P1 fix — 적응형 매도 추적 필드 read 누락 버그.
                # upsert 로 저장 되어도 다음 load 시 default 로 reset 되어
                # partial_tp_done=False 영구 유지 → PARTIAL_TP 매 사이클 재발동
                # (5/18 100790 9번, 005930 7번, 080220 3번 분할매도 폭주 원인).
                peak_pnl_rate=float(d.get("peak_pnl_rate", 0.0)),
                peak_updated_at=d.get("peak_updated_at", ""),
                partial_tp_done=bool(d.get("partial_tp_done", False)),
                trough_pnl_rate=float(d.get("trough_pnl_rate", 0.0)),
                trough_updated_at=d.get("trough_updated_at", ""),
            )
            result[symbol] = pos
        return result

    def save_all(self, positions: dict[str, ActivePosition]) -> None:
        data = {symbol: asdict(pos) for symbol, pos in positions.items()}
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        # 1) 덮어쓰기 직전 현재 '유효 비어있지 않은' 상태를 백업 (복원 소스 확보).
        self._backup_current()
        # 2) atomic write — tmp 작성 후 os.replace. write 중단에도 원본 파일 불변.
        self._atomic_write(payload)

    # ── 무결성 인프라 (BAR-OPS-09 sync-loss 재발 방지) ──────────────────

    @staticmethod
    def _now_stamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")

    def _atomic_write(self, payload: str) -> None:
        """tempfile 에 기록 후 os.replace 로 원자적 교체 (POSIX rename).

        write 도중 프로세스 종료/디스크 이슈가 나도 원본 self.path 는 손상되지 않는다
        (os.replace 직전까지 원본 불변). 기존 plain write_text 의 truncate-then-write
        손상 위험 제거.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{self.path.stem}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            # 실패 시 tmp 정리 — 원본은 os.replace 전이라 그대로 보존됨.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _backup_current(self) -> None:
        """현재 디스크 상태가 '유효 + 비어있지 않은' 경우에만 timestamped 백업.

        손상본/빈 {} 는 백업하지 않는다 (좋은 상태만 복원 소스로 보존). 백업 실패는
        저장 자체를 막지 않는다 (best-effort).
        """
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return  # 손상본은 백업 안 함 — load_all 이 격리/복원 처리.
        if not isinstance(parsed, dict) or not parsed:
            return  # 빈 {} 는 복원 가치 없음.
        try:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            dest = self._backup_dir / f"active_positions_{self._now_stamp()}.json"
            dest.write_text(raw, encoding="utf-8")
            self._prune_backups()
        except OSError as exc:
            logger.error(
                "active_positions 백업 실패 (%s) — 저장은 계속 진행", type(exc).__name__
            )

    def _prune_backups(self) -> None:
        """백업 디렉토리 정리 — 하드 캡(_BACKUP_MAX_KEEP) + 7일 경과분 삭제.

        단 최신 _BACKUP_MIN_KEEP 개는 나이와 무관하게 항상 보존 (복원 소스 소실 방지).
        """
        try:
            backups = sorted(
                self._backup_dir.glob("active_positions_*.json"), reverse=True
            )
        except OSError:
            return
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=_BACKUP_RETENTION_DAYS)
        ).timestamp()
        for idx, b in enumerate(backups):
            over_cap = idx >= _BACKUP_MAX_KEEP
            keep_recent = idx < _BACKUP_MIN_KEEP
            try:
                too_old = b.stat().st_mtime < cutoff
            except OSError:
                too_old = False
            if over_cap or (too_old and not keep_recent):
                try:
                    b.unlink()
                except OSError:
                    pass

    def _load_latest_backup(self) -> Optional[tuple[str, dict]]:
        """최신 백업부터 순회하며 파싱 가능한 비어있지 않은 dict 를 반환 (raw, parsed)."""
        if not self._backup_dir.is_dir():
            return None
        try:
            backups = sorted(
                self._backup_dir.glob("active_positions_*.json"), reverse=True
            )
        except OSError:
            return None
        for b in backups:
            try:
                raw = b.read_text(encoding="utf-8")
                parsed = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict) and parsed:
                return raw, parsed
        return None

    def _recover_corrupt(self, exc: Exception) -> Optional[dict]:
        """손상 파일을 .corrupt-<ts> 로 격리하고 최신 백업에서 복원 시도.

        복원 성공 시: 정상 파일로 atomic 재기록(이후 upsert/remove 안전) 후 parsed dict 반환.
        복원 불가 시: None 반환(호출부가 {} 반환) + CRITICAL 로그로 sync 누락 위험 경고.
        """
        ts = self._now_stamp()
        quarantine = self.path.with_name(f"{self.path.name}.corrupt-{ts}")
        try:
            os.replace(self.path, quarantine)
            logger.error(
                "active_positions.json 손상 감지 (%s) — %s 로 격리",
                type(exc).__name__, quarantine.name,
            )
        except OSError:
            logger.error(
                "active_positions.json 손상 격리 실패 (%s)", type(exc).__name__
            )

        restored = self._load_latest_backup()
        if restored is None:
            logger.critical(
                "active_positions.json 복원 실패 — 사용 가능한 백업 없음. 빈 상태로 진행하므로 "
                "broker 보유와 sync 누락 위험(5/28 swing_38 인시던트 유형). 운영 점검 필요."
            )
            return None

        payload, parsed = restored
        try:
            self._atomic_write(payload)  # 정상 파일 재기록 → 이후 save_all 의 {} 전파 차단.
            logger.warning(
                "active_positions.json 백업에서 복원 완료 (%d 종목)", len(parsed)
            )
        except OSError as werr:
            logger.error(
                "복원본 재기록 실패 (%s) — 메모리 데이터로 계속 진행", type(werr).__name__
            )
        return parsed

    def upsert(self, pos: ActivePosition) -> None:
        all_pos = self.load_all()
        all_pos[pos.symbol] = pos
        self.save_all(all_pos)

    def remove(self, symbol: str) -> None:
        all_pos = self.load_all()
        all_pos.pop(symbol, None)
        self.save_all(all_pos)

    def get(self, symbol: str) -> Optional[ActivePosition]:
        return self.load_all().get(symbol)

    def create_from_order(
        self,
        symbol: str,
        name: str,
        strategy: str,
        entry_price: float,
        total_recommended_qty: int,
        order_no: str,
        sl_pct: float = -4.0,
        flu_rate: float = 0.0,
        score: float = 0.0,
    ) -> ActivePosition:
        """매수 주문 직후 호출 — 3분할 트랜치 생성 후 저장."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tranches = _build_tranches(total_recommended_qty, entry_price, order_no, now)
        pos = ActivePosition(
            symbol=symbol, name=name, strategy=strategy,
            entry_price=entry_price, entry_time=now,
            total_recommended_qty=total_recommended_qty,
            sl_pct=sl_pct, flu_rate=flu_rate, score=score,
            tranches=tranches,
        )
        self.upsert(pos)
        return pos


__all__ = ["ActivePosition", "ActivePositionStore", "Tranche"]
