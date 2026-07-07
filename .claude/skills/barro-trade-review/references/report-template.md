# 서술형 리포트 템플릿 (Phase 4)

`reports/<date>/<date>_매매복기.md` + `.html`. 숫자는 `reports/strategy_audit_<date>.json`(진실원천)에서 인용, 재계산 금지. 섹션 순서:

1. **제목 + 메타**: `# BarroAiTrade 매매복기 — <date> (요일)` / 생성시각·직전영업일·실측 여부(브로커 실측 / 추정).
2. **데이터 무결성** (verify_eod_data 결과):
   - PASS → "✓ 데이터 무결성 — 정상(브로커 실측)" + fill_audit 행수·EOD balance 시각·buy_audit 유무.
   - NG → "⚠️ 데이터 완전성 경고(먼저 읽으세요)" 표(fill_audit/EOD balance/buy_audit 부재) + "손익은 추정" 명시 + 추정 방법(네이버 1분봉, 가능시 active_positions_history 실측 매수가 교체).
3. **한 줄 요약**: 실현 순손익(실측/추정) · 트립·승률 · gross 와 비용(gross의 N배) · **이월 vs 당일신규 분해**(이월이 흑자를 만든 날엔 강조) · EOD 미실현 반영 종합.
4. **종합 손익 표**: gross / 비용 / 실현순손익 / (└이월분 └당일신규) / EOD미실현 / 종합. + 회전대금·트립당평균비용·balance(cash/eval/total/est_asset/pos).
5. **라운드트립 표**: 종목·명·수량·매수→매도·순손익·수익률·비용·전략·매도시각·비고(이월/run-up).
6. **전략별 표**: 전략·트립·승·실현·gross·비용·비고. (JSON per_strategy 인용)
7. **시간대별**: 매도시각 KST 버킷.
8. **진입 갭 분석**: 전일종가 대비 매수평단 갭%, 12%+ 표시(⚠️), 결과. 고갭 추격 손실 패턴 + "갭 자체보다 진입가 위치(눌림)" 교훈.
9. **이월/당일 분해** (해당 시): 전일 보유분 청산 손익 vs 당일 신규 손익.
10. **EOD 보유**: 종목·전략·수량·진입가(buy_audit)·종가·미실현(gross)·수익률.
11. **직전일 대비 표**: 최근 2~3일 실현·승률·비용/gross·데이터(실측/추정)·갭추격·이월. 반복패턴 명시.
12. **권고(우선순위)**: P0/P1/P2, 각 권고에 분류 (a)~(d) 태깅(recommendation-policy.md).
13. **푸터**: 실측/추정 출처 주석.

## md → html 변환기 (Python, 검증된 함수)

```python
import re, html as H
def mdspan(s):
    s=H.escape(s)
    s=re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s=re.sub(r'`(.+?)`', r'<code>\1</code>', s)
    return s
def md2html(m):
    out=[]; rows=m.split("\n"); i=0
    while i<len(rows):
        ln=rows[i]
        if ln.startswith("|"):
            cells=[c.strip() for c in ln.strip().strip("|").split("|")]
            if i+1<len(rows) and set(rows[i+1].replace("|","").replace(":","").replace("-","").strip())==set():
                out.append("<table><thead><tr>"+"".join(f"<th>{mdspan(c)}</th>" for c in cells)+"</tr></thead><tbody>")
                i+=2
                while i<len(rows) and rows[i].startswith("|"):
                    cs=[c.strip() for c in rows[i].strip().strip("|").split("|")]
                    out.append("<tr>"+"".join(f"<td>{mdspan(c)}</td>" for c in cs)+"</tr>"); i+=1
                out.append("</tbody></table>"); continue
        if ln.startswith("# "): out.append(f"<h1>{mdspan(ln[2:])}</h1>")
        elif ln.startswith("## "): out.append(f"<h2>{mdspan(ln[3:])}</h2>")
        elif ln.startswith("> "): out.append(f"<blockquote>{mdspan(ln[2:])}</blockquote>")
        elif ln.startswith("- "): out.append(f"<li>{mdspan(ln[2:])}</li>")
        elif ln.strip()=="---": out.append("<hr>")
        elif ln.strip()=="": out.append("")
        else: out.append(f"<p>{mdspan(ln)}</p>")
        i+=1
    return "\n".join(out)
```

HTML 스타일(헤더 색은 실측=녹색 #1a7f4b, 추정/경고일=적색 #c0392b 정도로 구분):
```css
body{font-family:-apple-system,'Apple SD Gothic Neo',Segoe UI,Roboto,sans-serif;max-width:980px;margin:0 auto;padding:24px;color:#1a1a1a;line-height:1.6}
h1{border-bottom:3px solid #1a7f4b;padding-bottom:8px}
h2{margin-top:1.8em;border-left:4px solid #2c3e50;padding-left:10px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px}
th,td{border:1px solid #ddd;padding:6px 9px;text-align:right}
th{background:#2c3e50;color:#fff;text-align:center}
td:first-child,th:first-child,td:nth-child(2){text-align:left}
tr:nth-child(even){background:#f7f9fb}
code{background:#eef1f3;padding:1px 5px;border-radius:3px;font-size:13px}
blockquote{color:#666;border-left:3px solid #ccc;margin:0;padding-left:12px;font-size:14px}
strong{color:#c0392b} li{margin:3px 0} hr{border:none;border-top:1px solid #ddd;margin:24px 0}
```

## 검증
- md→html 후 `<p>|` 가 없어야(표 파싱 성공), `<table>` 개수 ≥ 섹션 표 수.
- 전략별 합계 == JSON `total_realized`.
- 산출 후 `SendUserFile` 로 html(+md) 전달.
