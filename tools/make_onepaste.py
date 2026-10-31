#!/usr/bin/env python3
"""把 make_kit.py 的分片脚本合并成“最少粘贴次数”的安装脚本。

用法: python make_onepaste.py --kit DIR --ext gz|xz
产物: paste_XXX_install.sh —— 每片一个；最后一片自动附带校验+解包，粘完即装好
"""
import argparse, glob, hashlib, os, re

ap = argparse.ArgumentParser()
ap.add_argument("--kit", required=True)
ap.add_argument("--ext", choices=("gz", "xz"), required=True)
a = ap.parse_args()

kit = os.path.abspath(a.kit)
parts = sorted(glob.glob(os.path.join(kit, "paste_[0-9][0-9][0-9].sh")))
assert parts, "no paste_*.sh"
blob = open(os.path.join(kit, "kit.tar." + a.ext), "rb").read()
blob_sha = hashlib.sha256(blob).hexdigest()
meta = open(os.path.join(kit, "MANIFEST.txt"), encoding="utf-8").read()
ver = re.search(r"version:\s*v?([0-9][^\s]*)", meta).group(1)
target = "optimizer-" + ver
tarflag = "J" if a.ext == "xz" else "z"

shas = [hashlib.sha256(open(p.replace("paste_", "raw_").replace(".sh", ".b64"), "rb").read()).hexdigest()
        for p in parts]

tail = ["echo '%s  p%03d.b64' | sha256sum -c -" % (h, i) for i, h in enumerate(shas, 1)]
tail += [
    "cat p*.b64 | tr -d '\\r\\n ' | base64 -d > kit_dl.tar.%s" % a.ext,
    "echo '%s  kit_dl.tar.%s' | sha256sum -c -" % (blob_sha, a.ext),
    "mkdir -p %s" % target,
    "tar -x%sf kit_dl.tar.%s -C %s" % (tarflag, a.ext, target),
    "echo \"OK $(find %s -type f | wc -l) files -> %s\"" % (target, target),
]
tail_txt = ("\n".join(tail) + "\n").encode()

for i, p in enumerate(parts, 1):
    body = open(p, "rb").read()
    if i == len(parts):
        body += tail_txt
    out = p[:-3] + "_install.sh"
    open(out, "wb").write(body)
    print("%s  %d bytes" % (os.path.basename(out), len(body)))
