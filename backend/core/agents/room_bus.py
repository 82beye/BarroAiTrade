"""에이전트 협업 방 버스 (room_bus) — @barroAiTrade_agents_bot.

에이전트들이 서로의 발견(finding)·제안(proposal)·투표(vote)·결정(decision)을 공유하는
내구성 버스 + 텔레그램 방 미러.

설계 불변식(고정):
- 진실원천 = data/agent_room/<YYYY-MM-DD>.jsonl (append-only, atomic).
- 미러 = 별도 봇 BARRO_AGENTS_BOT_TOKEN → 그룹 BARRO_AGENTS_CHAT_ID (사람 가시용, 비권위).
- 킬스위치 BARRO_AGENT_ROOM_ENABLED(default 0) → post no-op (거래 무영향).
- fail-open: 토큰/파일/네트워크 실패 → 로그만, 예외 전파 안 함.
- ★주문 API(executor/place_buy/place_sell)를 import/호출하지 않는다 — 실행 경로 없음(안전).★
  결정은 advisory.json(게이트 default-OFF) 또는 사람 승인으로만 흘러간다.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO = Path(__file__).resolve().parents[3]
_TRUTHY = {"1", "true", "yes", "on"}
MSG_TYPES = {"finding", "proposal", "vote", "decision", "question", "human"}
PRIORITIES = {"critical", "high", "normal", "low"}


def _enabled() -> bool:
    return os.environ.get("BARRO_AGENT_ROOM_ENABLED", "0").strip().lower() in _TRUTHY


def _data_dir() -> Path:
    return Path(os.environ.get("BARRO_DATA_DIR", str(_REPO / "data")))


def _room_dir() -> Path:
    return _data_dir() / "agent_room"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _room_file(date: Optional[str] = None) -> Path:
    d = date or _now().strftime("%Y-%m-%d")
    return _room_dir() / f"{d}.jsonl"


@dataclass
class RoomMessage:
    from_agent: str
    type: str
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    symbol: Optional[str] = None
    refs: list[str] = field(default_factory=list)
    id: str = ""
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.ts:
            self.ts = _iso()
        if self.type not in MSG_TYPES:
            self.type = "finding"
        if self.priority not in PRIORITIES:
            self.priority = "normal"


# ── 발신 ─────────────────────────────────────────────────────────────────────
def post(
    from_agent: str,
    type: str,
    topic: str,
    payload: Optional[dict] = None,
    *,
    priority: str = "normal",
    symbol: Optional[str] = None,
    refs: Optional[list[str]] = None,
    mirror: bool = True,
) -> Optional[str]:
    """방에 메시지 게시. enabled=0 이면 no-op(None). 실패는 fail-open(로그만).

    반환: 메시지 id (게시 성공) / None (비활성·실패)."""
    if not _enabled():
        return None
    msg = RoomMessage(
        from_agent=str(from_agent), type=str(type), topic=str(topic),
        payload=payload or {}, priority=priority, symbol=symbol, refs=refs or [],
    )
    ok = _append(msg)
    if mirror:
        try:
            _tg_mirror(msg)
        except Exception as e:  # noqa: BLE001 — fail-open
            print(f"[room_bus] 텔레그램 미러 실패(무시): {type}:{topic} {e}")
    return msg.id if ok else None


def _append(msg: RoomMessage) -> bool:
    """JSONL append (atomic line). 실패 → False(fail-open)."""
    try:
        path = _room_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(msg), ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        return True
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[room_bus] 버스 append 실패(무시): {e}")
        return False


# ── 수신(읽기) ───────────────────────────────────────────────────────────────
def read_date(date: Optional[str] = None) -> list[RoomMessage]:
    """해당 날짜 방 메시지 전체(시간순). 파일 부재 → []."""
    out: list[RoomMessage] = []
    path = _room_file(date)
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(RoomMessage(**json.loads(line)))
            except Exception:  # noqa: BLE001 — 손상 라인 skip
                continue
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[room_bus] 버스 read 실패(무시): {e}")
    return out


def read_today() -> list[RoomMessage]:
    return read_date(None)


def tail(since_ts: Optional[str] = None, *, types: Optional[set[str]] = None,
         limit: Optional[int] = None) -> list[RoomMessage]:
    """since_ts 이후(미포함) 메시지. types/limit 필터. 합의·코디네이터용."""
    msgs = [m for m in read_today() if (since_ts is None or m.ts > since_ts)]
    if types:
        msgs = [m for m in msgs if m.type in types]
    if limit:
        msgs = msgs[-limit:]
    return msgs


# ── 텔레그램 방 미러 (별도 봇·그룹) ──────────────────────────────────────────
_ICON = {"finding": "🔎", "proposal": "📋", "vote": "🗳", "decision": "✅",
         "question": "❓", "human": "🧑"}


def _tg_mirror(msg: RoomMessage) -> None:
    """별도 agents-bot 토큰으로 방(그룹)에 미러. 토큰 없으면 no-op(fail-open)."""
    token = os.environ.get("BARRO_AGENTS_BOT_TOKEN", "").strip()
    chat = os.environ.get("BARRO_AGENTS_CHAT_ID", "").strip()
    if not token or not chat:
        return  # 미설정 → 미러 비활성(버스는 정상)
    import html as _html
    import urllib.parse
    import urllib.request
    icon = _ICON.get(msg.type, "•")
    sym = f" [{msg.symbol}]" if msg.symbol else ""
    raw = msg.payload.get("text") or msg.payload.get("summary") or json.dumps(
        msg.payload, ensure_ascii=False)
    # 텔레그램 4096자 제한 + HTML parse 안전: 본문 절단 후 escape(헤더/푸터 여유 확보).
    # (본문의 '<' '>' '&' 가 parse_mode=HTML 을 깨 HTTP400 나던 문제 — escape 로 해소.)
    _MAX_BODY = 3500
    if len(raw) > _MAX_BODY:
        raw = raw[:_MAX_BODY] + " …(생략)"
    body = _html.escape(raw)
    agent = _html.escape(str(msg.from_agent))
    topic = _html.escape(str(msg.topic))
    esym = _html.escape(sym)
    text = f"{icon} <b>{agent}</b>{esym} · {topic}\n{body}\n<code>{msg.type}/{msg.priority} id={msg.id}</code>"

    def _send(payload_text: str, parse: Optional[str]) -> None:
        params = {"chat_id": chat, "text": payload_text}
        if parse:
            params["parse_mode"] = parse
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        urllib.request.urlopen(req, timeout=10).read()

    try:
        _send(text, "HTML")
    except Exception:  # noqa: BLE001 — HTML parse/길이 실패 → plain text 폴백
        try:
            _send(f"{msg.from_agent}{sym} · {msg.topic}\n{raw}\n[{msg.type}/{msg.priority} id={msg.id}]", None)
        except Exception as e:  # noqa: BLE001 — fail-open
            print(f"[room_bus] tg sendMessage 실패(무시): {e}")


# ── 상태(코디네이터 커서) ─────────────────────────────────────────────────────
def load_cursor(agent_id: str) -> Optional[str]:
    """agent_id 의 마지막 처리 ts. 부재 → None."""
    p = _room_dir() / f".cursor_{agent_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("last_ts")
    except Exception:  # noqa: BLE001
        return None


def save_cursor(agent_id: str, last_ts: str) -> None:
    try:
        p = _room_dir() / f".cursor_{agent_id}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            "w", dir=str(p.parent), delete=False, encoding="utf-8")
        json.dump({"last_ts": last_ts, "saved_at": _iso()}, tmp, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, p)
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[room_bus] cursor 저장 실패(무시): {e}")
