from __future__ import absolute_import, unicode_literals
from celery_app import celery_app
from celery import Celery
import os
import psycopg2
from psycopg2.extras import DictCursor
import logging
import requests
import json
from datetime import datetime, timezone
import time
import redis
from twilio.rest import Client
from langdetect import detect, LangDetectException
from openai import RateLimitError, APIError, AuthenticationError, APITimeoutError
import socketio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from ai_helpers import get_ai_response

# Configure logging
logger = logging.getLogger("chat_server")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(funcName)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Redis client for caching
redis_client = redis.Redis.from_url(
    os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    decode_responses=True,
    max_connections=10
)

# Create a SocketIO client for Celery to emit messages
# This client only writes to the message queue and doesn't run a server.
sio = socketio.KombuManager(os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'), write_only=True)

# Twilio client for WhatsApp
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    logger.info("✅ Twilio client initialized")
except Exception as e:
    logger.error(f"❌ Failed to initialize Twilio client: {str(e)}")
    twilio_client = None

# Database connection
def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")
    
    # Add sslmode=require if not present
    if "sslmode" not in database_url:
        database_url += "?sslmode=require"
    
    try:
        conn = psycopg2.connect(
            database_url,
            cursor_factory=DictCursor,
            connect_timeout=10
        )
        logger.info("✅ Database connection established")
        return conn
    except Exception as e:
        logger.error(f"❌ Database connection failed: {str(e)}", exc_info=True)
        raise

# --- DEAD LETTER QUEUE (DLQ) SETUP ---
DLQ_KEY = os.getenv('DLQ_KEY', 'dead_letter_queue')

def send_to_dead_letter_queue(message, reason, correlation_id=None):
    dlq_entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'message': message,
        'reason': reason,
        'correlation_id': correlation_id
    }
    try:
        redis_client.rpush(DLQ_KEY, json.dumps(dlq_entry))
        logger.error(f"[DLQ][CID:{correlation_id}] Message sent to dead letter queue: {reason}")
    except Exception as e:
        logger.critical(f"[DLQ][CID:{correlation_id}] Failed to write to DLQ: {str(e)}")

@celery_app.task(name="tasks.send_whatsapp_message_task", bind=True, max_retries=3, default_retry_delay=60)
def send_whatsapp_message_task(self, to_number, message_body, sender_info="system"):
    """Sends a WhatsApp message via Twilio."""
    correlation_id = self.request.id or "N/A"
    logger.info(f"[CID:{correlation_id}] Sending WhatsApp message to {to_number}")
    if not twilio_client:
        logger.error(f"[CID:{correlation_id}] Twilio client not initialized. Cannot send message.")
        # No retry if client is not configured
        return

    try:
        message = twilio_client.messages.create(
            from_=f'whatsapp:{TWILIO_WHATSAPP_NUMBER}',
            body=message_body,
            to=f'whatsapp:{to_number}'
        )
        logger.info(f"[CID:{correlation_id}] Successfully sent message SID {message.sid} to {to_number}")
    except Exception as e:
        logger.error(f"[CID:{correlation_id}] Failed to send WhatsApp message to {to_number}: {e}", exc_info=True)
        # Retry on failure
        raise self.retry(exc=e)


