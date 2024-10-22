#!/bin/bash

VENV_DIR="/home/ubuntu/HMS-Victory/venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating a virtual environment..."
    python3 -m venv $VENV_DIR
fi

source $VENV_DIR/bin/activate

echo "Installing dependencies..."
pip install -r /home/ubuntu/HMS-Victory/requirements.txt

while true
do
    echo "Starting the Discord bot..."
    python /home/ubuntu/HMS-Victory/run.py
    echo "Bot crashed with exit code $?. Restarting in 5 seconds..."
    sleep 5
done
