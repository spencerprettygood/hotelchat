from dotenv import load_dotenv
load_dotenv()

# Gevent monkey-patching at the very top
import gevent
from gevent import monkey
monkey.patch_all()

# --- ALL IMPORTS AT THE TOP ---
import os
import sys
import json
import logging
import redis
import psycopg2
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, Response, g
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from psycopg2.extras import DictCursor
from psycopg2.pool import SimpleConnectionPool
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from cachetools import TTLCache
import openai
from openai import RateLimitError, APIError, AuthenticationError, APITimeoutError
from concurrent_log_handler import ConcurrentRotatingFileHandler
from langdetect import detect, DetectorFactory
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from functools import wraps
import time

DetectorFactory.seed = 0

# --- ENVIRONMENT VARIABLES & GLOBALS ---
# Validate and load all required environment variables at the very top
REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "SECRET_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_SERVICE_ACCOUNT_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_NUMBER"
]
missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    error_message = f"❌ Missing required environment variables: {', '.join(missing_vars)}"
    print(error_message, file=sys.stderr)
    raise ValueError(error_message)

SECRET_KEY = os.getenv("SECRET_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SERVICE_ACCOUNT_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    error_message = "❌ REDIS_URL environment variable is not set."
    print(error_message, file=sys.stderr)
    raise ValueError(error_message)

# --- LOGGER SETUP ---
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "chat_server.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("chat_server")
logger.setLevel(LOG_LEVEL)
logger.propagate = False
if logger.hasHandlers():
    logger.handlers.clear()
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(funcName)s - %(message)s'
)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
try:
    file_handler = ConcurrentRotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except ImportError:
    logger.warning("concurrent_log_handler not installed; file logging disabled.")
logger.info(f"Logging initialized. Level: {LOG_LEVEL}, File: {LOG_FILE_PATH}")
logger.info(f"Using Redis URL: {REDIS_URL}")

# --- REDIS CLIENT ---
try:
    redis_client = redis.Redis.from_url(REDIS_URL)
    redis_client.ping()
    logger.info("✅ Redis client initialized and connection verified.")
except Exception as e:
    logger.error(f"❌ Redis connection failed: {e}")
    redis_client = None
    raise

# --- DATABASE CONNECTION POOL ---
if DATABASE_URL:
    database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    if "sslmode" not in database_url:
        database_url += "?sslmode=require"
else:
    database_url = None

db_pool = None
if database_url:
    try:
        db_pool = SimpleConnectionPool(
            minconn=1, maxconn=10, dsn=database_url,
            connect_timeout=10, options="-c statement_timeout=10000"
        )
        with db_pool.getconn() as test_conn:
            with test_conn.cursor() as c:
                c.execute("SELECT 1")
        logger.info("✅ PostgreSQL connection pool initialized and connection verified.")
    except Exception as e:
        logger.error(f"❌ PostgreSQL connection pool failed: {e}")
        db_pool = None
        raise
else:
    logger.error("❌ DATABASE_URL is not set. Database connection pool cannot be created.")
    raise ValueError("DATABASE_URL is not set.")


# --- CELERY TASKS ---
try:
    from tasks import process_whatsapp_message, send_whatsapp_message_task
    if not (hasattr(process_whatsapp_message, 'delay') and hasattr(send_whatsapp_message_task, 'delay')):
        logger.error("Celery tasks are not properly decorated. Check @celery_app.task in tasks.py.")
        raise ImportError("Invalid Celery tasks.")
    logger.info("✅ Celery tasks imported successfully.")
except Exception as e:
    logger.error(f"❌ Celery tasks import failed: {e}")
    raise

# --- OPENAI CLIENT CONFIGURATION ---
# Configure OpenAI API key
import openai
openai.api_key = OPENAI_API_KEY
openai_client = openai
logger.info("OpenAI client module configured with API key.")

