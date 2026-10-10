#!/usr/bin/env bash
# Pull everything Wrapped is built from off the bot's VM into one local folder.
#
#   HMS_VM=ubuntu@host HMS_VM_KEY=~/path/key.pem scripts/wrapped/pull.sh <data dir> [user ids...]
#
# Writes chronicle.db (last night's consistent snapshot), database.db (a backup taken now, not a
# copy of the live file), the JSON state files the video reads, and discord.json (names, avatars,
# channels, emoji). Passing user ids looks up people who have left the server; extract.py lists the
# ones it needs in <data dir>/unresolved.txt, so the usual flow is pull, extract, pull again with
# those ids, extract again. Heavy queries never run on the VM: it's a t3.small with the bot live.
set -euo pipefail
OUT=${1:?usage: pull.sh <data dir> [user ids...]}
shift
: "${HMS_VM:?set HMS_VM to user@host}" "${HMS_VM_KEY:?set HMS_VM_KEY to the ssh key}"
SSH=(ssh -i "$HMS_VM_KEY" -o BatchMode=yes "$HMS_VM")
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"

if [ $# -eq 0 ]; then
  echo "chronicle snapshot..."
  "${SSH[@]}" 'nice -n 15 zstd -1 -T1 -c ~/HMS-Victory/chronicle.db.snapshot' | zstd -q -d -f -o "$OUT/chronicle.db"

  echo "database backup..."
  "${SSH[@]}" 'cd ~/HMS-Victory && rm -f /tmp/wrapped_db.db && venv/bin/python -c "
import sqlite3
src = sqlite3.connect(\"file:database.db?mode=ro\", uri=True)
dst = sqlite3.connect(\"/tmp/wrapped_db.db\")
src.backup(dst)
dst.close()
" && cat /tmp/wrapped_db.db && rm -f /tmp/wrapped_db.db' > "$OUT/database.db"

  echo "state files..."
  for f in hall_of_fame.json skyrim_profiles.json economy_metrics.json; do
    "${SSH[@]}" "cat ~/HMS-Victory/data/json/$f" > "$OUT/$f"
  done
fi

echo "names, avatars, channels, emoji..."
"${SSH[@]}" "cd ~/HMS-Victory && venv/bin/python - $*" < "$HERE/fetch_discord.py" > "$OUT/discord.json.tmp"
mv "$OUT/discord.json.tmp" "$OUT/discord.json"
ls -la "$OUT"
