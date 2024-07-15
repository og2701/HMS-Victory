#!/bin/bash

while true
do
    echo "Starting the Discord bot..."
    python run.py
    echo "Bot crashed with exit code $?. Restarting in 5 seconds..."
    sleep 5
done
