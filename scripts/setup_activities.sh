#!/usr/bin/env bash
# One-off setup for ukplace activities (Wordle as a Discord Activity) on the bot's server.
# Run it ON the server, from anywhere:
#     bash ~/HMS-Victory/scripts/setup_activities.sh
#
# It installs cloudflared, connects the ukplace-activities tunnel, and puts the app's secrets
# in .env. It asks for three secrets, which you paste in (nothing is shown as you type) and
# which it never prints. Safe to run again: it skips what's already done.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=.env
CLIENT_ID=1555603316547780608

set_env() {   # set_env KEY VALUE - replace any existing line for KEY
  touch "$ENV_FILE"
  local tmp; tmp=$(mktemp)
  grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > "$ENV_FILE" && rm -f "$tmp"
}

echo "== 1/3  Cloudflare tunnel"
if ! command -v cloudflared >/dev/null 2>&1; then
  arch=$(dpkg --print-architecture)
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  sudo dpkg -i /tmp/cloudflared.deb >/dev/null
  rm -f /tmp/cloudflared.deb
fi
if systemctl is-active --quiet cloudflared 2>/dev/null; then
  echo "   tunnel service already running"
else
  echo "   In Cloudflare, copy the 'Install as service' box (sudo cloudflared service install eyJ...)."
  read -rsp "   Paste it here: " tunnel; echo
  tunnel=${tunnel##* }          # the whole command or just the token both work
  sudo cloudflared service install "$tunnel" >/dev/null
  unset tunnel
  echo "   tunnel service installed"
fi

echo "== 2/3  Discord app secrets"
set_env ACTIVITIES_CLIENT_ID "$CLIENT_ID"
if grep -q '^ACTIVITIES_CLIENT_SECRET=.' "$ENV_FILE" && grep -q '^ACTIVITIES_BOT_TOKEN=.' "$ENV_FILE"; then
  echo "   already in .env (delete those lines and re-run to replace them)"
else
  read -rsp "   Client secret (portal > ukplace activities > OAuth2 > Reset Secret): " secret; echo
  read -rsp "   Bot token (portal > ukplace activities > Bot > Reset Token): " token; echo
  set_env ACTIVITIES_CLIENT_SECRET "$secret"
  set_env ACTIVITIES_BOT_TOKEN "$token"
  unset secret token
fi
grep -q '^ACTIVITIES_SESSION_SECRET=.' "$ENV_FILE" || set_env ACTIVITIES_SESSION_SECRET "$(openssl rand -hex 32)"
echo "   saved"

echo "== 3/3  Restarting the bot"
./update_bot.sh >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1; then
    echo "   activities API is up on 127.0.0.1:8787"
    exit 0
  fi
  sleep 2
done
echo "   the API didn't come up - check: journalctl -u hms-victory -n 50 | grep -i activit"
exit 1
