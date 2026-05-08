# BAR-OPS-23 — 일일 markdown 리포트 텔레그램 전송

## 변경
- `TelegramNotifier.send_chunks(text, chunk_size=3900)`:
  - 줄 단위 자동 분할 (Telegram 4096 char 제한)
  - 한 줄 자체가 chunk_size 초과 시 강제 분할
  - 다중 chunk 일 때 `_part N/M_` prefix 자동 추가
- `scripts/generate_daily_report.py --telegram`:
  - markdown 보존 위해 `parse_mode=HTML` + `<pre>` 래핑
  - 비동기 send_chunks 호출

## 실 검증
```bash
$ python scripts/generate_daily_report.py data/simulation_log.csv \
    --output reports/2026-05-08-v2.md \
    --title "2026-05-08 일일 시뮬" --telegram

📝 markdown 리포트 → reports/2026-05-08-v2.md (565 bytes)
📨 텔레그램 전송 1 chunk
```
→ 사용자 텔레그램에 리포트 도착 (HTML `<pre>` 보존).

## 운영 — 매일 16시 자동 리포트
```bash
0 16 * * 1-5 python scripts/generate_daily_report.py data/simulation_log.csv \
  --output "reports/$(date +%F).md" \
  --title "$(date +%F) 일일 시뮬 리포트" \
  --telegram
```

→ markdown 파일 + 텔레그램 본문 동시 전송. 외부 어디서나 일일 결과 확인.

## Tests
- 4 신규 (single chunk / multi-chunk / oversized line / empty raise)
- 회귀 **754 → 758 (+4)**, 0 fail

## 다음
- BAR-OPS-24 — 양방향 봇 (`/sim`, `/eval` 텔레그램 명령)
- BAR-OPS-25 — 실현손익 (ka10073) 누적
- BAR-OPS-26 — WebSocket 실시간 시세
