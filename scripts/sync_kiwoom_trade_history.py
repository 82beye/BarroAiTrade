#!/usr/bin/env python3
"""Build and verify a portable, read-only Kiwoom trade-history SQLite DB."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import SecretStr


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth  # noqa: E402
from backend.core.gateway.kiwoom_trade_history import (  # noqa: E402
    ALLOWED_BASE_URLS,
    KiwoomHistoryClient,
    TradeHistoryStore,
    account_fingerprint,
    build_manifest,
    create_snapshot,
    one_year_ago,
    parse_yyyymmdd,
    sync_history,
    verify_database,
)


DEFAULT_DB = REPO_ROOT / "data" / "kiwoom_trade_history_1y.db"
DEFAULT_ENV = REPO_ROOT / ".env.local"
KST = ZoneInfo("Asia/Seoul")


class _SyncFileLock:
    def __init__(self, db_path: Path) -> None:
        self.path = db_path.with_suffix(db_path.suffix + ".sync.lock")
        self.fd: int | None = None

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise SystemExit(f"another history sync is using {self.path}") from exc
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            import fcntl

            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _identity_key(db_path: Path) -> str:
    """Create a stable local HMAC key that is not copied inside the research DB."""
    path = db_path.parent / ".kiwoom_history_identity_key"
    if path.exists():
        value = path.read_text(encoding="ascii").strip()
        if len(value) < 32:
            raise SystemExit(f"identity key is invalid: {path}")
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text(encoding="ascii").strip()
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value + "\n")
    return value


def _previous_weekday(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _last_completed_trade_day(now: datetime | None = None) -> date:
    current = now or datetime.now(KST)
    today = current.date()
    if today.weekday() >= 5:
        while today.weekday() >= 5:
            today -= timedelta(days=1)
        return today
    if current.timetz().replace(tzinfo=None) >= time(16, 0):
        return today
    return _previous_weekday(today)


def _last_weekday_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _date_arg(value: str) -> date:
    compact = value.replace("-", "")
    try:
        return parse_yyyymmdd(compact)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _credentials(env_file: Path) -> tuple[str, str, str, str]:
    if not env_file.is_file():
        raise SystemExit(f"environment file not found: {env_file}")
    load_dotenv(env_file, override=True)
    app_key = os.environ.get("KIWOOM_APP_KEY", "").strip()
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "").strip()
    account_no = os.environ.get("KIWOOM_ACCOUNT_NO", "").strip()
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com").rstrip("/")
    if not app_key or not app_secret:
        raise SystemExit("KIWOOM_APP_KEY and KIWOOM_APP_SECRET are required")
    if base_url not in ALLOWED_BASE_URLS:
        allowed = ", ".join(sorted(ALLOWED_BASE_URLS))
        raise SystemExit(f"KIWOOM_BASE_URL must be an official endpoint: {allowed}")
    return app_key, app_secret, account_no, base_url


async def _run_sync(args: argparse.Namespace) -> int:
    now_kst = datetime.now(KST)
    if (
        not args.allow_market_hours
        and now_kst.weekday() < 5
        and time(8, 30) <= now_kst.time().replace(tzinfo=None) < time(16, 0)
    ):
        raise SystemExit(
            "history sync is disabled during 08:30-16:00 KST; "
            "use --allow-market-hours only after checking production API load"
        )
    env_path = args.env_file.expanduser().resolve()
    app_key, app_secret, account_no, base_url = _credentials(env_path)
    environment = ALLOWED_BASE_URLS[base_url]
    minimum_rate = 1.05 if environment == "mock" else 0.25
    if args.rate_limit < minimum_rate:
        raise SystemExit(
            f"--rate-limit must be at least {minimum_rate:.2f}s for {environment}"
        )
    db_path = args.db.expanduser().resolve()
    account_id = account_fingerprint(
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
        account_no=account_no,
        identity_key=_identity_key(db_path),
    )

    last_completed = _last_completed_trade_day()
    end = args.end_date or last_completed
    start = args.start_date or one_year_ago(end)
    if end < start:
        raise SystemExit("--to must be on or after --from")
    if _last_weekday_on_or_before(end) > last_completed:
        raise SystemExit(
            f"--to includes an incomplete/future trading day; last completed day is {last_completed}"
        )

    with _SyncFileLock(db_path):
        with TradeHistoryStore(db_path) as store:
            if args.incremental:
                last_status = store.last_run_status(account_id)
                if last_status not in (None, "SUCCEEDED"):
                    raise SystemExit(
                        "latest sync did not succeed; rerun the failed full range before incremental sync"
                    )
                latest = store.latest_successful_day(account_id)
                if latest is not None:
                    start = max(start, latest - timedelta(days=args.overlap_days))
            oauth = KiwoomNativeOAuth(
                app_key=SecretStr(app_key),
                app_secret=SecretStr(app_secret),
                base_url=base_url,
                use_shared_cache=True,
            )
            async with KiwoomHistoryClient(
                oauth,
                rate_limit_seconds=args.rate_limit,
            ) as client:
                run_id = await sync_history(
                    store=store,
                    client=client,
                    account_id=account_id,
                    environment=environment,
                    start=start,
                    end=end,
                    alias=args.account_alias,
                    progress_every=args.progress_every,
                )

    verification = verify_database(db_path, expected_environment=environment)
    manifest = build_manifest(verification)
    manifest["sync_run_id"] = run_id
    manifest_path = args.manifest or db_path.with_suffix(".manifest.json")
    _write_manifest(manifest_path.expanduser().resolve(), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    if not verification.ok:
        print("database verification failed", file=sys.stderr)
        return 2
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    result = verify_database(args.db, expected_environment=args.expect_environment)
    print(json.dumps(build_manifest(result), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 2


def _run_snapshot(args: argparse.Namespace) -> int:
    if args.expect_environment:
        source_check = verify_database(
            args.db, expected_environment=args.expect_environment
        )
        if not source_check.ok:
            print(json.dumps(build_manifest(source_check), ensure_ascii=False, indent=2))
            return 2
    result = create_snapshot(args.db, args.output)
    manifest = build_manifest(result)
    manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
    _write_manifest(manifest_path.expanduser().resolve(), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "키움 REST 계좌 조회 전용 DB 도구. 주문/정정/취소 API는 포함하지 않습니다."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="체결·실현손익을 SQLite로 동기화")
    sync_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sync_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    sync_parser.add_argument("--from", dest="start_date", type=_date_arg)
    sync_parser.add_argument("--to", dest="end_date", type=_date_arg)
    sync_parser.add_argument(
        "--incremental",
        action="store_true",
        help="기존 마지막 성공일에서 overlap만큼 되돌아가 증분 조회",
    )
    sync_parser.add_argument("--overlap-days", type=int, default=7)
    sync_parser.add_argument("--account-alias", default="primary")
    sync_parser.add_argument("--rate-limit", type=float, default=1.05)
    sync_parser.add_argument("--progress-every", type=int, default=10)
    sync_parser.add_argument(
        "--allow-market-hours",
        action="store_true",
        help="운영 API 부하를 확인한 경우에만 08:30-16:00 KST 실행 허용",
    )

    verify_parser = subparsers.add_parser("verify", help="DB 무결성·해시·행 수 검증")
    verify_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    verify_parser.add_argument("--expect-environment", choices=("real", "mock"))

    snapshot_parser = subparsers.add_parser(
        "snapshot", help="실행 중 DB도 안전하게 단일 파일 스냅숏으로 복사"
    )
    snapshot_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    snapshot_parser.add_argument("--output", type=Path, required=True)
    snapshot_parser.add_argument("--manifest", type=Path)
    snapshot_parser.add_argument("--expect-environment", choices=("real", "mock"))

    sync_parser.add_argument("--manifest", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "sync":
        if args.overlap_days < 0:
            raise SystemExit("--overlap-days cannot be negative")
        if args.progress_every < 1:
            raise SystemExit("--progress-every must be positive")
        return asyncio.run(_run_sync(args))
    if args.command == "verify":
        return _run_verify(args)
    if args.command == "snapshot":
        return _run_snapshot(args)
    raise AssertionError(f"unexpected command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
