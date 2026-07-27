#!/usr/bin/env bash
# Hourly stars/forks/followers snapshot. Removals are only detectable by diff,
# so a missed run is a permanent blind spot for whatever changed inside it.
# flock: a full pass takes ~5 min but retries can stretch it; overlapping runs
# would read stale prior state and could manufacture false removals.
set -uo pipefail
DB="$HOME/.elyan/social_watch.db"
LOG="$HOME/.elyan/social_watch.log"
mkdir -p "$(dirname "$DB")"
exec 9>"$HOME/.elyan/social_watch.lock"
flock -n 9 || { echo "$(date -u +%FT%TZ) previous run still active, skipping" >> "$LOG"; exit 0; }
cd "$HOME/rtc-payout-guard" || exit 1
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  python3 scripts/social_snapshot.py --db "$DB"
  echo "exit=$?"
} >> "$LOG" 2>&1
# keep the log bounded
tail -n 5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
