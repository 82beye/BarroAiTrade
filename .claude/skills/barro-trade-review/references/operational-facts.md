# 운영 사실 (매번 재발견 금지)

## 경로
- 메인 레포(데이터·스크립트): `/Users/beye/workspace/BarroAiTrade` (이 개발머신). 운영 머신은 별도 원격 `/Users/beye/BarroAiTrade`(SSH 없음 — 직접 배포 불가).
- 데이터: `data/order_audit.csv`, `data/fill_audit.csv`(ka10073 브로커 실측 매도), `data/buy_audit.csv`(BAR-OPS-39 EOD 보유 매수평단), `data/balance_history.json`, `data/active_positions.json`, `data/_active_positions_history/`(장중 스냅샷, tranche 에 실측 filled_price), `data/ohlcv_cache/`(일봉), `data/ohlcv_cache_5m/`.
- data/ 는 **gitignore**(임포트해도 커밋 안 됨). `.claude/skills/` 도 gitignore(로컬).

## 거래비용 (`backend/core/trading_costs.py`)
- COMMISSION_RATE = 0.00175 (편도 0.175%, env `BARRO_COMMISSION_RATE`)
- TAX_RATE_SELL = 0.0020 (매도세 0.20%, ETF=0)
- 왕복 ≈ 0.55%. 비용이 gross 를 잠식하는 게 반복 주제(요율 협의가 구조적 1순위).

## 파일 스키마
- order_audit.csv: `ts(UTC ISO),action(ORDERED/FAILED/BLOCKED),side,symbol,qty,price(MKT),order_no,return_code,blocked,reason,strategy_id,filled_qty,avg_fill_price`. **ts 는 UTC** → KST=+9 (00:05Z=09:05). avg_fill_price 는 MKT라 보통 공란.
- fill_audit.csv: `date(YYYYMMDD),symbol,name,qty,buy_price,sell_price,pnl(순, 비용 차감후),pnl_rate,commission,tax`. **매도 실현만**(매수 독립감사는 buy_audit). pnl 은 net, 비용은 commission+tax 별도.
- buy_audit.csv: `date(YYYYMMDD),symbol,name,qty,avg_buy_price,source(kt00018)`. EOD 보유분만.
- balance_history.json: `[{date(YYYY-MM-DD),cash,eval_total,total,estimated_asset,position_count,updated_at(KST ISO)}]`. EOD 정산은 updated_at 시각 ≥14시.
- 일봉 ohlcv_cache/<sym>.json: `{"data":[{date(YYYYMMDD),open,high,low,close,volume}]}`. meta.json 에 `updated`.
- 5m ohlcv_cache_5m/<sym>.json: `{"data":[{datetime(YYYYMMDDHHMMSS),date,time,open,high,low,close,volume}]}` (또는 list). time 라벨=바 시작.

## 전략
- 단타 zone: `f_zone`, `sf_zone`, `gold_zone` (mean-rev/되돌림/눌림). `supertrend`(추세, 오버나잇 보유 가능 — 6/16 이월 +447K). `limit_up_chase`(상따, 보수 일일캡). `swing_38`(다일보유, 컷오프 예외).
- 갭가드 `_GAP_GUARD_STRATEGIES={gold_zone,f_zone}` — sf·supertrend 미적용(고갭 손실이 거기서 자주 남).

## 네이버 fchart 1분봉 (키움 인증 불필요 — 개발머신 보완용)
키움 네이티브 캔들이 rc=3 인증실패하면(개발머신 정상) 이걸로 진입가/갭/run-up 보완.
```python
import urllib.request, json, re, os
EXT=os.environ.get('CLAUDE_JOB_DIR','/tmp')+'/tmp/ext'; os.makedirs(EXT,exist_ok=True)
item_re=re.compile(r'data="([^"]+)"')   # YYYYMMDDHHMM|O|H|L|C|V (장전바는 OHLC null, close만)
def fetch(sym, count=2400):
    url=f'https://fchart.stock.naver.com/sise.nhn?symbol={sym}&timeframe=minute&count={count}&requestType=0'
    raw=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20).read().decode('euc-kr','ignore')
    rows=[dict(zip(('ts','open','high','low','close','volume'), m.split('|'))) for m in item_re.findall(raw) if len(m.split('|'))>=6]
    json.dump(rows, open(f'{EXT}/{sym}_minute.json','w')); return rows
```
체결가 추정: 주문 ts(KST) 의 1분봉 close (lag 0~+8분 → 못찾으면 -1~-20분). 시장가 슬리피지 미반영이라 추정은 실제보다 다소 낙관적. (검증: 2026-06-15 실측-추정 매수총액 편차 0.03%)

## _active_positions_history 활용 (NG 인 날 실측 매수가 복구)
fill_audit 가 없는 날도 `_active_positions_history/active_positions_<YYYYMMDD>T*.json` 의 tranche `filled_price`(order_no)로 **매수 레그는 실측** 가능(매도는 스냅샷에 없어 추정). 과거일 EOD 보유 판정은 그날 마지막 스냅샷(근사 — EOD 전 청산분 섞일 수 있음).

## 전문가 에이전트 (`~/.claude/agents/barrotrade-*.md`, 11종)
복기·권고에 활용(Phase 5 위임, recommendation-policy.md): **code-surgeon**(recap §5 권고→PolicyConfig/strategy dataclass 숫자 default 패치, AST검증·HITL강제·직접apply 금지), **self-reflector**(손절 오판패턴 추출), **intraday-reporter**(라이브 recap+§5 권고), **risk-manager**(ATR/VaR), trend-expert(EMA/ADX/MACD)·macro-specialist·debate-moderator·portfolio-pm·quick-decider·signal-watcher·controller. ⚠️ 이들은 `workspace/_intraday/`·`workspace/_memory/` 레이아웃 가정 — 이 머신엔 부재 가능 → 권고를 프롬프트에 직접 실어 위임(브리지) 후 실패 시 폴백. code-surgeon 타깃 PolicyConfig 은 BarroAiTrade 실코드에 실재.

## 무결성 도구
- `scripts/verify_eod_data.sh <date>` (= `verify_eod_data.py`): fill_audit·EOD balance·buy_audit 점검, exit=NG수. cron 권장(평일 16:10). `BARRO_DATA_DIR` 주입가능.
- `scripts/verify_deploy.sh`: 운영 머신 BAR-OPS-39 배포 검증.
- 반복 사건: 6/9~6/15 이브닝 파이프라인 침묵(미배포) → 6/16 복구(buy_audit 최초 생성=배포 정황).
