#!/usr/bin/env python3
"""당일 에이전트 협업 도출 + 코드/설정 수정 요약본 생성기.

에이전트 협업(텔레그램 그룹채팅 = 비권위 미러)의 진실원천 JSONL 과
당일 git 커밋·policy.json history 를 모아 옵시디언 vault(docs/operations/daily-collab)
하위에 하루치 요약 md 를 만든다.

* read-only 집계 — 거래/주문 일절 없음. 모든 소스는 fail-open(없으면 "데이터 없음").
* data/agent_room/ 은 운영 머신에만 쌓이므로, 개발 머신에서는 협업 섹션이 비고
  git/policy 섹션만 채워진다.

사용:
    python scripts/_daily_collab_summary.py                 # 오늘(KST)
    python scripts/_daily_collab_summary.py --date 2026-06-24
    python scripts/_daily_collab_summary.py --date 2026-06-24 --stdout   # 파일 대신 출력

설계 문서: docs/operations/agent-collaboration.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parent.parent


def _today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _read_jsonl(path: Path) -> list[dict]:
    """JSONL 을 dict 리스트로(깨진 라인은 건너뜀, fail-open)."""
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return rows


# ── 1) 협업 메시지 개요 ──────────────────────────────────────────────
def summarize_messages(rows: list[dict]) -> dict:
    by_type: dict[str, int] = {}
    agents: dict[str, int] = {}
    topics: dict[str, int] = {}
    for m in rows:
        by_type[m.get("type", "?")] = by_type.get(m.get("type", "?"), 0) + 1
        agents[m.get("from_agent", "?")] = agents.get(m.get("from_agent", "?"), 0) + 1
        t = (m.get("topic") or "").strip()
        if t:
            topics[t] = topics.get(t, 0) + 1
    return {"total": len(rows), "by_type": by_type, "agents": agents, "topics": topics}


# ── 2) 당일 도출 결정 ───────────────────────────────────────────────
def format_decisions(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for d in rows:
        ts = d.get("ts", "")[:19].replace("T", " ")
        topic = d.get("topic") or d.get("summary", "")[:40] or "(무제)"
        conf = d.get("confidence")
        conf_s = f"{conf:.0%}" if isinstance(conf, (int, float)) else "—"
        hitl = "🧑 HITL 필요" if d.get("needs_human_approval") else "자동"
        out.append(f"### {ts} · {topic}  (confidence {conf_s} · {hitl})")
        if d.get("summary"):
            out.append(f"- **요약**: {d['summary']}")
        if d.get("decision"):
            out.append(f"- **결정**: {d['decision']}")
        if d.get("consensus"):
            out.append(f"- **합의**: {d['consensus']}")
        if d.get("dissent"):
            out.append(f"- **이견**: {d['dissent']}")
        recs = d.get("recommendations") or []
        if recs:
            out.append("- **권고**:")
            out.extend(f"  - {r}" for r in recs[:10])
        parts = d.get("participants") or []
        if parts:
            out.append(f"- 참여: {', '.join(parts)} · {d.get('rounds', '?')}라운드")
        out.append("")
    return out


# ── 3) 당일 코드/설정 수정 ──────────────────────────────────────────
def git_commits(date: str, repo: Path) -> list[str]:
    """당일 커밋(작성자 시각 기준) oneline 목록."""
    try:
        res = subprocess.run(
            ["git", "log", f"--since={date} 00:00:00", f"--until={date} 23:59:59",
             "--pretty=format:%h %s", "--no-merges"],
            cwd=str(repo), capture_output=True, text=True, timeout=20,
        )
        if res.returncode != 0:
            return []
        return [ln for ln in res.stdout.splitlines() if ln.strip()]
    except (subprocess.SubprocessError, OSError):
        return []


def policy_changes(date: str, data_dir: Path) -> list[str]:
    """policy.json history 중 당일 변경 항목."""
    pf = data_dir / "policy.json"
    if not pf.exists():
        return []
    try:
        pol = json.loads(pf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError):
        return []
    out: list[str] = []
    for h in pol.get("history", []):
        ts = str(h.get("timestamp", h.get("ts", "")))
        if not ts.startswith(date):
            continue
        src = h.get("source", "?")
        for ch in h.get("changes", []):
            out.append(
                f"`{ch.get('field')}` {ch.get('old')} → {ch.get('new')}"
                f" ({src}{'; ' + ch['reason'] if ch.get('reason') else ''})"
            )
    return out


# ── 요약 md 작성 ────────────────────────────────────────────────────
def build_markdown(date: str, msgs: dict, decisions: list[dict],
                   commits: list[str], pol_changes: list[str]) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L: list[str] = []
    L.append(f"# 에이전트 협업 일일 요약 — {date}")
    L.append("")
    L.append(f"> 생성 {now} · `scripts/_daily_collab_summary.py` · "
             "소스: data/agent_room + git + policy.json (read-only 집계)")
    L.append("> 설계: [[../agent-collaboration|에이전트 협업 시스템]]")
    L.append("")

    # §1 협업 개요
    L.append("## 1. 협업 개요 (당일 도출)")
    if msgs["total"] == 0:
        L.append("- 협업 메시지 없음 *(data/agent_room 미존재 — 운영 머신 EOD 에서 생성)*")
    else:
        bt = ", ".join(f"{k} {v}" for k, v in sorted(msgs["by_type"].items()))
        ag = ", ".join(f"{k}({v})" for k, v in sorted(msgs["agents"].items(),
                                                       key=lambda x: -x[1]))
        L.append(f"- 메시지 **{msgs['total']}건** — {bt}")
        L.append(f"- 참여 에이전트: {ag}")
        if msgs["topics"]:
            tp = ", ".join(f"{k}({v})" for k, v in sorted(msgs["topics"].items(),
                                                          key=lambda x: -x[1])[:8])
            L.append(f"- 주요 주제: {tp}")
    L.append("")

    # §2 결정
    L.append("## 2. 당일 도출 결정")
    if not decisions:
        L.append("- 합의 결정 없음 *(decisions/<date>.jsonl 미존재 또는 비어 있음)*")
        L.append("")
    else:
        L.append(f"총 **{len(decisions)}건**.")
        L.append("")
        L.extend(format_decisions(decisions))

    # §3 코드/설정 수정
    L.append("## 3. 당일 코드/설정 수정 (적용)")
    L.append("")
    L.append("### 코드 변경 (git, no-merge)")
    if commits:
        L.extend(f"- `{c}`" for c in commits)
    else:
        L.append("- 당일 커밋 없음")
    L.append("")
    L.append("### 정책 변경 (policy.json history)")
    if pol_changes:
        L.extend(f"- {c}" for c in pol_changes)
    else:
        L.append("- 당일 정책 변경 없음")
    L.append("")

    # §4 HITL 대기
    hitl = [d for d in decisions if d.get("needs_human_approval")]
    L.append("## 4. 후속 / HITL 대기")
    if hitl:
        for d in hitl:
            L.append(f"- ⏳ {d.get('topic') or d.get('summary', '')[:50]} "
                     f"— {d.get('decision', '')[:80]}")
    else:
        L.append("- 사람 승인 대기 항목 없음")
    L.append("")

    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="당일 에이전트 협업+수정 요약본 생성")
    ap.add_argument("--date", default=_today_kst(), help="YYYY-MM-DD (기본 오늘 KST)")
    ap.add_argument("--data-dir", default=None, help="데이터 루트(기본 BARRO_DATA_DIR 또는 ./data)")
    ap.add_argument("--out-dir", default=None,
                    help="출력 디렉터리(기본 docs/operations/daily-collab)")
    ap.add_argument("--repo", default=str(REPO_ROOT), help="git 저장소 경로")
    ap.add_argument("--stdout", action="store_true", help="파일 대신 표준출력")
    args = ap.parse_args()

    import os
    data_dir = Path(args.data_dir or os.environ.get("BARRO_DATA_DIR") or (REPO_ROOT / "data"))
    out_dir = Path(args.out_dir or (REPO_ROOT / "docs" / "operations" / "daily-collab"))
    date = args.date

    room = data_dir / "agent_room"
    msgs = summarize_messages(_read_jsonl(room / f"{date}.jsonl"))
    decisions = _read_jsonl(room / "decisions" / f"{date}.jsonl")
    commits = git_commits(date, Path(args.repo))
    pol_changes = policy_changes(date, data_dir)

    md = build_markdown(date, msgs, decisions, commits, pol_changes)

    if args.stdout:
        print(md)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"[collab-summary] {out_path} "
          f"(메시지 {msgs['total']}, 결정 {len(decisions)}, 커밋 {len(commits)}, 정책 {len(pol_changes)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
