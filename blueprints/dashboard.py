# blueprints/dashboard.py
from flask import Blueprint, render_template, jsonify, session
from functools import wraps
import logging
from app import get_db_connection  # Absolute import
from psycopg2.extras import DictCursor

# Create the dashboard blueprint
dashboard_bp = Blueprint('dashboard', __name__, template_folder='templates')
logger = logging.getLogger(__name__)

# Define the login_required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

# Define the dashboard route using the blueprint
@dashboard_bp.route('/dashboard/')
@login_required
def dashboard():
    try:
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=DictCursor)
            c.execute("""
                SELECT c.id, c.username, c.phone_number, c.channel, c.status, a.username as agent_username
                FROM conversations c
                LEFT JOIN agents a ON c.assigned_agent = a.id
                WHERE c.visible_in_conversations = 1
                ORDER BY c.last_message_timestamp DESC
            """)
            conversations = c.fetchall()
            formatted_conversations = [
                {
                    "id": convo["id"],
                    "username": convo["username"],
                    "phone_number": convo["phone_number"],
                    "channel": convo["channel"],
                    "status": convo["status"],
                    "agent_username": convo["agent_username"]
                } for convo in conversations
            ]
        return render_template('dashboard.html', conversations=formatted_conversations)
    except Exception as e:
        logger.error(f"❌ Error fetching conversations for dashboard: {e}")
        return jsonify({"error": "Failed to fetch conversations"}), 500
