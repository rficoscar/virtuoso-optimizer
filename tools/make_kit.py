#!/usr/bin/env python3
"""把仓库打包成可经剪贴板/共享目录传输的 base64 分片。

用法:
  python make_kit.py [--repo DIR] [--out DIR] [--limit 20000] [--format xz|gz] [--probes]

产物:
  kit.tar.xz / kit.tar.gz     完整压缩包
  paste_001.sh ...            每片一个剪贴板块（heredoc，直接粘贴即可）
  unpack.sh                   服务器端重建脚本（粘贴一次）
  MANIFEST.txt                分片校验和与重建命令
  probes/probe_08k.sh ...     粘贴上限标定用
"""
import argparse, base64, fnmatch, gzip, hashlib, io, lzma, os, subprocess, sys, tarfile

CLIP2FILE = '''import os, sys, tkinter as tk
os.environ.setdefault("DISPLAY", ":0")
r = tk.Tk(); r.withdraw()
data = r.clipboard_get()
out = sys.argv[1] if len(sys.argv) > 1 else "clip.b64"
with open(out, "w", encoding="utf-8", newline="\\n") as f:
    f.write(data)
print("wrote %s %d chars" % (out, len(data)))
r.destroy()
'''

def sh(cmd, cwd=None):
    return subprocess.check_output(cmd, cwd=cwd)

def repo_files(repo):
    out = sh(["git", "-C", repo, "ls-files", "-z"])
    return [p.decode("utf-8") for p in out.split(b"\0") if p]

def version_of(repo):
    try:
        return sh(["git", "-C", repo, "describe", "--tags", "--always"], cwd=repo).decode().strip()
    except Exception:
        return "unknown"

def build_tar(repo, files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for rel in files:
            data = open(os.path.join(repo, rel), "rb").read()
            ti = tarfile.TarInfo(rel)
            ti.size = len(data); ti.mtime = 1767225600; ti.mode = 0o644
            ti.uid = ti.gid = 0; ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()

def compress(raw, fmt):
    if fmt == "xz":
        return lzma.compress(raw, format=lzma.FORMAT_XZ, preset=9 | lzma.PRESET_EXTREME)
    return gzip.compress(raw, 9)

def b64wrap(blob, width=76):
    s = base64.b64encode(blob).decode("ascii")
    return "\n".join(s[i:i+width] for i in range(0, len(s), width)) + "\n"

def chunk_text(text, limit):
    head = "cat > p%03d.b64 <<'B64_EOF'\n"
    tail = "\nB64_EOF\n"
    overhead = max(len(head % 999), 32) + len(tail)
    size = limit - overhead
    if size < 200:
        sys.exit("limit too small: %d" % limit)
    parts, off = [], 0
    while off < len(text):
        piece = text[off:off+size]
        off += size
        if not piece.endswith("\n"):
            piece += "\n"
        parts.append(piece)
    return parts

def write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)

