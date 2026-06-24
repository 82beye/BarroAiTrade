#!/usr/bin/env python
"""에이전트 협업 방 — 다자 토론(deliberation) 엔진. @barroAiTrade_agents_bot.

역할별 에이전트가 ★서로의 의견을 읽고 반응(동의/보완/반박)★하며 여러 라운드 토론하고,
코디네이터가 합의를 합성한다. 단방향 리포트가 아닌 양방향 협업.

설계 불변식(room_bus·coordinator 와 동일):
- LLM = 로컬 claude CLI (헤드리스, API키 없음). room_bus 로 게시(버스+텔레그램 미러).
- refs 로 동료 메시지 스레딩. 라운드별 [R#] + stance(agree/disagree/build/new).
- ★주문 실행 경로 없음 — 자문(advisory)·HITL. 킬스위치 BARRO_AGENT_ROOM_ENABLED(default 0).★
- fail-open: 에이전트 1턴 실패 → 해당 턴만 skip, 토론 계속.

사용:
  python scripts/agent_room_discuss.py --topic "내일 장 대응" --context "..." --rounds 2
  python scripts/agent_room_discuss.py --from-bus            # 방의 최근 question/human 을 주제로
  python scripts/agent_room_discuss.py --topic "..." --dry   # 게시 없이 토론 출력만
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
from scripts.agent_advisory_writer import _run_claude_cli  # noqa: E402

_TRUTHY = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return os.environ.get("BARRO_AGENT_ROOM_ENABLED", "0").strip().lower() in _TRUTHY

# [6/24] 협업방 에이전트 LLM 모델(.env.local BARRO_ROOM_MODEL, 예: claude-sonnet-4-6). 빈값=CLI 기본.
_ROOM_MODEL = (os.environ.get("BARRO_ROOM_MODEL", "").strip() or None)


# ── 역할 정의 (역할별 관점 — 의견 다양성 보장) ────────────────────────────────
ROLES = [
    {"id": "market-analyst",  "persona": "시장국면·지수·시장폭·방향성·거시/심리. 오늘의 장 성격을 진단."},
    {"id": "risk-officer",    "persona": "리스크·노출·손절(SL)·드로다운·포지션 사이징·집중도/쏠림."},
    {"id": "execution-trader", "persona": "체결품질·진입 타이밍·슬리피지·API(429)·주문 운영·실행 가능성."},
    {"id": "strategy-quant",  "persona": "전략 성과·종목 선정·파라미터·승률/손익비·백테스트 정합성."},
    {"id": "macro-specialist", "persona": "글로벌/美 거시 — Growth·Inflation Sentiment, 거시 regime(고성장저인플레/저성장고인플레/박스권/위기), 美증시(나스닥·SOX)·연준·VIX·환율의 한국 영향·전략 게이팅·섹터 회전."},
    {"id": "trend-expert", "persona": "기술적 추세추종 — EMA(8/21/55)·ADX(14)·MACD. ADX≥25 게이트(whipsaw 방어)로 추세 강도·방향을 정량 판단(横보엔 비활성)."},
    {"id": "devils-advocate", "persona": "반론·맹점 지적·낙관 견제. 합의를 근거로 도전(groupthink 방지)."},
]

_AGENT_PROMPT = """너는 BarroAiTrade 트레이딩 협업방의 '{rid}' 에이전트다.
역할 관점: {persona}

[토론 주제]
{topic}

[지금까지의 토론 — 동료들의 의견(반드시 읽고 반응하라)]
{discussion}

지시:
- 네 역할 관점에서 의견을 도출하되 ★반드시 동료 의견에 반응하라★: 누구의 어떤 주장에 동의/보완/반박인지 명시하고 이유를 달아라(단순 반복·일반론 금지).
- 합의는 환영하나 근거 없는 합의는 거부(반론 정당). 라운드가 갈수록 '실행 가능한 합의'로 수렴.
- 구체적 수치·종목·근거. 한국어 3~5문장. ★주문 실행 금지(자문만)★.
JSON 한 개만 출력(그 외 텍스트 금지):
{{"opinion":"<역할 의견(동료 반응 포함)>","stance":"agree|disagree|build|new","reply_to":"<반응 대상 역할id 또는 빈문자열>","confidence":0.0}}"""

_SYNTH_PROMPT = """너는 협업방 '코디네이터'다. 아래는 역할 에이전트들의 다라운드 토론 전문이다.
합의점·남은 이견·결론을 종합해 **집단 결정(자문)**을 산출하라. ★자문일 뿐 주문 실행 아님(HITL).★

[토론 주제]
{topic}

[토론 전문]
{transcript}

