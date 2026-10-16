#!/usr/bin/env bash
# netcheck.sh
# Any HTTP status means reachable. 000 or an x-deny-reason header (egress proxy) means blocked.
[ -f .env ] && set -a && . ./.env && set +a
URLS=(
  https://api.anthropic.com
  https://app.daytona.io/api
  https://api.anchorbrowser.io
  https://otlp.arize.com/v1
  https://app.arize.com
  https://api.elevenlabs.io
  https://pypi.org/simple/
  https://files.pythonhosted.org
  https://github.com
  https://download.surrealdb.com
  "${MEMORY_URL:-}"
)
fail=0
for u in "${URLS[@]}"; do
  [ -z "$u" ] && { echo "SKIP  MEMORY_URL unset"; continue; }
  hdr=$(mktemp)
  code=$(curl -s -o /dev/null -D "$hdr" -m 8 -w '%{http_code}' "$u")
  deny=$(grep -i '^x-deny-reason' "$hdr" | tr -d '\r')
  rm -f "$hdr"
  if [ "$code" = "000" ] || [ -n "$deny" ]; then
    echo "FAIL  $code $u $deny"; fail=1
  else
    echo "ok    $code $u"
  fi
done
exit $fail
