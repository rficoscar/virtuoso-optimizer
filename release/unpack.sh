#!/bin/sh
set -e
cat p*.b64 | tr -d '\r\n ' | base64 -d > kit_dl.tar.xz
echo 'd3c400a5af3dd45725cf7ed5f40ac6fd8b630670e58ee04fd0d0be159746ba0d  kit_dl.tar.xz' | sha256sum -c -
target="${1:-optimizer-1.7.1}"
mkdir -p "$target"
tar -xJf kit_dl.tar.xz -C "$target"
echo "OK $(find "$target" -type f | wc -l) files -> $target"
