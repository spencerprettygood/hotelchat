# tasks.py

import os
import sys
import logging
from pathlib import Path
from celery import Celery, chain
from datetime import datetime, timezone
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

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

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_connection_retry_on_startup=True  # Suppresses deprecation warning
)

@celery_app.task
def send_whatsapp_message_task(to_number, message, convo_id=None, username=None, chat_id=None):
    # Move the import inside the function to avoid circular import
    from chat_server import logger, socketio, get_db_connection, release_db_connection, log_message

    try:
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        if not to_number.startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"
        twilio_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        if not twilio_number:
            logger.error("❌ TWILIO_WHATSAPP_NUMBER environment variable is not set")
            return False
        if not twilio_number.startswith("whatsapp:"):
            logger.error(f"❌ TWILIO_WHATSAPP_NUMBER must start with 'whatsapp:': {twilio_number}")
            return False
        message_obj = client.messages.create(
            body=message,
            from_=twilio_number,
            to=to_number
        )
        logger.info(f"✅ Sent WhatsApp message to {to_number}: {message_obj.sid}")

        # If this task was called from process_whatsapp_message, log and emit the AI response
        if convo_id and username and chat_id:
            ai_timestamp = log_message(convo_id, username, message, "ai")
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute(
                    "UPDATE conversations SET last_updated = %s WHERE id = %s",
                    (ai_timestamp, convo_id)
                )
                conn.commit()
                release_db_connection(conn)
            socketio.emit("new_message", {
                "convo_id": convo_id,
                "message": message,
                "sender": "ai",
                "channel": "whatsapp",
                "timestamp": ai_timestamp
            }, room=str(convo_id))
            socketio.emit("live_message", {
                "convo_id": convo_id,
                "message": message,
                "sender": "ai",
                "chat_id": chat_id,
                "username": username,
                "timestamp": ai_timestamp
            })
            logger.info(f"Emitted live_message for AI response: {message}")

        return True
    except TwilioRestException as e:
        logger.error(f"❌ Failed to send WhatsApp message to {to_number}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp message to {to_number}: {str(e)}")
        return False

@celery_app.task
def process_whatsapp_message(from_number, chat_id, message_body, user_timestamp):
    # Move the imports inside the function to avoid circular import
    from chat_server import app, get_db_connection, release_db_connection, ai_respond, socketio, logger, get_ai_enabled

    try:
        # Get or create conversation
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, username, ai_enabled FROM conversations WHERE chat_id = %s AND channel = %s",
                (chat_id, "whatsapp")
            )
            result = c.fetchone()
            current_timestamp = user_timestamp
            if result:
                convo_id, username, ai_enabled = result
                c.execute(
                    "UPDATE conversations SET last_updated = %s WHERE id = %s",
                    (current_timestamp, convo_id)
                )
            else:
                username = chat_id
                ai_enabled = 1
                c.execute(
                    "INSERT INTO conversations (chat_id, channel, username, ai_enabled, last_updated) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (chat_id, "whatsapp", username, ai_enabled, current_timestamp)
                )
                convo_id = c.fetchone()[0]
            conn.commit()
            release_db_connection(conn)

        # Log user message
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                (convo_id, username, message_body, "user", user_timestamp)
            )
            conn.commit()
            release_db_connection(conn)

        socketio.emit("new_message", {
            "convo_id": convo_id,
            "message": message_body,
            "sender": "user",
            "channel": "whatsapp",
            "timestamp": user_timestamp
        }, room=str(convo_id))
        socketio.emit("live_message", {
            "convo_id": convo_id,
            "message": message_body,
            "sender": "user",
            "chat_id": chat_id,
            "username": username,
            "timestamp": user_timestamp
        })
        logger.info(f"Emitted live_message for user message: {message_body}")

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

        if should_respond:
            response = ai_respond(message_body, convo_id)
            logger.info(f"AI response generated: {response}")
        elif help_triggered:
            response = (
                "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you."
                if language == "en"
                else "Lo siento, no pude procesar eso. Te conectaré con un miembro del equipo para que te ayude."
            )
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute(
                    "UPDATE conversations SET ai_enabled = %s WHERE id = %s",
                    (0, convo_id)
                )
                conn.commit()
                release_db_connection(conn)
                logger.info(f"Disabled AI for convo_id {convo_id} due to help request")
        else:
            logger.info(f"AI response skipped for convo_id {convo_id}")
            return

        # Chain the send_whatsapp_message_task to run asynchronously
        chain(
            send_whatsapp_message_task.s(from_number, response, convo_id=convo_id, username=username, chat_id=chat_id)
        )()
        logger.info(f"Chained send_whatsapp_message_task for AI response to {from_number}")

    except Exception as e:
        logger.error(f"❌ Error in process_whatsapp_message task: {str(e)}", exc_info=True)
