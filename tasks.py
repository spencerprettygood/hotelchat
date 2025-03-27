import os
import sys
import logging
from pathlib import Path
from celery import Celery
from datetime import datetime, timezone
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import asyncio

# Add the project root directory to the Python path to fix import issues
project_root = str(Path(__file__).parent.absolute())
if project_root not in sys.path:
    sys.path.append(project_root)

# Configure logging for Celery to preserve application log levels
logger = logging.getLogger("chat_server")
celery_logger = logging.getLogger("celery")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
celery_logger.handlers = [handler]
celery_logger.setLevel(logging.INFO)
celery_logger.propagate = False

# Configure Celery with Redis
REDIS_URL = os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379')
if not REDIS_URL:
    raise ValueError("REDIS_URL environment variable is not set")

celery_app = Celery(
    'tasks',
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Optimize Celery configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_concurrency=int(os.getenv("CELERY_CONCURRENCY", 9)),  # Adjust based on CPU cores
    task_routes={
        'tasks.send_whatsapp_message_task': {'queue': 'whatsapp'},
        'tasks.process_whatsapp_message': {'queue': 'default'},
    },
)

# Initialize Twilio client
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
    logger.error("❌ Missing Twilio environment variables")
    raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_NUMBER must be set")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Use a single event loop for synchronous async operations
_sync_loop = None

def get_sync_loop():
    global _sync_loop
    if _sync_loop is None or _sync_loop.is_closed():
        _sync_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_sync_loop)
    return _sync_loop

@celery_app.task
def send_whatsapp_message_task(to_number, message, convo_id=None, username=None, chat_id=None, ai_timestamp=None):
    from chat_server import get_db_connection, release_db_connection, socketio

    try:
        if not to_number.startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"
        if not TWILIO_WHATSAPP_NUMBER.startswith("whatsapp:"):
            logger.error(f"❌ TWILIO_WHATSAPP_NUMBER must start with 'whatsapp:': {TWILIO_WHATSAPP_NUMBER}")
            return False

        logger.info(f"Attempting to send WhatsApp message to {to_number} from {TWILIO_WHATSAPP_NUMBER}: {message}")
        message_obj = twilio_client.messages.create(
            body=message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number
        )
        logger.info(f"✅ Sent WhatsApp message to {to_number}: SID={message_obj.sid}, Status={message_obj.status}")

        # If this task was called from process_whatsapp_message, log the AI response and emit the event
        if convo_id and username and chat_id and ai_timestamp:
            with get_db_connection() as conn:
                try:
                    c = conn.cursor()
                    c.execute("BEGIN")
                    # Log the AI message
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
                    c.execute("COMMIT")
                finally:
                    release_db_connection(conn)

            # Emit the new_message event directly using SocketIO
            room = f"conversation_{convo_id}"
            socketio.emit("new_message", {
                "convo_id": convo_id,
                "message": message,
                "sender": "ai",
                "timestamp": ai_timestamp,
                "chat_id": chat_id,
                "username": username
            }, room=room)
            logger.info(f"Emitted new_message event for AI response in room {room}")

        return True
    except TwilioRestException as e:
        logger.error(f"❌ Failed to send WhatsApp message to {to_number}: Code={e.code}, Message={e.msg}, Status={e.status}")
        return False
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp message to {to_number}: {str(e)}")
        return False

