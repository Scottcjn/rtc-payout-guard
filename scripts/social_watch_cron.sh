#!/usr/bin/env bash
# Hourly stars/forks/followers snapshot. Removals are only detectable by diff,
# so a missed run is a permanent blind spot for whatever changed inside it.
set -uo pipefail
DB="$HOME/.elyan/social_watch.db"
LOG="$HOME/.elyan/social_watch.log"
mkdir -p "$(dirname "$DB")"
cd "$HOME/rtc-payout-guard" || exit 1
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  python3 scripts/social_snapshot.py --db "$DB"
} >> "$LOG" 2>&1
