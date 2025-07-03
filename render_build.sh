#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Initialize the database
python -c 'from chat_server import initialize_database; initialize_database()'
