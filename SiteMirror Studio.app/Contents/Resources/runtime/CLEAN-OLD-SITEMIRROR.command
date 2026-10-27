#!/bin/bash
set -u
# Stop only local listeners that positively identify themselves as SiteMirror Studio.
for PORT in $(seq 8787 8797) $(seq 18787 18797); do
  if /usr/bin/curl -fsS --max-time 0.4 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | /usr/bin/grep -q 'SiteMirror Studio'; then
    PIDS="$(/usr/sbin/lsof -ti tcp:$PORT 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
      echo "Stopping old SiteMirror process on port $PORT: $PIDS"
      kill $PIDS 2>/dev/null || true
    fi
  fi
done
echo "SiteMirror server cleanup complete."
