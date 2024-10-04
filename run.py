import os
import sys

from main import client
import lib.settings as settings

sys.modules['ROLES'] = settings.ROLES
sys.modules['CHANNELS'] = settings.CHANNELS
sys.modules['USERS'] = settings.USERS
sys.modules['POLITICS_WHITELISTED_USER_IDS'] = settings.POLITICS_WHITELISTED_USER_IDS
sys.modules['command_usage_tracker'] = settings.command_usage_tracker

if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if TOKEN is None:
        raise ValueError("No DISCORD_TOKEN found in environment variables")
    client.run(TOKEN)
