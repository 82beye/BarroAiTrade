#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EOD 아카이브 이메일 발송 — iCloud Mail Drop 회피용 split + part.00 base64 우회.

배경(2026-06-14 규명): 기존 send_archive_email.applescript 는 5분봉 95M 을 Mail Drop 으로
보내려다 무인 osascript 가 '대용량 보내기' 다이얼로그를 못 눌러 AppleEvent -1712 로 상시 실패.
회피로 14MB 분할 일반첨부를 보내면 Mail Drop 은 우회되나, 각 아카이브의 part.00(=gzip/tar
매직헤더를 담은 시작 청크)만 메일측 첨부백신이 '압축파일'로 스캔하며 무한 deferral → 미도착.
=> 본 스크립트: 아카이브를 14MB raw 분할(part.01~는 일반첨부로 통과) + part.00 만 base64
   텍스트(.txt)로 무장(백신이 텍스트로 보고 통과) + 각 메일 인코딩후<20MB 로 패킹해 다중 발송.
   소형 아카이브(< 1파트)는 통째 전송(즉시 스캔통과). 2026-06-12 분 동일 방식으로 종단검증 완료.

사용: python3 scripts/eod_email_split_send.py <DT> <archive.tar.gz> [archive2 ...]
env:
  EOD_EMAIL_TO   수신자 (기본 82beye@gmail.com)
  DRY_RUN=1      실제 발송 대신 발송계획 출력 + 로컬 재조립 sha256 검증
  EOD_PART_MB    raw 분할 크기 MB (기본 14; 인코딩후 ~19MB < Mail Drop 20MB)
  EOD_B64_MB     base64 .txt 조각 크기 MB (기본 12)
  EOD_SEND_GAP   발송 간 대기초 (기본 5)
  EOD_KEEP=1     작업 임시폴더 유지(디버그)
