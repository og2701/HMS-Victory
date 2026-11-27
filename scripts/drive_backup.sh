#!/bin/bash
# Off-box backups to Google Drive, run from the VM's crontab (no bot code involved).
#
#   scripts/drive_backup.sh state       database.db + JSON state, every 6 hours
#   scripts/drive_backup.sh chronicle   last night's chronicle.db.snapshot, weekly
#
# Uploads go to the rclone remote "gdrive" (Google Drive, drive.file scope, so rclone only ever
# sees the files it made itself), folder "HMS Victory backups". Anything older than the retention
# for its kind is deleted outright, not sent to the Drive bin, so it stops counting against quota.
# The Discord backups keep running alongside this; nothing here touches them.
#
# Kept light on purpose (the VM has 2GB of RAM): one job at a time, lowest CPU and IO priority,
# single-threaded zstd, and sqlite3's own online backup for a consistent copy of the live DB.
#
# A failure posts to #logs, because a backup that only fails in a log file looks healthy.
#
# Crontab on the VM (times are UTC; the chronicle run follows the bot's 04:20 London snapshot):
#   15 */6 * * * /home/ubuntu/HMS-Victory/scripts/drive_backup.sh state >> /home/ubuntu/drive_backup/backup.log 2>&1
#   30 5 * * 0   /home/ubuntu/HMS-Victory/scripts/drive_backup.sh chronicle >> /home/ubuntu/drive_backup/backup.log 2>&1
#
# Restore, on the VM:
#   rclone lsl "gdrive:HMS Victory backups/state"
#   rclone copy "gdrive:HMS Victory backups/state/database_<stamp>.db.zst" /tmp/restore/
#   zstd -d /tmp/restore/database_<stamp>.db.zst      (stop the bot before swapping database.db in)
#   json_<stamp>.tar.zst unpacks with: tar --zstd -xf json_<stamp>.tar.zst   (from ~/HMS-Victory)
set -Eeuo pipefail

MODE=${1:-state}
BOT_DIR=${BOT_DIR:-/home/ubuntu/HMS-Victory}
WORK=${DRIVE_BACKUP_WORK:-/home/ubuntu/drive_backup}
REMOTE=${DRIVE_BACKUP_REMOTE:-"gdrive:HMS Victory backups"}
RCLONE=${RCLONE:-/home/ubuntu/.local/bin/rclone}
LOGS_CHANNEL=959723562892144690
STAMP=$(date -u +%Y-%m-%d_%H-%M)
TMP="$WORK/tmp"

mkdir -p "$TMP"
exec 9>"$WORK/.lock"
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) another drive backup is still running; skipping $MODE"
    exit 0
fi
renice -n 19 $$ >/dev/null
ionice -c3 -p $$ 2>/dev/null || true

alert() {
    local token
    token=$(grep -E '^DISCORD_TOKEN=' "$BOT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
    [ -n "$token" ] || return 0
    local msg="🚨 **Google Drive $MODE backup failed** - $1
-# The Discord backups are unaffected. Log: ~/drive_backup/backup.log on the VM."
    python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1][:1900]}))' "$msg" |
        curl -fsS -m 20 -o /dev/null -X POST \
            -H "Authorization: Bot $token" -H "Content-Type: application/json" \
            --data @- "https://discord.com/api/v10/channels/$LOGS_CHANNEL/messages" || true
}

on_error() {
    local rc=$? line=$1
    echo "$(date -u +%FT%TZ) FAILED ($MODE) at line $line, exit $rc"
    alert "exit $rc at line $line of drive_backup.sh"
    rm -f "$TMP"/*
}
trap 'on_error $LINENO' ERR

upload() {  # upload <file> <subfolder> <retention>
    "$RCLONE" copy "$1" "$REMOTE/$2" --retries 3 --low-level-retries 10 --stats 0
    "$RCLONE" delete "$REMOTE/$2" --min-age "$3" --drive-use-trash=false
    echo "$(date -u +%FT%TZ) uploaded $(basename "$1") ($(du -h "$1" | cut -f1)) to $2"
    rm -f "$1"
}

case "$MODE" in
state)
    cd "$BOT_DIR"
    rm -f "$TMP"/*
    db="$TMP/database_$STAMP.db"
    sqlite3 database.db ".timeout 30000" ".backup '$db'"
    check=$(sqlite3 "$db" "PRAGMA quick_check;")
    [ "$check" = "ok" ] || { echo "quick_check: $check"; false; }
    zstd -q -T1 -6 --rm "$db"
    upload "$db.zst" state 30d

    dirs=()
    for d in data/json daily_summaries balance_snapshots; do [ -d "$d" ] && dirs+=("$d"); done
    if [ ${#dirs[@]} -gt 0 ]; then
        # Only .json, like the bot's own backup: data/json also holds ~600MB of rebuildable
        # map tiles (guessr-map) that would make every copy ~370MB.
        find "${dirs[@]}" -type f -name '*.json' -print0 |
            tar --null -T - -cf - | zstd -q -T1 -6 > "$TMP/json_$STAMP.tar.zst"
        upload "$TMP/json_$STAMP.tar.zst" state 30d
    fi
    ;;
chronicle)
    snap="$BOT_DIR/chronicle.db.snapshot"
    [ -f "$snap" ] || { echo "no chronicle snapshot at $snap"; false; }
    # The bot writes the snapshot at 04:20 via a temp file + rename, so it's whole whenever it exists.
    rm -f "$TMP"/*
    out="$TMP/chronicle_$STAMP.db.zst"
    zstd -q -T1 -6 "$snap" -o "$out"
    upload "$out" chronicle 22d
    ;;
*)
    echo "usage: $0 state|chronicle" >&2
    exit 2
    ;;
esac
