# Hotel Chatbot Remediation Implementation Steps

This document outlines the steps to implement the fixes for the Hotel Chatbot application, resolving the conflict between asyncio and gevent that caused the AI responses and live message updates to fail.

## Step 1: Update chat_server.py

1. Make a backup of the current chat_server.py file.
   ```bash
   cp chat_server.py chat_server.py.backup
   ```

2. Edit chat_server.py to make the following changes:

   a. Change the OpenAI import:
   ```python
   # From:
   from openai import AsyncOpenAI, OpenAI
   
   # To:
   from openai import OpenAI
   ```

   b. Remove the asyncio import if not used elsewhere:
   ```python
   # Remove or comment this line:
   import asyncio
   ```

   c. Remove the redis.asyncio import if not used elsewhere:
   ```python
   # Remove or comment this line:
   import redis.asyncio as redis
   ```

   d. Change the OpenAI client initialization:
   ```python
   # From:
   openai_client = AsyncOpenAI(
       api_key=OPENAI_API_KEY,
       timeout=30.0
   )
   
   # To:
   openai_client = OpenAI(
       api_key=OPENAI_API_KEY,
       timeout=30.0
   )
   logger.info("OpenAI client initialized.")
   ```

   e. Replace the asyncio semaphore:
   ```python
   # From:
   OPENAI_CONCURRENCY = int(os.getenv("OPENAI_CONCURRENCY", "5"))
   openai_semaphore = asyncio.Semaphore(OPENAI_CONCURRENCY)
   
   # To:
   OPENAI_CONCURRENCY = int(os.getenv("OPENAI_CONCURRENCY", "5"))
   logger.info(f"OpenAI concurrency limit: {OPENAI_CONCURRENCY}")
   ```

   f. Remove the async ai_respond function (around lines 204-283)
   
   g. Remove the ai_respond_sync function (around lines 285-299)
   
   h. Add the new get_ai_response function from chat_server_patch.py

## Step 2: Update tasks.py

1. Make a backup of the current tasks.py file.
   ```bash
   cp tasks.py tasks.py.backup
   ```

2. Edit tasks.py to make the following changes:

   a. Change the import statement:
   ```python
   # From:
   from chat_server import ai_respond_sync
   
   # To:
   from chat_server import get_ai_response
   ```

   b. Change the function call:
   ```python
   # From:
   ai_reply, detected_intent, handoff_triggered = ai_respond_sync(
       convo_id=convo_id,
       username=username,
       conversation_history=conversation_history,
       user_message=message_body,
       chat_id=chat_id,
       channel="whatsapp",
       language=language
   )
   
   # To:
   ai_reply, detected_intent, handoff_triggered = get_ai_response(
       convo_id=convo_id,
       username=username,
       conversation_history=conversation_history,
       user_message=message_body,
       chat_id=chat_id,
       channel="whatsapp",
       language=language
   )
   ```

## Step 3: Update render.yaml

1. Make a backup of the current render.yaml file.
   ```bash
   cp render.yaml render.yaml.backup
   ```

2. Replace render.yaml with the content from render_updated.yaml, or edit it to change the startCommand:

   ```yaml
   # From:
   startCommand: gunicorn chat_server:app --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --config gunicorn.conf.py
   
   # To:
   startCommand: gunicorn -k gevent -w 4 chat_server:app --config gunicorn.conf.py
   ```

## Step 4: Update requirements.txt

1. Make a backup of the current requirements.txt file.
   ```bash
   cp requirements.txt requirements.txt.backup
   ```

2. Replace requirements.txt with the content from requirements_updated.txt, or edit it to remove the eventlet dependency:

   ```
   # Remove this line:
   eventlet==0.39.0
   ```

## Step 5: Deploy and Verify

1. Commit the changes to your git repository.

2. Deploy to Render.com.

3. Use the verification_checklist.md file to test and verify that the application is working correctly.

## Troubleshooting

If issues persist after these changes, check the application logs for any new errors. Common issues might include:

1. Import errors - Make sure all necessary imports are present and not duplicated.
2. Function name errors - Ensure that all references to the old functions have been updated.
3. OpenAI API errors - Check if the API key is valid and has sufficient quota.
4. Redis connection errors - Verify that the Redis service is running and accessible.
5. Database connection errors - Ensure the database service is running and accessible.