@celery_app.task
def process_whatsapp_message(from_number, chat_id, message_body, user_timestamp):
    from chat_server import get_db_connection, release_db_connection, ai_respond_sync, get_ai_enabled, detect_language, socketio
    from openai import RateLimitError, APIError, AuthenticationError, APITimeoutError

    start_time = time.time()
    logger.info(f"Starting process_whatsapp_message for chat_id {chat_id}: {message_body}")
    try:
        # Get or create conversation and log user message in a single transaction
        convo_id = None
        username = None
        ai_enabled = None
        needs_agent = None
        assigned_agent = None
        language = "en"

        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute("BEGIN")
                # Get or create conversation
                c.execute(
                    "SELECT id, username, ai_enabled, needs_agent, assigned_agent FROM conversations WHERE chat_id = %s AND channel = %s",
                    (chat_id, "whatsapp")
                )
                result = c.fetchone()
                current_timestamp = user_timestamp
                if result:
                    convo_id, username, ai_enabled, needs_agent, assigned_agent = result['id'], result['username'], result['ai_enabled'], result['needs_agent'], result['assigned_agent']
                    c.execute(
                        "UPDATE conversations SET last_updated = %s, visible_in_conversations = 1 WHERE id = %s",
                        (current_timestamp, convo_id)
                    )
                else:
                    username = f"User_{chat_id[-4:]}"
                    ai_enabled = 1
                    needs_agent = 0
                    assigned_agent = None
                    c.execute(
                        "INSERT INTO conversations (chat_id, channel, username, ai_enabled, needs_agent, last_updated, visible_in_conversations) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (chat_id, "whatsapp", username, ai_enabled, needs_agent, current_timestamp, 1)
                    )
                    convo_id = c.fetchone()['id']
                # Log user message
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (convo_id, username, message_body, "user", user_timestamp)
                )
                c.execute("COMMIT")
            finally:
                release_db_connection(conn)

        # Emit the user's message to the conversation room
        room = f"conversation_{convo_id}"
        socketio.emit("new_message", {
            "convo_id": convo_id,
            "message": message_body,
            "sender": "user",
            "timestamp": user_timestamp,
            "chat_id": chat_id,
            "username": username
        }, room=room)
        logger.info(f"Emitted new_message event for user message in room {room}")

        # Emit live_message to update conversation list
        socketio.emit("live_message", {
            "convo_id": convo_id,
            "chat_id": chat_id,
            "username": username,
            "message": message_body,
            "timestamp": user_timestamp
        })
        logger.info(f"Emitted live_message event for convo_id {convo_id}")

        # Detect language
        language = detect_language(message_body, convo_id)
        logger.info(f"Detected language for convo_id {convo_id}: {language}")

        # Check for help keywords
        help_triggered = "HELP" in message_body.upper() or "AYUDA" in message_body.upper()
        logger.info(f"Help triggered for convo_id {convo_id}: {help_triggered}")

        # Check AI settings
        global_ai_enabled, ai_toggle_timestamp = get_ai_enabled()
        logger.info(f"Global AI enabled: {global_ai_enabled}, Toggle timestamp: {ai_toggle_timestamp}")

        message_time = datetime.fromisoformat(user_timestamp.replace("Z", "+00:00"))
        toggle_time = datetime.fromisoformat(ai_toggle_timestamp.replace("Z", "+00:00"))

        # Determine if AI should respond
        should_respond = (
            ai_enabled and
            global_ai_enabled == "1" and
            not help_triggered and
            not needs_agent and
            assigned_agent is None and
            (global_ai_enabled != "1" or message_time > toggle_time)
        )
        logger.info(f"Should respond with AI for convo_id {convo_id}: {should_respond}, ai_enabled={ai_enabled}, global_ai_enabled={global_ai_enabled}, help_triggered={help_triggered}, needs_agent={needs_agent}, assigned_agent={assigned_agent}")

        response = None
        ai_timestamp = None
        if should_respond:
            try:
                response = ai_respond_sync(message_body, convo_id)
                ai_timestamp = datetime.now(timezone.utc).isoformat()
                logger.info(f"AI response for convo_id {convo_id}: {response}")
            except RateLimitError as e:
                logger.error(f"❌ OpenAI RateLimitError in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble processing your request right now due to rate limits. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para procesar tu solicitud ahora mismo debido a límites de tasa. Te conectaré con un miembro del equipo para que te ayude."
                )
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
                socketio.emit("refresh_conversations", {"conversation_id": convo_id})
            except APIError as e:
                logger.error(f"❌ OpenAI APIError in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble processing your request right now due to an API error. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para procesar tu solicitud ahora mismo debido a un error de API. Te conectaré con un miembro del equipo para que te ayude."
                )
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
                socketio.emit("refresh_conversations", {"conversation_id": convo_id})
            except AuthenticationError as e:
                logger.error(f"❌ OpenAI AuthenticationError in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble authenticating with the AI service. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para autenticarme con el servicio de IA. Te conectaré con un miembro del equipo para que te ayude."
                )
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
                socketio.emit("refresh_conversations", {"conversation_id": convo_id})
            except APITimeoutError as e:
                logger.error(f"❌ OpenAI APITimeoutError in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, the AI service timed out while processing your request. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, el servicio de IA se agotó mientras procesaba tu solicitud. Te conectaré con un miembro del equipo para que te ayude."
                )
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
                socketio.emit("refresh_conversations", {"conversation_id": convo_id})
            except Exception as e:
                logger.error(f"❌ Unexpected error in ai_respond for convo_id {convo_id}: {str(e)}")
                response = (
                    "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you."
                    if language == "en"
                    else "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
                )
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
                socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        elif help_triggered:
            response = (
                "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you."
                if language == "en"
                else "Lo siento, no pude procesar eso. Te conectaré con un miembro del equipo para que te ayude."
            )
            ai_timestamp = datetime.now(timezone.utc).isoformat()
            with get_db_connection() as conn:
                try:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE conversations SET ai_enabled = %s, needs_agent = %s, last_updated = %s WHERE id = %s",
                        (0, 1, ai_timestamp, convo_id)
                    )
                    conn.commit()
                    logger.info(f"Disabled AI and set needs_agent for convo_id {convo_id} due to help request")
                finally:
                    release_db_connection(conn)
            socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        else:
            logger.info(f"AI response skipped for convo_id {convo_id}: ai_enabled={ai_enabled}, global_ai_enabled={global_ai_enabled}, help_triggered={help_triggered}, needs_agent={needs_agent}, assigned_agent={assigned_agent}")
            # Reset conversation state if AI is disabled but shouldn't be
            if not ai_enabled or needs_agent or assigned_agent:
                logger.info(f"Resetting conversation state for convo_id {convo_id} to allow AI response")
                with get_db_connection() as conn:
                    try:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE conversations SET ai_enabled = 1, needs_agent = 0, assigned_agent = NULL, last_updated = %s WHERE id = %s",
                            (current_timestamp, convo_id)
                        )
                        conn.commit()
                    finally:
                        release_db_connection(conn)
            return

        # Send the AI response via WhatsApp
        if response and ai_timestamp:
            send_whatsapp_message_task.delay(
                from_number,
                response,
                convo_id=convo_id,
                username=username,
                chat_id=chat_id,
                ai_timestamp=ai_timestamp
            )
            logger.info(f"Queued send_whatsapp_message_task for AI response to {from_number}")

        logger.info(f"Finished process_whatsapp_message in {time.time() - start_time:.2f} seconds")
    except Exception as e:
        logger.error(f"❌ Error in process_whatsapp_message task for chat_id {chat_id}: {str(e)}", exc_info=True)
        raise
