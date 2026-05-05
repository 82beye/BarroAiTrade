"""
실시간 웹 대시보드

main.py가 작성하는 dashboard_status.json + trades.jsonl을 읽어
브라우저에서 실시간 계좌 현황을 표시한다.

사용:
    python -m monitoring.dashboard              # 기본 포트 8080
    python -m monitoring.dashboard --port 9090  # 포트 지정
"""

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
)


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    """대시보드 상태 JSON 반환"""
    status_file = LOGS_DIR / "dashboard_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            return jsonify(data)
        except Exception:
            pass
    return jsonify({
        "system_running": False,
        "error": "상태 파일 없음 — 트레이딩 시스템이 실행 중이 아닙니다",
    })


@app.route("/api/trades")
def api_trades():
    """오늘 매매 기록 반환 (최신순)"""
    today = date.today().isoformat()
    trades = []
    jsonl = LOGS_DIR / "trades.jsonl"
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                if t.get("timestamp", "").startswith(today):
                    trades.append(t)
            except json.JSONDecodeError:
                continue
    trades.reverse()
    return jsonify(trades)


@app.route("/api/logs")
def api_logs():
    """최근 시스템 로그 반환"""
    log_file = LOGS_DIR / "cron.log"
    lines = []
    if log_file.exists():
        all_lines = log_file.read_text(encoding="utf-8").splitlines()
        # 핵심 이벤트만 필터 (httpx 제외)
        filtered = [
            ln for ln in all_lines
            if "httpx" not in ln
        ]
        lines = filtered[-60:]
    return jsonify(lines)


def main():
    parser = argparse.ArgumentParser(description="AI-Trade 실시간 대시보드")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"대시보드 시작: http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
