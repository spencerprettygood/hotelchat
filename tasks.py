from celery import Celery
import os

# Configure Celery with Redis
celery_app = Celery(
    'tasks',
    broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('REDIS_URL', 'redis://localhost:6379/0')
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

@celery_app.task
def send_whatsapp_message_task(to_number, message):
    from chat_server import client, logger
    try:
        message = client.messages.create(
            body=message,
            from_=f"whatsapp:{os.getenv('TWILIO_PHONE_NUMBER')}",
            to=f"whatsapp:{to_number}"
        )
        logger.info(f"Sent WhatsApp message to {to_number}: {message.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message to {to_number}: {str(e)}")
        return False

@celery_app.task
def process_whatsapp_message(from_number, chat_id, message_body, user_timestamp):
    from chat_server import app, get_db_connection, release_db_connection, ai_respond, send_whatsapp_message, log_message, socketio, logger, get_ai_enabled
    from datetime import datetime, timezone

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

        # Check AI settings and toggle timestamp using cache
        global_ai_enabled, ai_toggle_timestamp = get_ai_enabled()

        message_time = datetime.fromisoformat(user_timestamp.replace("Z", "+00:00"))
        toggle_time = datetime.fromisoformat(ai_toggle_timestamp.replace("Z", "+00:00"))

        # Generate AI response if conditions are met
        should_respond = (
            ai_enabled and
            global_ai_enabled == "1" and
            not help_triggered and
            (global_ai_enabled != "1" or message_time > toggle_time)
        )

        if should_respond:
            response = ai_respond(message_body, convo_id)
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

        # Send and log AI response
        task = send_whatsapp_message(from_number, response)  # Returns AsyncResult
        if task.get():  # Wait for the task to complete (this is fine in a Celery task)
            ai_timestamp = log_message(convo_id, username, response, "ai")
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
                "message": response,
                "sender": "ai",
                "channel": "whatsapp",
                "timestamp": ai_timestamp
            }, room=str(convo_id))
            socketio.emit("live_message", {
                "convo_id": convo_id,
                "message": response,
                "sender": "ai",
                "chat_id": chat_id,
                "username": username,
                "timestamp": ai_timestamp
            })
            logger.info(f"Emitted live_message for AI response: {response}")
        else:
            logger.error(f"Failed to send AI response to WhatsApp for chat_id {chat_id}")

    except Exception as e:
        logger.error(f"Error in process_whatsapp_message task: {str(e)}")
