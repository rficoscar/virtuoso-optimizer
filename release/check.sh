#!/bin/sh
fail=0
echo "2f2a76730d879689743f07e8be87e07a6f1954fca7307ed4b9d244f3775ddc6c  p001.b64" | sha256sum -c - || fail=1
echo "e5ba0aae3d8f74e381488adbe808fb1fe4ca515e1aa24abfa4965f2a1920924d  p002.b64" | sha256sum -c - || fail=1
exit $fail
