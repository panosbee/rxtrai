#!/bin/bash
# RxTrail nightly backup: mongodump + uploads, 14 μέρες retention.
# Μπαίνει σε cron: 0 3 * * * /var/www/rxtrail/backup.sh >> /var/log/rxtrail-backup.log 2>&1
set -e
BK_DIR="/var/www/rxtrail/backups"
STAMP=$(date +%Y%m%d_%H%M)
mkdir -p "$BK_DIR"

mongodump --db rxtrail --out "$BK_DIR/db_$STAMP" --quiet
tar czf "$BK_DIR/rxtrail_db_$STAMP.tar.gz" -C "$BK_DIR" "db_$STAMP"
rm -rf "$BK_DIR/db_$STAMP"
tar czf "$BK_DIR/rxtrail_uploads_$STAMP.tar.gz" -C /var/www/rxtrail uploads 2>/dev/null || true

# Retention: 14 μέρες
find "$BK_DIR" -name "rxtrail_db_*.tar.gz" -mtime +14 -delete
find "$BK_DIR" -name "rxtrail_uploads_*.tar.gz" -mtime +14 -delete
find "$BK_DIR" -name "predeploy_*.tar.gz" -mtime +30 -delete

echo "[$STAMP] backup OK: $(du -sh "$BK_DIR/rxtrail_db_$STAMP.tar.gz" | cut -f1)"