JSON 한 개만 출력:
{{"summary":"<상황·토론 요지 1-2문장>","consensus":"<수렴된 합의/결론>","dissent":"<남은 이견(없으면 빈문자열)>","decision":"<집단 권고>","confidence":0.0,"needs_human_approval":true,"recommendations":["<행동 권고>"]}}"""


def _last_id_of(posted: list, rid: str):
    for (r, mid, _) in reversed(posted):
        if r == rid and mid:
            return mid
    return None


def _decisions_file() -> Path:
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return room_bus._room_dir() / "decisions" / f"{d}.jsonl"


def _topic_from_bus() -> str:
    """방의 최근 question/human 메시지를 주제로."""
    msgs = [m for m in room_bus.read_today() if m.type in {"question", "human"}]
    if not msgs:
        return ""
    last = msgs[-1]
    return last.payload.get("text") or last.topic or ""


def discuss(topic: str, context: str, rounds: int, timeout: float, dry: bool) -> dict | None:
    if not _enabled():
        print("[discuss] BARRO_AGENT_ROOM_ENABLED=0 — 무동작")
        return None
    label = (topic[:48] or "토론")
    full_topic = topic + (("\n\n[배경/데이터]\n" + context) if context else "")
    posted: list = []   # (rid, msg_id, opinion)
    log: list = []      # transcript lines

    if not dry:
        room_bus.post("coordinator", "question", label,
                      {"text": f"🗣 [다자토론 개시] {topic}\n참여: "
                               f"{', '.join(r['id'] for r in ROLES)} · {rounds}라운드"},
                      priority="high")
        time.sleep(0.3)

    for rnd in range(1, rounds + 1):
        for role in ROLES:
            disc = "\n".join(log[-20:]) if log else "(아직 의견 없음 — 네가 첫 발언자다)"
            prompt = _AGENT_PROMPT.format(rid=role["id"], persona=role["persona"],
                                          topic=full_topic, discussion=disc)
            obj = _run_claude_cli(prompt, timeout, _ROOM_MODEL)
            if not obj or not obj.get("opinion"):
                print(f"[discuss] R{rnd} {role['id']} 합성 실패 — skip")
                continue
            opinion = str(obj["opinion"]).strip()[:700]
            stance = str(obj.get("stance", "new")).lower().strip()
            reply_to = str(obj.get("reply_to", "")).strip()
            refs = [m for m in [_last_id_of(posted, reply_to)] if m] if reply_to else []
            arrow = f"→{reply_to}" if reply_to else ""
            log.append(f"[R{rnd}] {role['id']} ({stance}{arrow}): {opinion}")
            if dry:
                print(log[-1])
                posted.append((role["id"], None, opinion))
                continue
            mid = room_bus.post(
                role["id"], "proposal" if stance == "new" else "finding", label,
                {"text": f"[R{rnd}·{stance}{(' ' + arrow) if arrow else ''}] {opinion}"},
                refs=refs, priority="normal")
            posted.append((role["id"], mid, opinion))
            time.sleep(0.4)  # 텔레그램 rate

    transcript = "\n".join(log) if log else "(토론 없음)"
    sobj = _run_claude_cli(_SYNTH_PROMPT.format(topic=topic, transcript=transcript), timeout, _ROOM_MODEL)
    if not sobj:
        print("[discuss] 합의 합성 실패 — fail-open")
        return None
    decision = {
        "ts": room_bus._iso(), "by": "coordinator", "topic": topic, "mode": "deliberation",
        "summary": str(sobj.get("summary", ""))[:600],
        "consensus": str(sobj.get("consensus", ""))[:1000],
        "dissent": str(sobj.get("dissent", ""))[:600],
        "decision": str(sobj.get("decision", ""))[:1000],
        "confidence": sobj.get("confidence", 0.0),
        "needs_human_approval": bool(sobj.get("needs_human_approval", True)),
        "recommendations": sobj.get("recommendations", [])[:10],
        "rounds": rounds, "participants": [r["id"] for r in ROLES],
    }
    if dry:
        print("\n[discuss][DRY] 합의(미게시):")
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return decision
    try:
        f = _decisions_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[discuss] 기록 실패(무시): {e}")
    _diss = f"이견: {decision['dissent']}\n" if decision["dissent"] else ""
    room_bus.post("coordinator", "decision", label,
                  {"text": f"✅ [집단합의] {decision['decision']}\n"
                           f"수렴: {decision['consensus']}\n{_diss}"
                           f"신뢰도 {decision['confidence']} · 사람승인={decision['needs_human_approval']}",
                   **decision},
                  priority="high" if decision["needs_human_approval"] else "normal")
    print(f"[discuss] 집단합의 게시: {decision['decision'][:80]}")
    return decision


def main() -> None:
    ap = argparse.ArgumentParser(description="에이전트 방 다자토론(역할별 의견·상호반응·합의·자문)")
    ap.add_argument("--topic", default="", help="토론 주제")
    ap.add_argument("--context", default="", help="배경/데이터(선택)")
    ap.add_argument("--from-bus", action="store_true", help="방의 최근 question/human 을 주제로")
    ap.add_argument("--rounds", type=int, default=2, help="토론 라운드 수")
    ap.add_argument("--timeout", type=float, default=90.0, help="claude 호출 타임아웃")
    ap.add_argument("--dry", action="store_true", help="게시 없이 출력만")
    args = ap.parse_args()
    topic = args.topic or (_topic_from_bus() if args.from_bus else "")
    if not topic:
        print("[discuss] 주제 없음(--topic 또는 --from-bus 필요)")
        return
    discuss(topic, args.context, max(1, args.rounds), args.timeout, args.dry)


if __name__ == "__main__":
    main()
