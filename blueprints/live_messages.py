# blueprints/live_messages.py
from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required  # Use Flask-Login's login_required
import logging
from app import get_db_connection
from psycopg2.extras import DictCursor
from datetime import datetime

# Create the live_messages blueprint
live_messages_bp = Blueprint('live_messages', __name__, template_folder='templates')
logger = logging.getLogger(__name__)

@live_messages_bp.route('/live-messages/')
@login_required  # Use Flask-Login's login_required
def live_messages():
    return render_template('live-messages.html')

@live_messages_bp.route('/live-messages/all-whatsapp-messages')
@login_required
def all_whatsapp_messages():
    try:
        # Test database connection
        conn = get_db_connection()
        if conn is None:
            logger.error("Failed to establish database connection: get_db_connection returned None")
            return jsonify({'error': 'Database connection failed'}), 500

        with conn:
            c = conn.cursor(cursor_factory=DictCursor)
            logger.debug("Executing SQL query to fetch WhatsApp conversations")
            c.execute("""
                SELECT c.id AS convo_id, c.username, c.phone_number, c.channel, c.status, c.last_message_timestamp,
                       m.message, m.sender, m.timestamp
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.convo_id
                WHERE c.channel = 'whatsapp' AND c.visible_in_conversations = 1
                ORDER BY c.last_message_timestamp DESC, m.timestamp ASC
            """)
            rows = c.fetchall()
            logger.debug(f"Fetched {len(rows)} rows from database")

            # Group messages by conversation
            conversations = {}
            for row in rows:
                convo_id = row['convo_id']
                if convo_id not in conversations:
                    conversations[convo_id] = {
                        'convo_id': convo_id,
                        'username': row['username'],
                        'phone_number': row['phone_number'],
                        'channel': row['channel'],
                        'status': row['status'],
                        'last_message_timestamp': row['last_message_timestamp'].isoformat() if row['last_message_timestamp'] else None,
                        'messages': []
                    }
                if row['message']:  # If there is a message (not NULL)
                    conversations[convo_id]['messages'].append({
                        'message': row['message'],
                        'sender': row['sender'],
                        'timestamp': row['timestamp'].isoformat() if row['timestamp'] else None
                    })

            # Convert to list for JSON response
            conversation_list = list(conversations.values())
            logger.debug(f"Returning {len(conversation_list)} conversations")
            return jsonify({'conversations': conversation_list})

    except Exception as e:
        logger.error(f"Error fetching all WhatsApp messages: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to fetch conversations: {str(e)}'}), 500

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

@live_messages_bp.route('/live-messages/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'GET':
        try:
            with get_db_connection() as conn:
                c = conn.cursor(cursor_factory=DictCursor)
                c.execute("SELECT key, value FROM settings WHERE key = 'ai_enabled'")
                setting = c.fetchone()
            return jsonify({"ai_enabled": setting["value"] if setting else "1"})
        except Exception as e:
            logger.error(f"❌ Error fetching settings: {e}")
            return jsonify({"error": "Failed to fetch settings"}), 500
    else:  # POST
        try:
            data = request.get_json()
            ai_enabled = data.get('ai_enabled')
            if ai_enabled not in ['0', '1']:
                return jsonify({"error": "Invalid value for ai_enabled"}), 400
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("INSERT INTO settings (key, value) VALUES ('ai_enabled', %s) ON CONFLICT (key) DO UPDATE SET value = %s",
                          (ai_enabled, ai_enabled))
                conn.commit()
            socketio.emit('settings_updated', {'ai_enabled': ai_enabled})
            return jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"❌ Error updating settings: {e}")
            return jsonify({"error": "Failed to update settings"}), 500
