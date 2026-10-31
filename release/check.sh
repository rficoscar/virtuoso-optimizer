#!/bin/sh
fail=0
echo "6a1bd9bfab545539b64bed0d748708b5e53508183cea50232d352f9f2d72d2af  p001.b64" | sha256sum -c - || fail=1
echo "932c250869a623b0b515de66dd10b4629eedddbbc47179e50bcd5f973ac6ac95  p002.b64" | sha256sum -c - || fail=1
exit $fail
