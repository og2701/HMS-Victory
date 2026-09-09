#!/usr/bin/env python3
"""
Set / Clear Troll Defence Target Script
---------------------------------------
Invokes or clears HMS Victory's troll defence / hunting mode on a user ID or @mention.
Automatically persists the configuration to data/json/chatbot_config.json and triggers
an immediate real-time update to the Discord control panel (dashboard).

Usage:
    python3 scripts/set_troll_target.py <user_id_or_mention>
    python3 scripts/set_troll_target.py clear
    python3 scripts/set_troll_target.py status
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REMOTE_KEY = os.path.expanduser('~/Downloads/owen-dev-key.pem')
REMOTE_HOST = 'ubuntu@ec2-16-61-145-183.eu-west-2.compute.amazonaws.com'
REMOTE_PROJECT_DIR = '/home/ubuntu/HMS-Victory'

def is_running_on_ec2() -> bool:
    try:
        import socket
        hostname = socket.gethostname()
        if hostname.startswith('ip-'):
            return True
    except Exception:
        pass
    return os.path.exists('/home/ubuntu/HMS-Victory')

def parse_user_id(val: str):
    if not val:
        return None
    val = val.strip()
    if val.startswith('<@') and val.endswith('>'):
        val = val[2:-1].lstrip('!')
    try:
        return int(val)
    except ValueError:
        return None

def main():
    parser = argparse.ArgumentParser(
        description='Set or clear troll/defence mode target for HMS Victory.'
    )
    parser.add_argument(
        'target',
        nargs='?',
        default=None,
        help='User ID or @mention to target, or "clear"/"off"/"status"',
    )
    parser.add_argument(
        '--local',
        action='store_true',
        help='Apply only to local workspace without syncing to remote EC2',
    )
    parser.add_argument(
        '--remote',
        action='store_true',
        help='Force execution on remote EC2 server via SSH',
    )

    args = parser.parse_args()

    # Determine config file path
    config_file = PROJECT_ROOT / 'data' / 'json' / 'chatbot_config.json'

    # Load existing config
    current_cfg = {}
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                current_cfg = json.load(f)
        except Exception:
            current_cfg = {}

    current_target = current_cfg.get('target_user_id')

    if not args.target or args.target.lower() in ('status', 'info', 'check'):
        if current_target:
            print(f'🎯 Current Troll/Defence Target: <@{current_target}> (ID: {current_target})')
            print('🚨 Mode: ACTIVE (Retaliating and roasting every message)')
        else:
            print('🛡️ Current Troll/Defence Target: None')
            print('💤 Mode: INACTIVE (Standard live chat / asleep)')
        return

    raw = args.target.strip().lower()
    is_clearing = raw in ('clear', 'off', 'none', '0', 'stop', 'reset', 'disable')

    if is_clearing:
        new_target = None
    else:
        new_target = parse_user_id(args.target)
        if not new_target:
            print(f'❌ Error: Invalid user ID or mention format: "{args.target}"', file=sys.stderr)
            print('Provide a numeric Discord User ID (e.g. 123456789012345678) or @mention, or "clear".', file=sys.stderr)
            sys.exit(1)

    # 1. Update local config file
    config_file.parent.mkdir(parents=True, exist_ok=True)
    current_cfg['target_user_id'] = new_target
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(current_cfg, f, indent=2)

    if new_target:
        print(f'🎯 [LOCAL] Troll/Defence target set to <@{new_target}> (ID: {new_target})')
    else:
        print('🛡️ [LOCAL] Troll/Defence target cleared.')

    # 2. Check if we should sync to remote EC2
    on_ec2 = is_running_on_ec2()
    should_sync_remote = (not on_ec2 and not args.local and os.path.exists(REMOTE_KEY)) or args.remote

    if should_sync_remote:
        print(f'📡 Syncing target configuration to remote EC2 ({REMOTE_HOST})...')
        remote_cmd = f'python3 {REMOTE_PROJECT_DIR}/scripts/set_troll_target.py "{args.target}" --local'
        ssh_cmd = [
            'ssh',
            '-o', 'StrictHostKeyChecking=no',
            '-i', REMOTE_KEY,
            REMOTE_HOST,
            remote_cmd,
        ]
        try:
            res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                print(res.stdout.strip())
                print('⚡ Remote EC2 live watcher notified. Discord control panel view is updating!')
            else:
                print(f'⚠️ Remote sync warning: {res.stderr.strip()}', file=sys.stderr)
        except Exception as e:
            print(f'⚠️ Failed to connect to remote EC2 via SSH: {e}', file=sys.stderr)
    elif on_ec2:
        print('⚡ Live watcher notified on EC2. Discord control panel view is updating!')

if __name__ == '__main__':
    main()
