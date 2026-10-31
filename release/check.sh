#!/bin/sh
fail=0
echo "27c3dd77a9620643d961864ddba467136244bd895788caaa810252b2ead64f35  p001.b64" | sha256sum -c - || fail=1
echo "28b2dec04eae8755f2540e4a244c528b962eeb87a5099a6fe8918c33ec9592f3  p002.b64" | sha256sum -c - || fail=1
exit $fail
