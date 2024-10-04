import os
from main import client

if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if TOKEN is None:
        raise ValueError("No DISCORD_TOKEN found in environment variables")
    client.run(TOKEN)
