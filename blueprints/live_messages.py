from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from flask_socketio import emit
from app import socketio, get_db_connection
import logging

# Set up logging
logger = logging.getLogger(__name__)

live_messages_bp = Blueprint('live_messages', __name__)

@live_messages_bp.route('/')
@login_required
def live_messages_page():
    try:
        return render_template("live-messages.html")
    except Exception as e:
        logger.error(f"❌ Error rendering live-messages page: {e}")
        return jsonify({"error": "Failed to load live-messages page"}), 500

@live_messages_bp.route('/all-whatsapp-messages')
@login_required
def all_whatsapp_messages():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, username, chat_id, channel FROM conversations WHERE channel = 'whatsapp'")
            conversations = [{"convo_id": row["id"], "username": row["username"], "chat_id": row["chat_id"], "channel": row["channel"]} for row in c.fetchall()]
            for convo in conversations:
                c.execute("SELECT message, sender, timestamp FROM messages WHERE convo_id = %s ORDER BY timestamp", (convo["convo_id"],))
                convo["messages"] = [{"message": row["message"], "sender": row["sender"], "timestamp": row["timestamp"]} for msg in c.fetchall()]
        return jsonify({"conversations": conversations})
    except Exception as e:
        logger.error(f"❌ Error fetching WhatsApp messages: {e}")
        return jsonify({"error": "Failed to fetch WhatsApp messages"}), 500

@live_messages_bp.route('/messages', methods=["GET"])
@login_required
def get_messages():
    try:
        convo_id = request.args.get("conversation_id")
        if not convo_id:
            logger.error("❌ Missing conversation ID in /live-messages/messages request")
            return jsonify({"error": "Missing conversation ID"}), 400
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                return jsonify({"error": "Conversation not found"}), 404
            username = result['username']
            chat_id = result['chat_id']
            c.execute("""
                SELECT sender, message, timestamp
                FROM messages
                WHERE convo_id = %s
                ORDER BY timestamp ASC
            """, (convo_id,))
            messages = [
                {
                    "sender": row['sender'],
                    "message": row['message'],
                    "timestamp": row['timestamp']
                }
                for row in c.fetchall()
            ]
            logger.info(f"✅ Fetched {len(messages)} messages for convo_id {convo_id}")
            return jsonify({"messages": messages, "username": username, "chat_id": chat_id})
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo_id {convo_id}: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

@live_messages_bp.route('/settings', methods=["GET", "POST"])
@login_required
def settings():
    try:
        if request.method == "POST":
            data = request.get_json()
            logger.info(f"ℹ️ Received /live-messages/settings POST request with data: {data}")
            ai_enabled = data.get("ai_enabled")
            if ai_enabled is None:
                logger.error("❌ Missing ai_enabled in /live-messages/settings POST request")
                return jsonify({"error": "Missing ai_enabled parameter"}), 400
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s",
                          ("ai_enabled", ai_enabled, ai_enabled))
                conn.commit()
            socketio.emit("settings_updated", {"ai_enabled": ai_enabled})
            logger.info(f"✅ Updated settings: ai_enabled = {ai_enabled}")
            return jsonify({"status": "success"})
        else:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
                result = c.fetchone()
                ai_enabled = result['value'] if result else '1'
            return jsonify({"ai_enabled": ai_enabled})
    except Exception as e:
        logger.error(f"❌ Error in /live-messages/settings endpoint: {e}")
        return jsonify({"error": "Failed to access settings"}), 500
