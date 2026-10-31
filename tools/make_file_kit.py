#!/usr/bin/env python3
"""按文件生成可一次全选复制的落盘脚本（gzip+base64）。

用法: python make_file_kit.py [--repo DIR] [--out DIR]
产物: put__<path>.sh（每个文件一个）、check_files.sh、MANIFEST.txt
"""
import argparse, base64, gzip, hashlib, os, subprocess, sys

def repo_files(repo):
    out = subprocess.check_output(["git", "-C", repo, "ls-files", "-z"])
    return [p.decode("utf-8") for p in out.split(b"\0") if p]

def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="perfile")
    a = ap.parse_args()
    repo, out = os.path.abspath(a.repo), os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    files = repo_files(repo)
    man, checks = [], []
    for rel in files:
        data = open(os.path.join(repo, *rel.split("/")), "rb").read()
        gz = gzip.compress(data, 9)
        b = base64.b64encode(gz).decode("ascii")
        body = "\n".join(b[i:i+76] for i in range(0, len(b), 76))
        token = hashlib.sha256(rel.encode()).hexdigest()[:12]
        cmd = ""
        parent = os.path.dirname(rel)
        if parent:
            cmd += "mkdir -p %s\n" % parent
        cmd += "tr -d '\\r\\n ' <<'B64_%s' | base64 -d | gzip -dc > %s\n%s\nB64_%s\n" % (
            token, rel, body, token)
        name = "put__" + rel.replace("/", "__") + ".sh"
        write(os.path.join(out, name), cmd)
        h = hashlib.sha256(data).hexdigest()
        checks.append("echo '%s  %s' | sha256sum -c -" % (h, rel))
        man.append("%-34s raw=%7d b64=%7d script=%7d sha256=%s" % (
            rel, len(data), len(b), len(cmd), h[:16]))
    write(os.path.join(out, "check_files.sh"),
          "#!/bin/sh\nfail=0\n" + "\n".join(c + " || fail=1" for c in checks) +
          "\nexit $fail\n")
    write(os.path.join(out, "MANIFEST.txt"),
          "files=%d\n\n" % len(files) + "\n".join(man) + "\n\n"
          "用法: 依次全选复制 put__*.sh 粘贴回车；全部完成后 sh check_files.sh\n"
          "解码链: tr 去空白 -> base64 -d -> gzip -dc；目标机需 base64 与 gzip\n")
    print("files=%d out=%s" % (len(files), out))
    print("\n".join(man))

if __name__ == "__main__":
    main()
