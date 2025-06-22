#!/bin/bash
# Robust launch script for Hotel Chatbot

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment in ./.venv..."
    python3 -m venv .venv
fi

# Activate the virtual environment
source .venv/bin/activate

# Ensure gunicorn is installed in the venv
if ! .venv/bin/pip show gunicorn &> /dev/null; then
    echo "Installing dependencies (including gunicorn) into ./.venv..."
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

# Set environment variables (edit as needed)
export OPENAI_API_KEY="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"
export REDIS_URL="redis://localhost:6379/0"
export OPENAI_CONCURRENCY=5

# Launch the server using the venv's python and gunicorn
exec .venv/bin/gunicorn -k gevent -w 4 chat_server:app --config gunicorn.conf.py
