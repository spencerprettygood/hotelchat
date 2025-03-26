# tasks.py
# Gevent monkey-patching at the very top
import gevent
from gevent import monkey
monkey.patch_all()

# Now proceed with other imports
import os
import sys
import logging
from pathlib import Path
from celery import Celery
from datetime import datetime, timezone
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import requests

# Add the project root directory to the Python path to fix import issues
project_root = str(Path(__file__).parent.absolute())
if project_root not in sys.path:
    sys.path.append(project_root)

# Configure logging for Celery to preserve application log levels
logger = logging.getLogger("chat_server")  # Match the logger name used in chat_server.py
celery_logger = logging.getLogger("celery")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
celery_logger.handlers = [handler]  # Replace default handlers
celery_logger.setLevel(logging.INFO)  # Set Celery logger to INFO level
celery_logger.propagate = False  # Prevent Celery logs from being handled by the root logger

# Configure Celery with Redis
REDIS_URL = os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379')
if not REDIS_URL:
    raise ValueError("REDIS_URL environment variable is not set")

celery_app = Celery(
    'tasks',
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Optimize Celery configuration to reduce memory usage
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,  # Don't store task results to save memory
    worker_prefetch_multiplier=1,  # Reduce task prefetching to minimize memory usage
    task_acks_late=True,  # Acknowledge tasks after completion to prevent memory buildup
    worker_concurrency=int(os.getenv("CELERY_CONCURRENCY", 2)),  # Adjust based on CPU count; default to 2 for small instances
)

# Initialize Twilio client once at the module level to avoid creating new instances per task
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
    logger.error("❌ Missing Twilio environment variables")
    raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_NUMBER must be set")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Server URL for emitting events (adjust based on your deployment)
SERVER_URL = os.getenv("SERVER_URL", "https://hotel-chatbot-1qj5.onrender.com/")

@celery_app.task
def send_whatsapp_message_task(to_number, message, convo_id=None, username=None, chat_id=None):
    # Move the import inside the function to avoid circular import
    from chat_server import get_db_connection, release_db_connection

    try:
        if not to_number.startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"
        if not TWILIO_WHATSAPP_NUMBER.startswith("whatsapp:"):
            logger.error(f"❌ TWILIO_WHATSAPP_NUMBER must start with 'whatsapp:': {TWILIO_WHATSAPP_NUMBER}")
            return False

        message_obj = twilio_client.messages.create(
            body=message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number
        )
        logger.info(f"✅ Sent WhatsApp message to {to_number}: {message_obj.sid}")

        # If this task was called from process_whatsapp_message, log the AI response and notify the server
        if convo_id and username and chat_id:
            with get_db_connection() as conn:
                try:
                    c = conn.cursor()
                    # Log the AI message
                    ai_timestamp = datetime.now(timezone.utc).isoformat()
                    c.execute(
                        "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (convo_id, username, message, "ai", ai_timestamp)
                    )
                    # Update the conversation's last_updated timestamp
                    c.execute(
                        "UPDATE conversations SET last_updated = %s WHERE id = %s",
                        (ai_timestamp, convo_id)
                    )
                    conn.commit()
                finally:
                    release_db_connection(conn)

            # Notify the Flask-SocketIO server to emit the new_message event
            try:
                response = requests.post(
                    f"{SERVER_URL}/emit_new_message",
                    json={
                        "convo_id": str(convo_id),
                        "message": message,
                        "sender": "ai",
                        "channel": "whatsapp",
                        "timestamp": ai_timestamp,
                        "chat_id": chat_id,
                        "username": username
                    },
                    timeout=5
                )
                response.raise_for_status()
                logger.info(f"✅ Notified server to emit new_message for convo_id {convo_id}")
            except requests.RequestException as e:
                logger.error(f"❌ Failed to notify server for new_message: {str(e)}")

        return True
    except TwilioRestException as e:
        logger.error(f"❌ Failed to send WhatsApp message to {to_number}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp message to {to_number}: {str(e)}")
        return False

