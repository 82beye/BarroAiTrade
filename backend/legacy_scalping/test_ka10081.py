#!/usr/bin/env python3
"""
ka10081 (주식일봉차트조회) API 탐색 스크립트

mockapi에서 ka10081이 작동하는지 5가지 조합을 테스트한 후,
성공 시 페이징(최대 5페이지)까지 확인한다.

사용법:
    python3 test_ka10081.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 프로젝트 루트
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import yaml
from dotenv import load_dotenv

from execution.kiwoom_api import KiwoomRestAPI


def load_config() -> dict:
    env_path = ROOT / "config" / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    with open(ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["mode"] = "simulation"
    return config


def pp(obj, label: str = ""):
    """Pretty-print JSON (축약)"""
    if label:
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
    if obj is None:
        print("  (None)")
        return
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    lines = text.splitlines()
    if len(lines) > 40:
        print("\n".join(lines[:30]))
        print(f"  ... ({len(lines) - 30} lines truncated)")
    else:
        print(text)


# ─────────────────────────────────────────────────────────────────────────────
# 테스트 케이스 정의
# ─────────────────────────────────────────────────────────────────────────────

TEST_CASES = [
    {
        "label": "#1 /api/dostk/chart + 005930 + base_dt=20260305 + upd_stkpc_tp=1",
        "endpoint": "/api/dostk/chart",
        "api_id": "ka10081",
        "data": {"stk_cd": "005930", "base_dt": "20260305", "upd_stkpc_tp": "1"},
    },
    {
        "label": "#2 /api/dostk/chart + KRX:005930",
        "endpoint": "/api/dostk/chart",
        "api_id": "ka10081",
        "data": {"stk_cd": "KRX:005930", "base_dt": "20260305", "upd_stkpc_tp": "1"},
    },
    {
        "label": "#3 /api/dostk/chart + upd_stkpc_tp=0",
        "endpoint": "/api/dostk/chart",
        "api_id": "ka10081",
        "data": {"stk_cd": "005930", "base_dt": "20260305", "upd_stkpc_tp": "0"},
    },
    {
        "label": "#4 /api/dostk/mrkcond + ka10081 api-id",
        "endpoint": "/api/dostk/mrkcond",
        "api_id": "ka10081",
        "data": {"stk_cd": "005930", "base_dt": "20260305", "upd_stkpc_tp": "1"},
    },
    {
        "label": "#5 /api/dostk/chart + 최소 파라미터 (stk_cd만)",
        "endpoint": "/api/dostk/chart",
        "api_id": "ka10081",
        "data": {"stk_cd": "005930"},
    },
]


async def run_test_case(api: KiwoomRestAPI, tc: dict):
    """단일 테스트 실행. 성공 시 응답 dict, 실패 시 None."""
    pp(None, tc["label"])
    try:
        result = await api._post(
            tc["endpoint"],
            api_id=tc["api_id"],
            data=tc["data"],
        )
        # 기본 정보 출력
        items_key = None
        for key in ["stk_dt_pole_chart_qry", "output", "list", "stk_ddwkmm", "data"]:
            if key in result and isinstance(result[key], list):
                items_key = key
                break

        if items_key:
            items = result[items_key]
            print(f"  ✅ 성공! items_key='{items_key}', count={len(items)}")
            print(f"  cont-yn={result.get('_cont_yn')}, next-key={result.get('_next_key', '')[:40]}")
            if items:
                print(f"  첫 레코드 키: {list(items[0].keys())}")
                pp(items[0], "첫 레코드")
                if len(items) > 1:
                    pp(items[-1], "마지막 레코드")
        else:
            # list 형태가 아닐 수도 있음 — 전체 키 출력
            print(f"  ⚠️  리스트 키 못 찾음. 응답 키: {list(result.keys())}")
            pp(result, "전체 응답")

        return result

    except Exception as e:
        print(f"  ❌ 실패: {type(e).__name__}: {e}")
        return None


async def run_paging_test(api: KiwoomRestAPI, success_tc: dict, success_result: dict):
    """성공한 케이스로 페이징 테스트 (최대 5페이지)"""
    pp(None, f"페이징 테스트: {success_tc['label']}")

    # items 키 찾기
    items_key = None
    for key in ["output", "list", "stk_ddwkmm", "data"]:
        if key in success_result and isinstance(success_result[key], list):
            items_key = key
            break

    if not items_key:
        print("  리스트 키를 찾을 수 없어 페이징 테스트 불가")
        return

    all_items = list(success_result[items_key])
    cont_yn = success_result.get("_cont_yn", "N")
    next_key = success_result.get("_next_key", "")

    print(f"  1페이지: {len(all_items)}건 (cont={cont_yn})")

    page = 1
    max_pages = 5

    while cont_yn == "Y" and next_key and page < max_pages:
        page += 1
        try:
            result = await api._post(
                success_tc["endpoint"],
                api_id=success_tc["api_id"],
                data=success_tc["data"],
                cont_yn="Y",
                next_key=next_key,
            )
            items = result.get(items_key, [])
            cont_yn = result.get("_cont_yn", "N")
            next_key = result.get("_next_key", "")
            all_items.extend(items)
            print(f"  {page}페이지: {len(items)}건 (누적: {len(all_items)}, cont={cont_yn})")
        except Exception as e:
            print(f"  {page}페이지 실패: {e}")
            break

    print(f"\n  총 {page}페이지, {len(all_items)}건 수집")

    if all_items:
        # 날짜 범위 확인
        date_keys = ["date", "stdr_dt", "stck_bsop_date", "dt"]
        date_field = None
        for dk in date_keys:
            if dk in all_items[0]:
                date_field = dk
                break
        if date_field:
            dates = [item[date_field] for item in all_items if item.get(date_field)]
            if dates:
                print(f"  날짜 범위: {dates[-1]} ~ {dates[0]} (필드: {date_field})")

    # 필드 매핑 힌트 출력
    if all_items:
        print(f"\n  === 필드 매핑 참고 ===")
        sample = all_items[0]
        for k, v in sample.items():
            print(f"    {k}: {v}")


async def main():
    config = load_config()
    api = KiwoomRestAPI(config)

    try:
        await api.initialize()
        print(f"API 초기화 완료 (base_url: {api.base_url})")

        # ── 5개 테스트 실행 ──
        first_success_tc = None
        first_success_result = None

        for tc in TEST_CASES:
            result = await run_test_case(api, tc)
            if result is not None and first_success_tc is None:
                # 리스트 데이터가 있는 경우만 성공으로 간주
                for key in ["stk_dt_pole_chart_qry", "output", "list", "stk_ddwkmm", "data"]:
                    if key in result and isinstance(result[key], list) and len(result[key]) > 0:
                        first_success_tc = tc
                        first_success_result = result
                        break

        # ── 페이징 테스트 ──
        if first_success_tc:
            await run_paging_test(api, first_success_tc, first_success_result)
        else:
            print("\n" + "=" * 60)
            print("  ⚠️  모든 테스트 실패 — ka10081 미지원 또는 파라미터 오류")
            print("  → ka10005 폴백 전략 사용 필요")
            print("=" * 60)

    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