# --- GOOGLE SERVICE ACCOUNT KEY VALIDATION ---
# The following block is intentionally commented out to allow deployment without a Google service account key.
# try:
#     google_key = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
#     if not google_key:
#         raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")
#     service_account_info = json.loads(google_key)
#     SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
#     credentials = service_account.Credentials.from_service_account_info(
#         service_account_info, scopes=SCOPES
#     )
#     service = build('calendar', 'v3', credentials=credentials)
#     logger.info("✅ Google service account credentials loaded.")
# except Exception as e:
#     logger.error(f"❌ Google service account credentials failed: {e}")
#     raise
service = None  # Mock/fallback: Google Calendar API is not configured

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = SECRET_KEY
CORS(app)

socketio = SocketIO(
    app,
    cors_allowed_origins=["http://localhost:5000", "https://hotel-chatbot-1qj5.onrender.com"],
    async_mode="gevent",
    message_queue=REDIS_URL,
    ping_timeout=60,
    ping_interval=15,
    logger=True,
    engineio_logger=True
)

# --- CACHE SETUP ---
settings_cache = TTLCache(maxsize=10, ttl=300)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # type: ignore

# --- DATABASE HELPERS ---
def get_db_connection():
    """Gets a database connection from the pool."""
    if 'db' not in g:
        try:
            if not db_pool:
                raise Exception("Database connection pool is not initialized.")
            g.db = db_pool.getconn()
            logger.debug("Acquired DB connection from pool.")
        except Exception as e:
            logger.error(f"❌ Failed to get DB connection from pool: {e}")
            raise
    return g.db