def sha(b):
    return hashlib.sha256(b).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="kit")
    ap.add_argument("--limit", type=int, default=20000, help="max chars per pasted block")
    ap.add_argument("--format", choices=("xz", "gz"), default="xz")
    ap.add_argument("--probes", action="store_true")
    ap.add_argument("--exclude", default="", help="comma-separated paths or glob patterns to drop")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    excl = [x.strip() for x in a.exclude.split(",") if x.strip()]
    files = [f for f in repo_files(repo) if not any(fnmatch.fnmatch(f, p) for p in excl)]
    if not files:
        sys.exit("no tracked files in %s" % repo)

    raw = build_tar(repo, files)
    blob = compress(raw, a.format)
    ext = "xz" if a.format == "xz" else "gz"
    tar_name = "optimizer-%s.tar.%s" % (version_of(repo).lstrip("v"), ext)
    open(os.path.join(out, tar_name), "wb").write(blob)
    open(os.path.join(out, "kit.tar." + ext), "wb").write(blob)

    text = b64wrap(blob)
    parts = chunk_text(text, a.limit)

    for old in os.listdir(out):
        if (old.startswith("paste_") and old.endswith(".sh")) or old.startswith("raw_") \
                or old in ("all.b64", "clip2file.py") or old.startswith("p") and old.endswith(".b64"):
            try:
                os.remove(os.path.join(out, old))
            except OSError:
                pass

    lines, chunk_sum = [], []
    for i, piece in enumerate(parts, 1):
        block = "cat > p%03d.b64 <<'B64_EOF'\n%s\nB64_EOF\n" % (i, piece.rstrip("\n"))
        assert len(block) <= a.limit, (len(block), a.limit)
        write(os.path.join(out, "paste_%03d.sh" % i), block)
        write(os.path.join(out, "raw_%03d.b64" % i), piece)
        h = sha(piece.encode())
        chunk_sum.append("p%03d.b64  %8d chars  sha256=%s" % (i, len(piece), h))
        lines.append(h)

    write(os.path.join(out, "all.b64"), text)
    write(os.path.join(out, "clip2file.py"), CLIP2FILE)
    check = ["#!/bin/sh", "fail=0"]
    for i, h in enumerate(lines, 1):
        check.append('echo "%s  p%03d.b64" | sha256sum -c - || fail=1' % (h, i))
    check.append('exit $fail')
    write(os.path.join(out, "check.sh"), "\n".join(check) + "\n")

    dec = "xz" if a.format == "xz" else "gzip"
    unpack = (
        "#!/bin/sh\n"
        "set -e\n"
        "cat p*.b64 | tr -d '\\r\\n ' | base64 -d > kit_dl.tar.%s\n"
        "echo '%s  kit_dl.tar.%s' | sha256sum -c -\n"
        "target=\"${1:-optimizer-%s}\"\n"
        "mkdir -p \"$target\"\n"
        "tar -x%sf kit_dl.tar.%s -C \"$target\"\n"
        "echo \"OK $(find \"$target\" -type f | wc -l) files -> $target\"\n"
    ) % (ext, sha(blob), ext, version_of(repo).lstrip("v"), "J" if a.format == "xz" else "z", ext)
    write(os.path.join(out, "unpack.sh"), unpack)

    man = []
    man.append("version: %s" % version_of(repo))
    man.append("files: %d  raw_tar: %d  %s: %d  base64: %d chars" % (
        len(files), len(raw), ext, len(blob), len(text)))
    man.append("chunks: %d at limit %d chars/paste" % (len(parts), a.limit))
    man.append("blob sha256: %s" % sha(blob))
    man.append("")
    man.append("paste counts: " + "  ".join(
        "%dk=%d" % (lim // 1000, (len(text) + lim - 65) // (lim - 64))
        for lim in (8000, 16000, 32000, 64000, 128000, 256000, 512000)))
    man.append("")
    man.extend(chunk_sum)
    man.append("")
    man.append("A 终端粘贴: 依次粘贴 paste_001.sh .. paste_%03d.sh，再执行 sh check.sh && sh unpack.sh" % len(parts))
    man.append("B 剪贴板落盘: 复制 raw_001.b64 .. raw_%03d.b64，每片在服务器执行" % len(parts))
    man.append("  python3 clip2file.py p001.b64   (DISPLAY=:0，需图形会话)")
    man.append("C 一次落盘: 复制 all.b64 -> python3 clip2file.py p000.b64 -> sh unpack.sh")
    man.append("校验: 每片落盘后 sh check.sh；unpack.sh 还会校验整包 sha256")
    man.append("注意: RealVNC 剪贴板上限 256kB，超限会整段粘贴失败并粘出上一次的旧内容；")
    man.append("     若 check.sh 报某片不符，说明该次复制超限或粘到了旧内容，重新复制该片即可。")
    write(os.path.join(out, "MANIFEST.txt"), "\n".join(man) + "\n")

    if a.probes:
        for kb in (8, 16, 32, 64, 128, 256):
            n = kb * 1000
            body = "cat > probe_%03dk.txt <<'P_EOF'\n%s\nP_EOF\nwc -c probe_%03dk.txt\n" % (
                kb, "A" * n, kb)
            write(os.path.join(out, "probes", "probe_%03dk.sh" % kb), body)

    print("files=%d raw=%d %s=%d b64=%d chunks=%d limit=%d" % (
        len(files), len(raw), ext, len(blob), len(text), len(parts), a.limit))
    print("out:", out)

if __name__ == "__main__":
    main()
