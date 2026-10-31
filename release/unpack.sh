#!/bin/sh
set -e
cat p*.b64 | tr -d '\r\n ' | base64 -d > kit_dl.tar.xz
echo 'd7deec0ec68fbd54f6ce1a57cc50cdf48bda910d4a669baaff5b8b800f9dcb76  kit_dl.tar.xz' | sha256sum -c -
target="${1:-optimizer-1.7.3}"
mkdir -p "$target"
tar -xJf kit_dl.tar.xz -C "$target"
echo "OK $(find "$target" -type f | wc -l) files -> $target"
