#!/usr/bin/env python3
"""
Script to update tasks.py to use the new get_ai_response function.
"""

import re

# Read the current tasks.py file
with open('tasks.py', 'r') as f:
    content = f.read()

# 1. Replace the import statement
content = re.sub(
    r'from chat_server import ai_respond_sync',
    'from chat_server import get_ai_response',
    content
)

# 2. Replace the function call
content = re.sub(
    r'ai_reply, detected_intent, handoff_triggered = ai_respond_sync\(',
    'ai_reply, detected_intent, handoff_triggered = get_ai_response(',
    content
)

# Write the updated content to the new file
with open('tasks_new.py', 'w') as f:
    f.write(content)

print("Updated tasks.py has been created as tasks_new.py")
print("Review the changes and then rename it to tasks.py")