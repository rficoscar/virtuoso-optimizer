#!/usr/bin/env python3
"""校验 kit 目录：安装脚本为纯 ASCII、单片不超过上限、分片/整包 sha 与 MANIFEST 一致。

用法: python verify_kit.py KIT_DIR [KIT_DIR ...] [--limit 240000]
"""
import argparse, glob, hashlib, os, re, sys

ap = argparse.ArgumentParser()
ap.add_argument("kits", nargs="+")
ap.add_argument("--limit", type=int, default=240000)
a = ap.parse_args()

bad = 0
for kit in a.kits:
    kit = os.path.abspath(kit)
    man = open(os.path.join(kit, "MANIFEST.txt"), encoding="utf-8").read()
    blob_sha = re.search(r"blob sha256:\s*([0-9a-f]{64})", man).group(1)
    ext = "xz" if os.path.exists(os.path.join(kit, "kit.tar.xz")) else "gz"
    blob = open(os.path.join(kit, "kit.tar." + ext), "rb").read()
    ok = hashlib.sha256(blob).hexdigest() == blob_sha
    print("%-28s blob:%s %d B" % (os.path.basename(kit), "OK" if ok else "BAD", len(blob)))
    bad += 0 if ok else 1

    for p in sorted(glob.glob(os.path.join(kit, "*install*.sh"))):
        d = open(p, "rb").read()
        ascii_ok = all(b < 128 for b in d)
        size_ok = len(d) <= a.limit
        print("  %-28s %7d B  ascii:%s  <=%d:%s" % (
            os.path.basename(p), len(d), "OK" if ascii_ok else "BAD",
            a.limit, "OK" if size_ok else "BAD"))
        bad += 0 if (ascii_ok and size_ok) else 1
        for m in re.finditer(rb"echo '([0-9a-f]{64})  (p\d{3})\.b64'", d):
            raw = os.path.join(kit, "raw_" + m.group(2).decode()[1:] + ".b64")
            real = hashlib.sha256(open(raw, "rb").read()).hexdigest().encode()
            same = real == m.group(1)
            print("    %s sha:%s" % (m.group(2).decode(), "OK" if same else "BAD"))
            bad += 0 if same else 1

print("PASS" if not bad else "FAIL %d" % bad)
sys.exit(1 if bad else 0)
