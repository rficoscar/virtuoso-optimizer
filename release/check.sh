#!/bin/sh
fail=0
echo "6234ba55f41405f2aca5118797ff298b90660aeff42c0336e3d9b2fea01b1291  p001.b64" | sha256sum -c - || fail=1
echo "e6c60787716bb14f96a681802bf99e41361bce6180258beb18c6a8a938319b8e  p002.b64" | sha256sum -c - || fail=1
exit $fail