@celery_app.task(name="tasks.process_incoming_message", bind=True, max_retries=3)
def process_incoming_message(self, from_number, chat_id, message_body, user_timestamp, channel, sid=None):
    """
    Process an incoming message from any channel (WhatsApp, Web).
    Enhanced: error categorization, retry, DLQ, correlation ID, Sentry hook.
    """
    import uuid
    correlation_id = str(uuid.uuid4())
    start_time = time.time()
    logger.info(f"[CID:{correlation_id}] Processing {channel} message from {chat_id}: '{message_body[:50]}...'")
    try:
        conn = None
        try:
            conn = get_db_connection()
            c = conn.cursor()
        except Exception as db_init_err:
            logger.error(f"[CID:{correlation_id}] DB connection failed: {str(db_init_err)}", exc_info=True)
            raise
        # Check if conversation exists for this chat_id
        c.execute(
            "SELECT id, username, ai_enabled, language FROM conversations WHERE chat_id = %s AND channel = %s",
            (chat_id, channel)
        )
        conversation = c.fetchone()
        
        if conversation:
            convo_id = conversation['id'] # type: ignore
            username = conversation['username'] # type: ignore
            ai_enabled = conversation['ai_enabled'] # type: ignore
            language = conversation['language'] # type: ignore
            logger.info(f"Found existing conversation for {chat_id}: ID {convo_id}, user '{username}'")
        else:
            # Create a new conversation
            username = f"{channel.capitalize()}_{chat_id}"
            
            # Try to detect language (default to English if detection fails)
            try:
                language = detect(message_body)
                logger.info(f"Detected language for new conversation: {language}")
            except LangDetectException:
                language = 'en'
                logger.warning(f"Could not detect language for message '{message_body[:30]}...'. Using default: {language}")
            
            # Insert new conversation
            c.execute(
                "INSERT INTO conversations (username, chat_id, channel, ai_enabled, language, last_updated) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (username, chat_id, channel, 1, language, user_timestamp)
            )
            convo_id = c.fetchone()['id'] # type: ignore
            ai_enabled = 1
            logger.info(f"Created new conversation for {chat_id}: ID {convo_id}, language: {language}")

            # For new web chats, notify the client of its new convo_id and chat_id
            if channel == 'web' and sid:
                try:
                    sio.emit('session_assigned', {
                        'convo_id': convo_id,
                        'chat_id': chat_id
                    }, to=sid)
                    logger.info(f"[CID:{correlation_id}] Emitted 'session_assigned' to sid {sid} with convo_id {convo_id}")
                except Exception as e:
                    logger.error(f"[CID:{correlation_id}] Failed to emit 'session_assigned' to sid {sid}: {e}")

        # Log the user message
        c.execute(
            "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (convo_id, username, message_body, "user", user_timestamp)
        )
        message_id = c.fetchone()['id'] # type: ignore
        logger.info(f"Logged user message with ID {message_id} for convo_id {convo_id}")
        
        # Update conversation timestamp
        c.execute(
            "UPDATE conversations SET last_updated = %s WHERE id = %s",
            (user_timestamp, convo_id)
        )
        conn.commit()
        
        # Get conversation history for AI context
        c.execute(
            "SELECT message, sender, timestamp FROM messages "
            "WHERE convo_id = %s ORDER BY timestamp ASC",
            (convo_id,)
        )
        history = c.fetchall()
        
        # Format conversation history for OpenAI
        conversation_history = []
        for msg in history:
            role = "user" if msg['sender'] == "user" else "assistant" # type: ignore
            conversation_history.append({"role": role, "content": msg['message']}) # type: ignore
        
        # Check if AI is globally enabled
        c.execute("SELECT value FROM settings WHERE key = %s", ("ai_enabled",))
        setting = c.fetchone()
        global_ai_enabled = setting['value'] if setting else "1" # type: ignore
        
        # Process with AI if enabled
        if global_ai_enabled == "1" and ai_enabled == 1:
            logger.info(f"[CID:{correlation_id}] AI is enabled for conversation {convo_id}. Generating response...")
            try:
                # The new get_ai_response function has a simpler signature
                ai_reply = get_ai_response(
                    prompt=message_body,
                    conversation_history=conversation_history
                )
            except Exception as ai_err:
                logger.error(f"[CID:{correlation_id}] AI response failed: {str(ai_err)}", exc_info=True)
                send_to_dead_letter_queue({
                    'from_number': from_number,
                    'chat_id': chat_id,
                    'message_body': message_body,
                    'user_timestamp': user_timestamp
                }, reason=f"AI error: {str(ai_err)}", correlation_id=correlation_id)
                # Sentry/monitoring hook
                try:
                    # sentry_sdk.capture_exception(ai_err)
                    pass
                except Exception:
                    pass
                raise
            # Log AI response
            if ai_reply:
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (convo_id, "AI Bot", ai_reply, "bot", datetime.now(timezone.utc).isoformat())
                )
                ai_message_id = c.fetchone()['id'] # type: ignore
                logger.info(f"Logged AI response with ID {ai_message_id} for convo_id {convo_id}")
                
                # Send AI response via the appropriate channel
                if channel == 'whatsapp':
                    send_whatsapp_message_task.delay(
                        to_number=chat_id,
                        message_body=ai_reply
                    )
                elif channel == 'web':
                    # For web, we emit a socketio event back to the client
                    try:
                        room = f"convo_{convo_id}"
                        sio.emit('new_message', {
                            'id': ai_message_id,
                            'convo_id': convo_id,
                            'username': 'AI Bot',
                            'message': ai_reply,
                            'sender': 'bot',
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                            'chat_id': chat_id,
                            'channel': channel
                        }, room=room) # Corrected: use room= instead of to=
                        logger.info(f"Emitted AI response via Socket.IO to room {room}")
                    except Exception as e:
                        logger.error(f"Failed to emit Socket.IO event for AI reply: {str(e)}")


                # Handoff logic is removed as it's not part of the simplified get_ai_response
                # if handoff_triggered:
                #     c.execute(
                #         "UPDATE conversations SET needs_agent = 1, booking_intent = %s WHERE id = %s",
                #         (detected_intent, convo_id)
                #     )
                #     logger.info(f"Updated conversation {convo_id} with handoff flag and intent: {detected_intent}")
            else:
                logger.error(f"No AI response generated for convo_id {convo_id}")
                
        conn.commit()
        
        # Emit to Socket.IO that a new message arrived (for dashboard)
        try:
            # Emit to Socket.IO using the write-only KombuManager
            room = f"convo_{convo_id}"
            sio.emit('new_message', {
                'id': message_id,
                'convo_id': convo_id,
                'username': username,
                'message': message_body,
                'sender': 'user',
                'timestamp': user_timestamp,
                'chat_id': chat_id,
                'channel': channel
            }, room=room) # Corrected: use room= instead of to=
            logger.info(f"Emitted Socket.IO 'new_message' event to room {room}")
        except Exception as e:
            logger.error(f"Failed to emit Socket.IO event: {str(e)}")
        
        processing_time = time.time() - start_time
        logger.info(f"[CID:{correlation_id}] {channel.capitalize()} message processed in {processing_time:.2f} seconds")
        return {"status": "success", "convo_id": convo_id, "processing_time": processing_time}
    except psycopg2.OperationalError as db_op_err:
        logger.error(f"[CID:{correlation_id}] Database operational error: {str(db_op_err)}", exc_info=True)
        try:
            send_to_dead_letter_queue({
                'from_number': from_number,
                'chat_id': chat_id,
                'message_body': message_body,
                'user_timestamp': user_timestamp
            }, reason=f"DB operational error: {str(db_op_err)}", correlation_id=correlation_id)
        except Exception:
            pass
        raise self.retry(exc=db_op_err, countdown=60, max_retries=3)
    except redis.ConnectionError as redis_err:
        logger.error(f"[CID:{correlation_id}] Redis connection error: {str(redis_err)}", exc_info=True)
        try:
            send_to_dead_letter_queue({
                'from_number': from_number,
                'chat_id': chat_id,
                'message_body': message_body,
                'user_timestamp': user_timestamp
            }, reason=f"Redis error: {str(redis_err)}", correlation_id=correlation_id)
        except Exception:
            pass
        raise self.retry(exc=redis_err, countdown=60, max_retries=3)
    except Exception as e:
        logger.error(f"[CID:{correlation_id}] ❌ Error processing {channel} message: {str(e)}", exc_info=True)
        try:
            send_to_dead_letter_queue({
                'from_number': from_number,
                'chat_id': chat_id,
                'message_body': message_body,
                'user_timestamp': user_timestamp
            }, reason=f"General error: {str(e)}", correlation_id=correlation_id)
        except Exception:
            pass
        # Sentry/monitoring hook
        try:
            # sentry_sdk.capture_exception(e)
            pass
        except Exception:
            pass
        raise self.retry(exc=e, countdown=120, max_retries=3)
