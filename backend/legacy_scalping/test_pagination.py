"""
키움 REST API 페이징 테스트
mockapi vs 실 API에서 일봉 데이터 페이징이 작동하는지 확인
"""

import asyncio
import os
import json
import yaml
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

with open("config/settings.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")
TEST_CODE = "005930"  # 삼성전자
TARGET_COUNT = 100     # 100일치 요청 (페이징 테스트)


async def get_token(client: httpx.AsyncClient, base_url: str) -> str:
    """토큰 발급 (디버그 포함)"""
    resp = await client.post(
        f"{base_url}/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "secretkey": APP_SECRET,
        },
    )
    resp.raise_for_status()
    body = resp.json()

    # 토큰 필드 탐색 (API마다 다를 수 있음)
    token = body.get("token") or body.get("access_token") or body.get("Token") or ""
    print(f"  토큰 응답 키: {list(body.keys())}")
    print(f"  토큰 길이: {len(token)}자")
    if not token:
        # 전체 응답 출력 (값은 마스킹)
        masked = {k: (v[:8] + "..." if isinstance(v, str) and len(v) > 8 else v) for k, v in body.items()}
        print(f"  토큰 응답 (마스킹): {json.dumps(masked, ensure_ascii=False)}")

    return token


async def test_pagination(base_url: str, label: str):
    """일봉 API 페이징 테스트"""
    print(f"\n{'='*60}")
    print(f"[{label}] {base_url}")
    print(f"종목: {TEST_CODE} (삼성전자), 목표: {TARGET_COUNT}일")
    print(f"{'='*60}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. 토큰 발급
        try:
            token = await get_token(client, base_url)
            if not token:
                print("  토큰이 빈 값 — API 인증 실패")
                return
            print(f"  토큰 발급: OK")
        except Exception as e:
            print(f"  토큰 발급 실패: {e}")
            return

        # 2. 일봉 데이터 페이징 테스트
        all_items = []
        cont_yn = "N"
        next_key = ""
        page = 0

        while len(all_items) < TARGET_COUNT:
            page += 1
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "api-id": "ka10005",
                "cont-yn": cont_yn,
            }
            if next_key:
                headers["next-key"] = next_key

            try:
                resp = await client.post(
                    f"{base_url}/api/dostk/mrkcond",
                    headers=headers,
                    json={"stk_cd": TEST_CODE},
                )

                if resp.status_code == 429:
                    print(f"  Page {page}: 429 Rate Limit — 3초 대기 후 재시도")
                    await asyncio.sleep(3)
                    continue

                if resp.status_code != 200:
                    print(f"  Page {page}: HTTP {resp.status_code}")
                    print(f"    응답: {resp.text[:300]}")
                    break

                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"  Page {page}: HTTP 에러 {e.response.status_code}")
                print(f"    응답: {e.response.text[:300]}")
                break
            except Exception as e:
                print(f"  Page {page}: 에러 — {e}")
                break

            result = resp.json()
            items = result.get("stk_ddwkmm", [])
            resp_cont_yn = resp.headers.get("cont-yn", "N")
            resp_next_key = resp.headers.get("next-key", "")

            # 반환 코드 체크
            return_code = result.get("return_code", "0")
            return_msg = result.get("return_msg", "")

            if items:
                first_date = items[0].get("date", "?")
                last_date = items[-1].get("date", "?")
            else:
                first_date = last_date = "-"

            print(
                f"  Page {page}: {len(items)}건 "
                f"({first_date} ~ {last_date}) | "
                f"cont-yn={resp_cont_yn} | "
                f"next-key={'있음(' + resp_next_key[:20] + '...)' if resp_next_key else '없음'}"
                f"{f' | code={return_code} msg={return_msg}' if str(return_code) != '0' else ''}"
            )

            if not items:
                print(f"  → 데이터 없음, 중단")
                break

            all_items.extend(items)

            # 페이징 계속?
            if resp_cont_yn == "Y" and resp_next_key:
                cont_yn = "Y"
                next_key = resp_next_key
                await asyncio.sleep(0.5)  # rate limit
            else:
                print(f"  → 페이징 종료 (cont-yn={resp_cont_yn})")
                break

        # 3. 결과 요약
        print(f"\n결과 요약:")
        print(f"  총 페이지: {page}")
        print(f"  총 레코드: {len(all_items)}")
        if all_items:
            dates = [item.get("date", "") for item in all_items]
            dates_sorted = sorted(dates)
            print(f"  날짜 범위: {dates_sorted[0]} ~ {dates_sorted[-1]}")
            unique_dates = len(set(dates))
            print(f"  고유 날짜: {unique_dates}")
        pagination_works = page > 1 and len(all_items) > 30
        print(f"  페이징 작동: {'YES ✓' if pagination_works else 'NO (30일 제한)'}")


async def main():
    print("키움 REST API 페이징 테스트")
    print(f"APP_KEY: {'설정됨 (' + APP_KEY[:4] + '...)' if APP_KEY else '미설정'}")
    print(f"APP_SECRET: {'설정됨' if APP_SECRET else '미설정'}")

    if not APP_KEY or not APP_SECRET:
        print("ERROR: KIWOOM_APP_KEY / KIWOOM_APP_SECRET 미설정")
        return

    # mockapi 테스트
    await test_pagination("https://mockapi.kiwoom.com", "모의투자 (mockapi)")

    # 실 API 테스트
    await test_pagination("https://api.kiwoom.com", "실거래 (api)")


if __name__ == "__main__":
    asyncio.run(main())
