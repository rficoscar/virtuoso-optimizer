#!/bin/sh
set -e
cat p*.b64 | tr -d '\r\n ' | base64 -d > kit_dl.tar.xz
echo 'f1b5849a2149f6cb1237af126420617ec211a2ad13ac16a7a56065681d143313  kit_dl.tar.xz' | sha256sum -c -
target="${1:-optimizer-1.7.0}"
mkdir -p "$target"
tar -xJf kit_dl.tar.xz -C "$target"
echo "OK $(find "$target" -type f | wc -l) files -> $target"
