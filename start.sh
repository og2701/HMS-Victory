#!/bin/bash

echo "Installing dependencies..."
pip3 install -r /home/ec2-user/discord_bot/requirements.txt

while true
do
    echo "Starting the Discord bot..."
    python3 /home/ec2-user/discord_bot/run.py
    echo "Bot crashed with exit code $?. Restarting in 5 seconds..."
    sleep 5
done