@celery_app.task
def process_whatsapp_message(from_number, chat_id, message_body, user_timestamp):
    from chat_server import get_db_connection, release_db_connection, ai_respond, logger, get_ai_enabled
    from openai import RateLimitError, APIError
    import asyncio

    try:
        # Get or create conversation
        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT id, username, ai_enabled, needs_agent FROM conversations WHERE chat_id = %s AND channel = %s",
                    (chat_id, "whatsapp")
                )
                result = c.fetchone()
                current_timestamp = user_timestamp
                if result:
                    convo_id, username, ai_enabled, needs_agent = result
                    c.execute(
                        "UPDATE conversations SET last_updated = %s WHERE id = %s",
                        (current_timestamp, convo_id)
                    )
                else:
                    username = chat_id
                    ai_enabled = 1
                    needs_agent = 0
                    c.execute(
                        "INSERT INTO conversations (chat_id, channel, username, ai_enabled, needs_agent, last_updated) "
                        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        (chat_id, "whatsapp", username, ai_enabled, needs_agent, current_timestamp)
                    )
                    convo_id = c.fetchone()[0]
                conn.commit()
            finally:
                release_db_connection(conn)

        # Log user message
        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (convo_id, username, message_body, "user", user_timestamp)
                )
                conn.commit()
            finally:
                release_db_connection(conn)

        # Determine language and check for help keywords
        language = "en" if message_body.strip().upper().startswith("EN ") else "es"
        help_triggered = "HELP" in message_body.upper() or "AYUDA" in message_body.upper()
        logger.info(f"Detected language: {language}, Help triggered: {help_triggered}")

        # Check AI settings and toggle timestamp using cache
        global_ai_enabled, ai_toggle_timestamp = get_ai_enabled()
        logger.info(f"Global AI enabled: {global_ai_enabled}, Toggle timestamp: {ai_toggle_timestamp}")

        message_time = datetime.fromisoformat(user_timestamp.replace("Z", "+00:00"))
        toggle_time = datetime.fromisoformat(ai_toggle_timestamp.replace("Z", "+00:00"))

        # Generate AI response if conditions are met
        should_respond = (
            ai_enabled and
            global_ai_enabled == "1" and
            not help_triggered and
            (global_ai_enabled != "1" or message_time > toggle_time)
        )
        logger.info(f"Should respond with AI: {should_respond}")

        response = None
        if should_respond:
            try:
                # Create a new event loop for this task
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    response = loop.run_until_complete(ai_respond(message_body, convo_id))
                    logger.info(f"AI response generated: {response}")
                finally:
                    loop.close()
            except RateLimitError as e:
                logger.error(f"❌ OpenAI RateLimitError in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble processing your request right now due to rate limits. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para procesar tu solicitud ahora mismo debido a límites de tasa. Te conectaré con un miembro del equipo para que te ayude."
                )
                # Set needs_agent to 1 since AI failed
                with get_db_connection() as conn:
                    try:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                            (datetime.now(timezone.utc).isoformat(), convo_id)
                        )
                        conn.commit()
                    finally:
                        release_db_connection(conn)
                # Notify the server to refresh conversations
                try:
                    requests.post(
                        f"{SERVER_URL}/refresh_conversations",
                        json={"conversation_id": str(convo_id)},
                        timeout=5
                    )
                except requests.RequestException as e:
                    logger.error(f"❌ Failed to notify server to refresh conversations: {str(e)}")
            except APIError as e:
                logger.error(f"❌ OpenAI APIError in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble processing your request right now due to an API error. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para procesar tu solicitud ahora mismo debido a un error de API. Te conectaré con un miembro del equipo para que te ayude."
                )
                # Set needs_agent to 1 since AI failed
                with get_db_connection() as conn:
                    try:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                            (datetime.now(timezone.utc).isoformat(), convo_id)
                        )
                        conn.commit()
                    finally:
                        release_db_connection(conn)
                # Notify the server to refresh conversations
                try:
                    requests.post(
                        f"{SERVER_URL}/refresh_conversations",
                        json={"conversation_id": str(convo_id)},
                        timeout=5
                    )
                except requests.RequestException as e:
                    logger.error(f"❌ Failed to notify server to refresh conversations: {str(e)}")
            except Exception as e:
                logger.error(f"❌ Unexpected error in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
                )
                # Set needs_agent to 1 since AI failed
                with get_db_connection() as conn:
                    try:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                            (datetime.now(timezone.utc).isoformat(), convo_id)
                        )
                        conn.commit()
                    finally:
                        release_db_connection(conn)
                # Notify the server to refresh conversations
                try:
                    requests.post(
                        f"{SERVER_URL}/refresh_conversations",
                        json={"conversation_id": str(convo_id)},
                        timeout=5
                    )
                except requests.RequestException as e:
                    logger.error(f"❌ Failed to notify server to refresh conversations: {str(e)}")
        elif help_triggered:
            response = (
                "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you."
                if language == "en"
                else "Lo siento, no pude procesar eso. Te conectaré con un miembro del equipo para que te ayude."
            )
            with get_db_connection() as conn:
                try:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE conversations SET ai_enabled = %s, needs_agent = %s, last_updated = %s WHERE id = %s",
                        (0, 1, datetime.now(timezone.utc).isoformat(), convo_id)
                    )
                    conn.commit()
                    logger.info(f"Disabled AI and set needs_agent for convo_id {convo_id} due to help request")
                finally:
                    release_db_connection(conn)
            # Notify the server to refresh conversations
            try:
                requests.post(
                    f"{SERVER_URL}/refresh_conversations",
                    json={"conversation_id": str(convo_id)},
                    timeout=5
                )
            except requests.RequestException as e:
                logger.error(f"❌ Failed to notify server to refresh conversations: {str(e)}")
        else:
            logger.info(f"AI response skipped for convo_id {convo_id}")

        # Send the AI response via WhatsApp if there is a response
        if response:
            send_whatsapp_message_task.delay(from_number, response, convo_id=convo_id, username=username, chat_id=chat_id)
            logger.info(f"Queued send_whatsapp_message_task for AI response to {from_number}")

        # Notify the Flask-SocketIO server to emit the new_message event for the user's message
        # This is moved after the AI response to ensure the user gets a reply even if the notification fails
        try:
            response = requests.post(
                f"{SERVER_URL}/emit_new_message",
                json={
                    "convo_id": str(convo_id),
                    "message": message_body,
                    "sender": "user",
                    "channel": "whatsapp",
                    "timestamp": user_timestamp,
                    "chat_id": chat_id,
                    "username": username
                },
                timeout=5
            )
            response.raise_for_status()
            logger.info(f"✅ Notified server to emit new_message for user message in convo_id {convo_id}")
        except requests.RequestException as e:
            logger.error(f"❌ Failed to notify server for user new_message: {str(e)}")

        # If an AI response was generated, notify the server about the AI's message
        if response:
            try:
                ai_timestamp = datetime.now(timezone.utc).isoformat()
                response = requests.post(
                    f"{SERVER_URL}/emit_new_message",
                    json={
                        "convo_id": str(convo_id),
                        "message": response,
                        "sender": "ai",
                        "channel": "whatsapp",
                        "timestamp": ai_timestamp,
                        "chat_id": chat_id,
                        "username": username
                    },
                    timeout=5
                )
                response.raise_for_status()
                logger.info(f"✅ Notified server to emit new_message for AI response in convo_id {convo_id}")
            except requests.RequestException as e:
                logger.error(f"❌ Failed to notify server for AI new_message: {str(e)}")

    except Exception as e:
        logger.error(f"❌ Error in process_whatsapp_message task: {str(e)}", exc_info=True)
