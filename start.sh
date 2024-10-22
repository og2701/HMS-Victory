#!/bin/bash

echo "Installing dependencies..."
python3 -m pip install -r /home/ubuntu/HMS-Victory/requirements.txt

while true
do
    echo "Starting the Discord bot..."
    python3 /home/ubuntu/HMS-Victory/run.py
    echo "Bot crashed with exit code $?. Restarting in 5 seconds..."
    sleep 5
done

