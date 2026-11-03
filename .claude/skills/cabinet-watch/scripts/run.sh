#!/bin/sh
# Runs a Python script on the VM inside ~/HMS-Victory, where it loads Vic's .env, so the bot token never leaves the box.
#   run.sh <script.py> [args...]   a script from this folder
#   run.sh - [args...]             the script comes in on stdin (e.g. from card.py)
here=$(cd "$(dirname "$0")" && pwd)
script=$1
shift
vm() {
    ssh -o BatchMode=yes -o ServerAliveInterval=60 -i "$HOME/Documents/Personal/owen-dev-key.pem" \
        ubuntu@ec2-16-61-145-183.eu-west-2.compute.amazonaws.com "cd ~/HMS-Victory && venv/bin/python - $*"
}
if [ "$script" = "-" ]; then
    vm "$@"
else
    vm "$@" < "$here/$script"
fi