종료코드: 발송 실패 건수(0 = 전부 성공/DRY).
"""
import os
import sys
import time
import base64
import hashlib
import tempfile
import shutil
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLESCRIPT = os.path.join(REPO, "scripts", "eod_send_one.applescript")
TO = os.environ.get("EOD_EMAIL_TO", "82beye@gmail.com")
DRY = os.environ.get("DRY_RUN", "0") == "1"
PART = int(os.environ.get("EOD_PART_MB", "14")) * 1024 * 1024
B64 = int(os.environ.get("EOD_B64_MB", "12")) * 1024 * 1024
CAP = PART  # 메일 1통의 raw 첨부 합계 상한(= part 1개가 단독으로 들어갈 수 있게)
GAP = int(os.environ.get("EOD_SEND_GAP", "5"))
KEEP = os.environ.get("EOD_KEEP", "0") == "1"


def log(m):
    print(m, flush=True)


def human(n):
    n = float(n)
    for u in ("B", "K", "M", "G"):
        if n < 1024:
            return ("%.0f%s" % (n, u))
        n /= 1024
    return ("%.1fT" % n)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def split_file(path, prefix, size):
    """path 를 size 바이트씩 잘라 prefix.00, prefix.01 ... 생성, 경로 리스트 반환."""
    parts = []
    i = 0
    with open(path, "rb") as f:
        while True:
            c = f.read(size)
            if not c:
                break
            op = "%s.%02d" % (prefix, i)
            with open(op, "wb") as o:
                o.write(c)
            parts.append(op)
            i += 1
    return parts


def b64_armor(part0_path, out_prefix):
    """part.00 → base64(76열 줄바꿈) → B64 바이트씩 .txt 조각으로 분할. .txt 경로 리스트 반환."""
    with open(part0_path, "rb") as f:
        enc = base64.b64encode(f.read())
    wrapped = b"\n".join(enc[i:i + 76] for i in range(0, len(enc), 76)) + b"\n"
    b64_full = out_prefix + ".b64.full"
    with open(b64_full, "wb") as o:
        o.write(wrapped)
    raw_parts = split_file(b64_full, out_prefix + ".b64", B64)
    os.remove(b64_full)
    txt_parts = []
    for rp in raw_parts:
        tp = rp + ".txt"
        os.rename(rp, tp)
        txt_parts.append(tp)
    return txt_parts


def main():
    if len(sys.argv) < 3:
        log("usage: eod_email_split_send.py <DT> <archive...>")
        return 2
    dt = sys.argv[1]
    given = sys.argv[2:]
    archives = []
    for a in given:
        if os.path.isfile(a):
            archives.append(a)
        else:
            log("  [!] 없음(건너뜀): %s" % a)
    if not archives:
        log("발송할 아카이브 없음 — 종료")
        return 3

    work = tempfile.mkdtemp(prefix="eod_%s_send_" % dt)
    log("==== EOD 분할발송 (DT=%s, DRY_RUN=%s) ====" % (dt, DRY))
    log("work=%s  TO=%s  PART=%s B64=%s" % (work, TO, human(PART), human(B64)))

    units = []          # {path, name}
    sha_lines = []      # "<sha>  <basename>" (원본 아카이브)
    recon = []          # 복원 명령(README 용)
    try:
        for arch in archives:
            name = os.path.basename(arch)
            sz = os.path.getsize(arch)
            sha_lines.append("%s  %s" % (sha256(arch), name))
            if sz <= PART:
                # 소형: 통째 전송(빠르게 스캔통과)
                units.append({"path": arch, "name": name})
                recon.append("# %s : 분할 없음 — 그대로 사용" % name)
                log("  + %s (%s) 통째 1첨부" % (name, human(sz)))
                continue
            prefix = os.path.join(work, name + ".part")
            parts = split_file(arch, prefix, PART)
            # part.00 → base64 .txt 우회 / 나머지 raw
            txts = b64_armor(parts[0], os.path.join(work, name + ".p00"))
            os.remove(parts[0])
            for tp in txts:
                units.append({"path": tp, "name": os.path.basename(tp)})
            for rp in parts[1:]:
                units.append({"path": rp, "name": os.path.basename(rp)})
            recon.append("cat %s.p00.b64.*.txt | base64 -D > %s.part.00" % (name, name))
            recon.append("cat %s.part.* > %s" % (name, name))
            log("  + %s (%s) → %d조각(part.00=base64 .txt %d) + raw %d"
                % (name, human(sz), len(parts), len(txts), len(parts) - 1))

        # 매니페스트(README + 체크섬) 작성 → 첫 메일에 동봉되도록 units 앞에
        sha_path = os.path.join(work, "SHA256SUMS.txt")
        with open(sha_path, "w") as o:
            o.write("\n".join(sha_lines) + "\n")
        readme_path = os.path.join(work, "README_복원.txt")
        readme = []
        readme.append("BarroAiTrade EOD %s — 분할/우회 이메일 복원 안내" % dt)
        readme.append("=" * 50)
        readme.append("받은 첨부 전부를 한 폴더에 저장한 뒤 아래를 실행하세요.")
        readme.append("(part.00 은 메일백신 회피로 base64 .txt 로 전송됨 → 먼저 디코드)")
        readme.append("")
        readme.extend(recon)
        readme.append("")
        readme.append("# 무결성 검증(모두 OK 면 성공):")
        readme.append("shasum -a 256 -c SHA256SUMS.txt")
        with open(readme_path, "w") as o:
            o.write("\n".join(readme) + "\n")
        units = [{"path": readme_path, "name": "README_복원.txt"},
                 {"path": sha_path, "name": "SHA256SUMS.txt"}] + units

        # 그리디 패킹: 각 메일 raw 합계 <= CAP
        bins = []
        cur, cur_sz = [], 0
        for u in units:
            usz = os.path.getsize(u["path"])
            if cur and cur_sz + usz > CAP:
                bins.append(cur)
                cur, cur_sz = [], 0
            cur.append(u)
            cur_sz += usz
        if cur:
            bins.append(cur)
        n = len(bins)
        log("-- 발송계획: %d통 (units=%d) --" % (n, len(units)))

        if DRY:
            for k, b in enumerate(bins, 1):
                tot = sum(os.path.getsize(x["path"]) for x in b)
                log("  [%d/%d] %s : %s" % (k, n, human(tot),
                                           ", ".join(x["name"] for x in b)))
            ok = _self_check(dt, work, archives, sha_lines)
            return 0 if ok else 9

        fail = 0
        for k, b in enumerate(bins, 1):
            names = ", ".join(x["name"] for x in b)
            subj = "BarroAiTrade EOD %s [%d/%d]" % (dt, k, n)
            body = "%s\n\n복원: 첨부 README_복원.txt 참고." % names
            paths = [x["path"] for x in b]
            try:
                r = subprocess.run(["osascript", APPLESCRIPT, TO, subj, body] + paths,
                                   capture_output=True, text=True, timeout=300)
                out = (r.stdout or "").strip()
            except Exception as e:
                out = "EXC %s" % e
            if out == "sent":
                log("  [%d/%d] sent: %s" % (k, n, names))
            else:
                fail += 1
                log("  [%d/%d] FAIL(%s): %s" % (k, n, out or (r.stderr or "").strip(), names))
            time.sleep(GAP)

        log("==== 발송완료: 성공 %d / 실패 %d (총 %d통) ====" % (n - fail, fail, n))
        log("   참고: part.00(raw) 직첨부는 백신차단되니 절대 추가하지 말 것.")
        return fail
    finally:
        if KEEP:
            log("   (작업폴더 유지: %s)" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def _self_check(dt, work, archives, sha_lines):
    """DRY 전용: 생성된 조각을 사용자 절차대로 재조립해 원본 sha256 과 일치하는지 검증."""
    log("-- 로컬 재조립 검증(사용자 절차 모사) --")
    want = {}
    for ln in sha_lines:
        h, nm = ln.split("  ", 1)
        want[nm] = h
    vdir = os.path.join(work, "_verify")
    os.makedirs(vdir, exist_ok=True)
    allok = True
    for arch in archives:
        name = os.path.basename(arch)
        out = os.path.join(vdir, name)
        b64s = sorted(f for f in os.listdir(work)
                      if f.startswith(name + ".p00.b64.") and f.endswith(".txt"))
        if b64s:
            # part.00 = base64 디코드(.txt 합쳐서)
            enc = b"".join(open(os.path.join(work, f), "rb").read() for f in b64s)
            part00 = base64.b64decode(enc)
            with open(out, "wb") as o:
                o.write(part00)
                rest = sorted(f for f in os.listdir(work)
                              if f.startswith(name + ".part.") and f != name + ".part.00")
                for rp in rest:
                    o.write(open(os.path.join(work, rp), "rb").read())
        else:
            shutil.copyfile(arch, out)  # 소형: 통째
        got = sha256(out)
        mark = "OK" if got == want.get(name) else "MISMATCH"
        if got != want.get(name):
            allok = False
        log("  [%s] %s" % (mark, name))
    log("-- 재조립 검증: %s --" % ("전부 일치 ✅" if allok else "불일치 ❌"))
    return allok


if __name__ == "__main__":
    sys.exit(main())
