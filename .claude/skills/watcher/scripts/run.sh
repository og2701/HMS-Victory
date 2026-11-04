#!/bin/sh
# Runs a Python script on the VM inside ~/HMS-Victory, where it loads Vic's .env, so the bot token never leaves the box.
#   run.sh <script.py> [args...]   a script from this folder
#   run.sh - [args...]             the script comes in on stdin (e.g. from card.py)
#
# On Oggers' Mac it uses the local key and the VM's public address. Anywhere else, such as a cloud session (which
# can't reach port 22), it tunnels through AWS SSM and takes the key from $VIC_SSH_KEY (the key itself) or
# $VIC_SSH_KEY_FILE (a path). VIC_SSH_ROUTE=ssm forces the tunnel on the Mac too, for testing.
here=$(cd "$(dirname "$0")" && pwd)
script=$1
shift
remote="cd ~/HMS-Victory && venv/bin/python - $*"
mac_key="$HOME/Documents/Personal/owen-dev-key.pem"

if [ -f "$mac_key" ] && [ "${VIC_SSH_ROUTE:-}" != "ssm" ]; then
    vm() {
        ssh -o BatchMode=yes -o ServerAliveInterval=60 -i "$mac_key" \
            ubuntu@ec2-16-61-145-183.eu-west-2.compute.amazonaws.com "$remote"
    }
else
    key=${VIC_SSH_KEY_FILE:-}
    if [ -z "$key" ] && [ -n "${VIC_SSH_KEY:-}" ]; then
        key="${TMPDIR:-/tmp}/vic-ssh-key"
        # Secrets sometimes arrive on one line with literal \n; put the real line breaks back.
        (umask 077 && printf '%s\n' "$VIC_SSH_KEY" | sed 's/\\n/\
/g' > "$key")
    fi
    if [ -z "$key" ] || [ ! -f "$key" ]; then
        echo "run.sh: no SSH key here. Set VIC_SSH_KEY (the owen-dev-key private key) or VIC_SSH_KEY_FILE." >&2
        exit 2
    fi
    for tool in aws session-manager-plugin; do
        command -v "$tool" >/dev/null 2>&1 || { echo "run.sh: the SSM tunnel needs $tool installed." >&2; exit 2; }
    done
    vm() {
        ssh -o BatchMode=yes -o ServerAliveInterval=60 -o StrictHostKeyChecking=accept-new -i "$key" \
            -o ProxyCommand="aws ssm start-session --region eu-west-2 --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p" \
            ubuntu@i-0ce7cc704a996a899 "$remote"
    }
fi

if [ "$script" = "-" ]; then
    vm
else
    vm < "$here/$script"
fi
