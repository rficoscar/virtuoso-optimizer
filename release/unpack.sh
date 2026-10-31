#!/bin/sh
set -e
cat p*.b64 | tr -d '\r\n ' | base64 -d > kit_dl.tar.xz
echo 'f4d07601b8a0056cf143211bee3cf019e74657eae57197594694b170f26a0f40  kit_dl.tar.xz' | sha256sum -c -
target="${1:-optimizer-1.7.2}"
mkdir -p "$target"
tar -xJf kit_dl.tar.xz -C "$target"
echo "OK $(find "$target" -type f | wc -l) files -> $target"
