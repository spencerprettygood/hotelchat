from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from flask_socketio import emit
from app import socketio, get_db_connection
import logging
from psycopg2.extras import DictCursor

# Create the live_messages blueprint
live_messages_bp = Blueprint('live_messages', __name__, template_folder='templates')
logger = logging.getLogger(__name__)

# Define the login_required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

@live_messages_bp.route('/')
@login_required
def live_messages_page():
    try:
        return render_template("live-messages.html")
    except Exception as e:
        logger.error(f"❌ Error rendering live-messages page: {e}")
        return jsonify({"error": "Failed to load live-messages page"}), 500

@live_messages_bp.route('/live-messages/')
@login_required
def live_messages():
    return render_template('live-messages.html')

@live_messages_bp.route('/live-messages/all-whatsapp-messages')
@login_required
def all_whatsapp_messages():
    try:
        logger.info("ℹ️ Attempting to connect to database")
        with get_db_connection() as conn:
            logger.info("ℹ️ Successfully connected to database")
            c = conn.cursor(cursor_factory=DictCursor)
            logger.info("ℹ️ Fetching conversations from database")
            c.execute("""
                SELECT conversation_id, username, phone_number, channel
                FROM conversations
                WHERE channel = 'whatsapp'
                ORDER BY last_message_timestamp DESC
            """)
            conversations = c.fetchall()
            logger.info(f"ℹ️ Found {len(conversations)} conversations")
            formatted_conversations = []
            for convo in conversations:
                logger.info(f"ℹ️ Fetching latest message for conversation_id: {convo['conversation_id']}")
                c.execute("""
                    SELECT message, sender, timestamp
                    FROM messages
                    WHERE convo_id = %s
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, (convo["conversation_id"],))
                message = c.fetchone()
                formatted_conversations.append({
                    "convo_id": convo["conversation_id"],
                    "username": convo["username"],
                    "chat_id": convo["phone_number"],
                    "channel": convo["channel"],
                    "messages": [{
                        "message": message["message"],
                        "sender": message["sender"],
                        "timestamp": message["timestamp"].isoformat()
                    }] if message else []
                })
        logger.info("✅ Successfully fetched conversations")
        return jsonify({"conversations": formatted_conversations})
    except Exception as e:
        logger.error(f"❌ Error fetching WhatsApp messages: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to fetch WhatsApp messages"}), 500

@live_messages_bp.route('/live-messages/messages')
@login_required
def messages():
    try:
        conversation_id = request.args.get("conversation_id")
        if not conversation_id:
            logger.error("❌ Missing conversation_id in /live-messages/messages request")
            return jsonify({"error": "Missing conversation_id"}), 400
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=DictCursor)
            c.execute("SELECT message, sender, timestamp FROM messages WHERE convo_id = %s ORDER BY timestamp ASC",
                      (conversation_id,))
            messages = c.fetchall()
        formatted_messages = [
            {
                "message": msg["message"],
                "sender": msg["sender"],
                "timestamp": msg["timestamp"].isoformat()
            } for msg in messages
        ]
        return jsonify({"messages": formatted_messages})
    except Exception as e:
        logger.error(f"❌ Error fetching messages: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

@live_messages_bp.route('/live-messages/settings')
@login_required
def get_settings():
    try:
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=DictCursor)
            c.execute("SELECT key, value FROM settings WHERE key = 'ai_enabled'")
            setting = c.fetchone()
        return jsonify({"ai_enabled": setting["value"] if setting else "1"})
    except Exception as e:
        logger.error(f"❌ Error fetching settings: {e}")
        return jsonify({"error": "Failed to fetch settings"}), 500
