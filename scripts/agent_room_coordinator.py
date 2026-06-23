#!/usr/bin/env python
"""에이전트 협업 방 코디네이터 — @barroAiTrade_agents_bot.

방(room_bus)의 finding/proposal/vote 를 읽어 **로컬 claude CLI**로 합성하고,
집단 결정(decision, ADVISORY)을 방에 게시 + 감사 로그(data/agent_room/decisions/<date>.jsonl).

설계 불변식(고정):
- 결정은 자문/합의(advisory). **주문 실행 경로 없음**(executor/place_buy/place_sell 미참조).
  결정은 방 게시 + 감사로그뿐. 매매 반영은 별도 게이트(advisory.json·policy.json·사람 /approve = HITL).
- LLM = 로컬 claude CLI (agent_advisory_writer._run_claude_cli 재사용, API키 없음).
- 킬스위치 BARRO_AGENT_ROOM_ENABLED(default 0) → 무동작. fail-open(claude/방 실패 → 사이클 skip).

사용:
  python scripts/agent_room_coordinator.py --once --dry      # 합성만(미게시)
  python scripts/agent_room_coordinator.py --interval 60      # 상시 루프
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.agents import room_bus  # noqa: E402
from scripts.agent_advisory_writer import _run_claude_cli  # noqa: E402  (로컬 claude 재사용)

_AGENT_ID = "coordinator"
_TRUTHY = {"1", "true", "yes", "on"}

_PROMPT = """너는 BarroAiTrade 트레이딩 에이전트 협업 방의 '코디네이터'다.
아래는 최근 에이전트들의 메시지(발견/제안/투표)다. 이를 종합해 **집단 결정(자문)**을 1건 산출하라.

★중요: 이건 자문(advisory)일 뿐 주문 실행이 아니다. 실제 매매는 별도 게이트(사람 승인)를 거친다.
결정 유형 예: 시장국면 콜, 전략 일시중지 권고, 리스크 throttle, 진입/청산 '제안'(HITL 필요).

최근 메시지:
{messages}

JSON 한 개만 출력(다른 텍스트 금지):
{{"summary": "<현 상황 1-2문장>", "decision": "<집단 결정/권고>", "confidence": 0.0~1.0,
  "rationale": "<근거>", "needs_human_approval": true|false, "recommendations": ["<행동 권고>", ...]}}"""


def _enabled() -> bool:
    return os.environ.get("BARRO_AGENT_ROOM_ENABLED", "0").strip().lower() in _TRUTHY


def _decisions_file() -> Path:
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return room_bus._room_dir() / "decisions" / f"{d}.jsonl"


def _fmt_msgs(msgs: list) -> str:
    lines = []
    for m in msgs[-40:]:  # 최근 40건 컨텍스트
        body = m.payload.get("text") or m.payload.get("summary") or json.dumps(
            m.payload, ensure_ascii=False)[:300]
        sym = f"[{m.symbol}]" if m.symbol else ""
        lines.append(f"- ({m.type}/{m.priority}) {m.from_agent}{sym} {m.topic}: {body}")
    return "\n".join(lines) if lines else "(메시지 없음)"


def _tally_votes(msgs: list) -> list[dict]:
    """proposal 별 vote 집계 → 정족수(≥66% agree) 결정 후보. (결정형 메시지는 별도.)"""
    proposals = {m.id: m for m in msgs if m.type == "proposal"}
    votes: dict[str, list[str]] = {}
    for m in msgs:
        if m.type == "vote":
            for ref in m.refs:
                if ref in proposals:
                    votes.setdefault(ref, []).append(
                        str(m.payload.get("vote", "abstain")).lower())
    out = []
    for pid, vs in votes.items():
        agree = sum(1 for v in vs if v in {"agree", "yes", "go"})
        total = len([v for v in vs if v != "abstain"])
        ratio = (agree / total) if total else 0.0
        out.append({"proposal_id": pid, "topic": proposals[pid].topic,
                    "agree": agree, "total": total, "ratio": round(ratio, 2),
                    "consensus": ratio >= 0.66 and total >= 2})
    return out


def _record(decision: dict) -> None:
    try:
        f = _decisions_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[coordinator] 결정 기록 실패(무시): {e}")


def run_once(dry: bool, timeout: float) -> dict | None:
    if not _enabled():
        print("[coordinator] BARRO_AGENT_ROOM_ENABLED=0 — 무동작")
        return None
    since = room_bus.load_cursor(_AGENT_ID)
    msgs = room_bus.tail(since_ts=since)
    if not msgs:
        print("[coordinator] 신규 메시지 없음 — skip")
        return None
    tallies = _tally_votes(room_bus.read_today())  # 투표는 당일 전체 기준
    prompt = _PROMPT.format(messages=_fmt_msgs(msgs))
    obj = _run_claude_cli(prompt, timeout)  # 로컬 claude CLI
    if obj is None:
        print("[coordinator] claude 합성 실패/타임아웃 — fail-open skip")
        return None
    decision = {
        "ts": room_bus._iso(), "by": _AGENT_ID,
        "summary": str(obj.get("summary", ""))[:500],
        "decision": str(obj.get("decision", ""))[:1000],
        "confidence": obj.get("confidence", 0.0),
        "rationale": str(obj.get("rationale", ""))[:1000],
        "needs_human_approval": bool(obj.get("needs_human_approval", True)),
        "recommendations": obj.get("recommendations", [])[:10],
        "consensus_proposals": [t for t in tallies if t["consensus"]],
        "n_input_msgs": len(msgs),
    }
    if dry:
        print("[coordinator][DRY] 결정(미게시):")
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return decision
    _record(decision)
    room_bus.post(
        _AGENT_ID, "decision", decision["summary"][:60] or "collective-decision",
        {"text": f"📌 {decision['decision']}\n근거: {decision['rationale']}\n"
                 f"신뢰도 {decision['confidence']} · 사람승인필요={decision['needs_human_approval']}",
         **decision},
        priority="high" if decision["needs_human_approval"] else "normal",
    )
    if msgs:
        room_bus.save_cursor(_AGENT_ID, msgs[-1].ts)
    print(f"[coordinator] 결정 게시: {decision['decision'][:80]} (입력 {len(msgs)}건)")
    return decision


def main() -> None:
    ap = argparse.ArgumentParser(description="에이전트 방 코디네이터(주문 없음·로컬 claude·advisory)")
    ap.add_argument("--once", action="store_true", help="1회 실행")
    ap.add_argument("--interval", type=float, default=60.0, help="루프 주기(초)")
    ap.add_argument("--dry", action="store_true", help="합성만(방 미게시·기록)")
    ap.add_argument("--timeout", type=float, default=30.0, help="claude 호출 타임아웃")
    args = ap.parse_args()
    if args.once:
        run_once(args.dry, args.timeout)
        return
    print(f"[coordinator] 루프 시작 (interval={args.interval}s, dry={args.dry})")
    while True:
        try:
            run_once(args.dry, args.timeout)
        except Exception as e:  # noqa: BLE001 — 루프 회복력(fail-open)
            print(f"[coordinator] 사이클 오류(무시): {type(e).__name__}: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
