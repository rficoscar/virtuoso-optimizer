#!/bin/sh
fail=0
echo "489014d7a4496ab8b7a25b590e071c708267e712a7f3c5327ce4a507d47b5f13  p001.b64" | sha256sum -c - || fail=1
echo "bc4ec6906b56c3f2a867ffee4e5dfad1119d2576e286045d70027a84527e62e3  p002.b64" | sha256sum -c - || fail=1
exit $fail
