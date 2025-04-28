#!/bin/bash

# Exit on error
set -e

# Go to the script's directory (project root)
cd "$(dirname "$0")"

# Use non-GUI backend for matplotlib
export MPLBACKEND=Agg

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install requirements
pip install --upgrade pip
pip install -r requirements.txt

# Run the bot
echo "Starting LNhelperBot..."
python bot.py