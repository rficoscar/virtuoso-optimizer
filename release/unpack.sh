#!/bin/sh
set -e
cat p*.b64 | tr -d '\r\n ' | base64 -d > kit_dl.tar.xz
echo '75177d24ff4cb2228aac6cf359280e864d0077aba3131ace9c94ecd668ac9d56  kit_dl.tar.xz' | sha256sum -c -
target="${1:-optimizer-1.7.2}"
mkdir -p "$target"
tar -xJf kit_dl.tar.xz -C "$target"
echo "OK $(find "$target" -type f | wc -l) files -> $target"
