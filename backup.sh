#!/bin/bash
# Copy the live database to the lab share, keeping the last N days of backups.
# Usage: backup.sh [config.json]
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:-$DIR/config.json}"
read_cfg() { /usr/bin/python3 -c "import json,sys; c=json.load(open(sys.argv[1])); print(c.get(sys.argv[2], sys.argv[3]))" "$CONFIG" "$1" "$2"; }

DB="$(read_cfg db freezer.db)"
[[ "$DB" = /* ]] || DB="$DIR/$DB"
DEST="$(read_cfg backup_dir /Volumes/MTB_Lab/FreezerTracker/backups)"
KEEP="$(read_cfg backup_keep_days 30)"

# Skip quietly (but logged) if the share isn't mounted, rather than writing to the local disk.
SHARE_ROOT="$(echo "$DEST" | cut -d/ -f1-3)"
if [[ "$SHARE_ROOT" == /Volumes/* && ! -d "$SHARE_ROOT" ]]; then
  echo "$(date '+%F %T') backup skipped: $SHARE_ROOT is not mounted"
  exit 0
fi

mkdir -p "$DEST"
OUT="$DEST/freezer-$(date +%Y-%m-%d_%H%M).db"
# .backup takes a consistent copy even while the server is running.
/usr/bin/sqlite3 "$DB" ".backup '$OUT'"
/usr/bin/sqlite3 "$OUT" "PRAGMA integrity_check;" | grep -qx ok
find "$DEST" -name 'freezer-*.db' -mtime +"$KEEP" -delete
echo "$(date '+%F %T') backup written: $OUT"