@app.teardown_appcontext
def close_db_connection(exception=None):
    """Closes the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        try:
            if db_pool:
                db_pool.putconn(db)
            logger.debug("Released DB connection back to pool.")
        except Exception as e:
            logger.error(f"Error putting connection back to pool: {e}")


# --- SETTINGS HELPERS ---
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(psycopg2.Error)
)
def get_ai_enabled(force_db: bool = False):
    """
    Fetches the global AI enabled flag from cache or database.
    Includes retry logic for database queries.
    """
    if not force_db and 'ai_enabled' in settings_cache:
        cached_value, timestamp = settings_cache['ai_enabled']
        logger.debug(f"AI enabled status '{cached_value}' from cache.")
        return cached_value, timestamp

    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value, last_updated FROM settings WHERE key = 'ai_enabled'")
        setting = c.fetchone()
        if setting:
            value = setting['value']
            timestamp = setting['last_updated']
            settings_cache['ai_enabled'] = (value, timestamp)
            logger.info(f"AI enabled status '{value}' fetched from DB and cached.")
            return value, timestamp
        else:
            # Default to enabled if not set
            logger.warning("AI enabled setting not found in DB, defaulting to '1' (enabled).")
            return "1", None
    except psycopg2.Error as e:
        logger.error(f"Database error fetching AI enabled status: {e}", exc_info=True)
        raise  # Re-raise to trigger tenacity retry
    finally:
        # The teardown context will handle closing the connection
        pass


# --- User class for Flask-Login ---
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        user = c.fetchone()
        if user:
            return User(id=user['id'], username=user['username'])
        return None
    except Exception as e:
        logger.error(f"Failed to load user {user_id}: {str(e)}", exc_info=True)
        return None
    finally:
        # Connection closed by teardown context
        pass

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        if not username:
            return render_template('login.html', error="Username is required")
        
        conn = None
        try:
            conn = get_db_connection()
            c = conn.cursor()
            
            # Check if user exists
            c.execute("SELECT id, username FROM users WHERE username = %s", (username,))
            user = c.fetchone()
            
            if user:
                # User exists, log them in
                user_obj = User(id=user['id'], username=user['username'])
                login_user(user_obj)
                logger.info(f"User '{username}' logged in successfully")
                return redirect('/')
            else:
                # Create new user
                c.execute(
                    "INSERT INTO users (username, created_at) VALUES (%s, %s) RETURNING id",
                    (username, datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
                user_id = c.fetchone()['id']
                user_obj = User(id=user_id, username=username)
                login_user(user_obj)
                logger.info(f"New user '{username}' created and logged in")
                return redirect('/')
        except Exception as e:
            logger.error(f"Login error for user '{username}': {str(e)}", exc_info=True)
            return render_template('login.html', error="An error occurred during login. Please try again.")
        finally:
            # Connection closed by teardown context
            pass
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    username = current_user.username
    logout_user()
    logger.info(f"User '{username}' logged out")
    return redirect('/login')

@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html', username=current_user.username)

@app.route('/live-messages')
@login_required
def live_messages():
    return render_template('live-messages.html', username=current_user.username)

# Main application endpoints
@app.route('/api/conversations', methods=['GET'])
@login_required
def get_conversations():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute(
            """
            SELECT c.id, c.username, c.chat_id, c.channel, c.last_updated, c.ai_enabled, c.language,
                   COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.convo_id
            GROUP BY c.id
            ORDER BY c.last_updated DESC
            LIMIT 50
            """
        )
        conversations = c.fetchall()
        
        result = []
        for convo in conversations:
            result.append({
                "id": convo['id'],
                "username": convo['username'],
                "chat_id": convo['chat_id'],
                "channel": convo['channel'],
                "last_updated": convo['last_updated'],
                "ai_enabled": convo['ai_enabled'],
                "language": convo['language'],
                "message_count": convo['message_count']
            })
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Failed to get conversations: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to retrieve conversations"}), 500
    finally:
        # Connection closed by teardown context
        pass

@app.route('/api/messages/<int:convo_id>', methods=['GET'])
@login_required
def get_messages(convo_id):
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get conversation details
        c.execute("SELECT username, chat_id, channel, ai_enabled, language FROM conversations WHERE id = %s", (convo_id,))
        conversation = c.fetchone()
        
        if not conversation:
            return jsonify({"error": "Conversation not found"}), 404
        
        # Get messages
        c.execute(
            """
            SELECT id, username, message, sender, timestamp
            FROM messages
            WHERE convo_id = %s
            ORDER BY timestamp ASC
            """,
            (convo_id,)
        )
        messages = c.fetchall()
        
        result = {
            "conversation": {
                "id": convo_id,
                "username": conversation['username'],
                "chat_id": conversation['chat_id'],
                "channel": conversation['channel'],
                "ai_enabled": conversation['ai_enabled'],
                "language": conversation['language']
            },
            "messages": []
        }
        
        for msg in messages:
            result["messages"].append({
                "id": msg['id'],
                "username": msg['username'],
                "message": msg['message'],
                "sender": msg['sender'],
                "timestamp": msg['timestamp']
            })
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Failed to get messages for conversation {convo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to retrieve messages"}), 500
    finally:
        # Connection closed by teardown context
        pass

@app.route('/api/ai/toggle/<int:convo_id>', methods=['POST'])
@login_required
def toggle_ai(convo_id):
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get current AI setting
        c.execute("SELECT ai_enabled FROM conversations WHERE id = %s", (convo_id,))
        conversation = c.fetchone()
        
        if not conversation:
            return jsonify({"error": "Conversation not found"}), 404
        
        # Toggle AI setting
        new_status = 0 if conversation['ai_enabled'] == 1 else 1
        c.execute(
            "UPDATE conversations SET ai_enabled = %s WHERE id = %s",
            (new_status, convo_id)
        )
        conn.commit()
        
        status_text = "enabled" if new_status == 1 else "disabled"
        logger.info(f"AI {status_text} for conversation {convo_id} by user {current_user.username}")
        
        return jsonify({"success": True, "ai_enabled": new_status})
    except Exception as e:
        logger.error(f"Failed to toggle AI for conversation {convo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to toggle AI setting"}), 500
    finally:
        # Connection closed by teardown context
        pass

@app.route('/api/ai/global-toggle', methods=['POST'])
@login_required
def toggle_global_ai():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get current global AI setting
        current_value, _ = get_ai_enabled()
        
        # Toggle global AI setting
        new_value = "0" if current_value == "1" else "1"
        
        c.execute(
            """
            INSERT INTO settings (key, value, last_updated)
            VALUES ('ai_enabled', %s, %s, %s)
            ON CONFLICT (key) DO UPDATE
            SET value = %s, last_updated = %s
            """,
            (new_value, datetime.now(timezone.utc).isoformat(), new_value, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        
        # Clear cache
        if "ai_enabled" in settings_cache:
            del settings_cache["ai_enabled"]
        
        status_text = "enabled" if new_value == "1" else "disabled"
        logger.info(f"Global AI {status_text} by user {current_user.username}")
        
        return jsonify({"success": True, "ai_enabled": new_value})
    except Exception as e:
        logger.error(f"Failed to toggle global AI: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to toggle global AI setting"}), 500
    finally:
        # Connection closed by teardown context
        pass

@app.route('/api/send-message', methods=['POST'])
@login_required
def send_message():
    data = request.json
    if not data or 'message' not in data or 'convo_id' not in data:
        return jsonify({"error": "Missing required fields"}), 400
    
    message = data['message'].strip()
    convo_id = data['convo_id']
    
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get conversation details
        c.execute(
            "SELECT username, chat_id, channel, ai_enabled, language FROM conversations WHERE id = %s",
            (convo_id,)
        )
        conversation = c.fetchone()
        
        if not conversation:
            return jsonify({"error": "Conversation not found"}), 404
        
        # Log the message from the agent (human)
        timestamp = datetime.now(timezone.utc).isoformat()
        c.execute(
            "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (convo_id, current_user.username, message, "agent", timestamp)
        )
        message_id = c.fetchone()['id']
        
        # Update conversation timestamp
        c.execute(
            "UPDATE conversations SET last_updated = %s WHERE id = %s",
            (timestamp, convo_id)
        )
        conn.commit()
        
        # Broadcast the message via SocketIO
        socketio.emit('new_message', {
            'id': message_id,
            'convo_id': convo_id,
            'username': current_user.username,
            'message': message,
            'sender': 'agent',
            'timestamp': timestamp
        }, to=f"convo_{convo_id}")  # Changed 'room' to 'to' for Flask-SocketIO compatibility
        
        logger.info(f"Agent message sent to conversation {convo_id} by {current_user.username}")
        
        # Forward message to WhatsApp if the conversation is from WhatsApp
        if conversation['channel'] == 'whatsapp' and conversation['chat_id']:
            try:
                send_whatsapp_message_task.delay( # type: ignore
                    conversation['chat_id'],
                    message,
                    f"Agent: {current_user.username}"
                )
                logger.info(f"Message forwarded to WhatsApp for {conversation['chat_id']}")
            except Exception as e:
                logger.error(f"Failed to forward message to WhatsApp: {str(e)}", exc_info=True)
        
        return jsonify({
            "success": True,
            "message": {
                "id": message_id,
                "username": current_user.username,
                "message": message,
                "sender": "agent",
                "timestamp": timestamp
            }
        })
    except Exception as e:
        logger.error(f"Failed to send message to conversation {convo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to send message"}), 500
    finally:
        # Connection closed by teardown context
        pass

# Socket.IO events
@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False
    logger.info(f"SocketIO: User {current_user.username} connected")
    return True

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        logger.info(f"SocketIO: User {current_user.username} disconnected")

@socketio.on('join')
def handle_join(data):
    if not current_user.is_authenticated:
        return
    
    if 'convo_id' in data:
        room = f"convo_{data['convo_id']}"
        join_room(room)
        logger.info(f"SocketIO: User {current_user.username} joined room {room}")

@socketio.on('leave')
def handle_leave(data):
    if not current_user.is_authenticated:
        return
    
    if 'convo_id' in data:
        room = f"convo_{data['convo_id']}"
        leave_room(room)
        logger.info(f"SocketIO: User {current_user.username} left room {room}")

# WhatsApp webhook
@app.route('/api/whatsapp/webhook', methods=['POST'])
def whatsapp_webhook():
    try:
        data = request.json
        if not data:
            logger.error("Received empty JSON payload for WhatsApp webhook")
            return jsonify({"error": "Empty payload"}), 400

        logger.info(f"Received WhatsApp webhook: {json.dumps(data)[:200]}...")
        
        # Validate the request
        if 'From' not in data or 'Body' not in data:
            logger.error("Invalid WhatsApp webhook payload")
            return jsonify({"error": "Invalid payload"}), 400
        
        from_number = data['From']
        message_body = data['Body']

        if not from_number or not message_body:
            logger.error(f"Invalid WhatsApp webhook payload. Missing 'From' or 'Body'. Payload: {data}")
            return jsonify({"error": "Invalid payload"}), 400
        
        # Import here to avoid circular imports
        from tasks import process_whatsapp_message
        
        # Extract chat_id from the phone number
        chat_id = from_number.replace('whatsapp:', '')
        
        # Process the message asynchronously with Celery
        task = process_whatsapp_message.delay( # type: ignore
            from_number=from_number,
            chat_id=chat_id,
            message_body=message_body,
            user_timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        logger.info(f"WhatsApp message queued for processing. Task ID: {task.id}")
        
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error processing WhatsApp webhook: {str(e)}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

# Twilio WhatsApp webhook
@app.route('/api/twilio/webhook', methods=['POST'])
def twilio_webhook():
    try:
        from_number = request.form.get('From', '')
        message_body = request.form.get('Body', '')
        
        if not from_number or not message_body:
            logger.error("Invalid Twilio webhook payload")
            return Response("<Response></Response>", mimetype='text/xml')
        
        logger.info(f"Received Twilio message from {from_number}: {message_body[:100]}...")
        
        # Import here to avoid circular imports
        from tasks import process_whatsapp_message
        
        # Extract chat_id from the phone number
        chat_id = from_number.replace('whatsapp:', '')
        
        # Process the message asynchronously with Celery
        task = process_whatsapp_message.delay( # type: ignore
            from_number=from_number,
            chat_id=chat_id,
            message_body=message_body,
            user_timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        logger.info(f"Twilio message queued for processing. Task ID: {task.id}")
        
        return Response("<Response></Response>", mimetype='text/xml')
    except Exception as e:
        logger.error(f"Error processing Twilio webhook: {str(e)}", exc_info=True)
        return Response("<Response></Response>", mimetype='text/xml')

# Performance monitoring dashboard
@app.route('/admin/dashboard')
@login_required
def performance_dashboard():
    return render_template('performance_dashboard.html')

# Performance metrics API
@app.route('/api/metrics', methods=['GET'])
@login_required
def get_metrics():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        metrics = {}
        
        # Count conversations
        c.execute("SELECT COUNT(*) as count FROM conversations")
        metrics['conversation_count'] = c.fetchone()['count']
        
        # Count messages
        c.execute("SELECT COUNT(*) as count FROM messages")
        metrics['message_count'] = c.fetchone()['count']
        
        # Count users
        c.execute("SELECT COUNT(*) as count FROM users")
        metrics['user_count'] = c.fetchone()['count']
        
        # Message statistics by sender
        c.execute(
            """
            SELECT sender, COUNT(*) as count
            FROM messages
            GROUP BY sender
            """
        )
        sender_stats = {}
        for row in c.fetchall():
            sender_stats[row['sender']] = row['count']
        metrics['sender_stats'] = sender_stats
        
        # Messages in the last 24 hours
        c.execute(
            """
            SELECT COUNT(*) as count
            FROM messages
            WHERE timestamp > %s
            """,
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),)
        )
        metrics['messages_last_24h'] = c.fetchone()['count']
        
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Failed to get metrics: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to retrieve metrics"}), 500
    finally:
        # Connection closed by teardown context
        pass

# OpenAI diagnostic endpoints
@app.route('/openai-diag')
@login_required
def openai_diag():
    return render_template('openai_diag.html')

@app.route('/api/openai-test', methods=['POST'])
@login_required
def openai_test():
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Missing prompt"}), 400
    
    prompt = data['prompt']
    if not prompt:
        return jsonify({"error": "Prompt cannot be empty"}), 400
    
    try:
        start_time = time.time()
        
        # Create a simple prompt for testing
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100
        )
        
        result = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else 0
        processing_time = time.time() - start_time
        
        logger.info(f"OpenAI diagnostic test successful. Tokens: {tokens}, Time: {processing_time:.2f}s")
        
        return jsonify({
            "success": True,
            "result": result,
            "tokens": tokens,
            "time": processing_time
        })
    except Exception as e:
        logger.error(f"OpenAI diagnostic test failed: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# Calendar API for resort availability
@app.route('/api/calendar/availability', methods=['GET'])
@login_required
def get_availability():
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    
    if not start_date or not end_date:
        return jsonify({"error": "Missing start or end date"}), 400
    
    if service is None:
        return jsonify({"error": "Google Calendar integration is not configured on this deployment."}), 501
    try:
        # Call Google Calendar API to get events
        events_result = service.events().list(
            calendarId='primary',
            timeMin=f"{start_date}T00:00:00Z",
            timeMax=f"{end_date}T23:59:59Z",
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        # Process events to determine availability
        availability = {}
        current_date = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        
        while current_date <= end:
            date_str = current_date.strftime('%Y-%m-%d')
            availability[date_str] = {
                "standard": True,
                "deluxe": True,
                "suite": True
            }
            current_date += timedelta(days=1)
        
        # Mark dates as unavailable based on events
        for event in events:
            if 'summary' not in event:
                continue
            
            summary = event['summary'].lower()
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            # Simplistic parsing for demonstration
            if 'standard' in summary:
                room_type = 'standard'
            elif 'deluxe' in summary:
                room_type = 'deluxe'
            elif 'suite' in summary:
                room_type = 'suite'
            else:
                continue
            
            # Mark as unavailable
            event_date = start.split('T')[0] if 'T' in start else start
            if event_date in availability:
                availability[event_date][room_type] = False
        
        return jsonify(availability)
    except Exception as e:
        logger.error(f"Failed to get calendar availability: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to retrieve availability"}), 500

# Health check endpoint
@app.route('/health')
def health_check():
    try:
        # Check database connection
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute("SELECT 1")
        
        # Check Redis connection
        if redis_client is not None:
            redis_client.ping()
        # Check OpenAI API key
        if not os.getenv("OPENAI_API_KEY"):
            return jsonify({
                "status": "warning",
                "database": "ok",
                "redis": "ok",
                "openai": "not configured",
                "message": "OpenAI API key not configured"
            })
        # Check Google credentials
        try:
            # Check GOOGLE_SERVICE_ACCOUNT_KEY is not None before json.loads
            google_key = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
            if not google_key:
                raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")
            _ = service_account.Credentials.from_service_account_info(json.loads(google_key), scopes=SCOPES)
        except Exception:
            return jsonify({
                "status": "warning",
                "database": "ok",
                "redis": "ok",
                "openai": "ok",
                "google": "not configured"
            })
        return jsonify({
            "status": "ok",
            "database": "ok",
            "redis": "ok",
            "openai": "ok",
            "google": "ok",
            "uptime": "unknown"
        })
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# Start the server
if __name__ == '__main__':
    logger.info("🚀 Starting HotelChat server...")
    socketio.run(app, debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))