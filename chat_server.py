from dotenv import load_dotenv
load_dotenv()

# Gevent monkey-patching at the very top
import gevent
from gevent import monkey
monkey.patch_all()

# --- ALL IMPORTS AT THE TOP ---
import os
import sys
import logging
import json
import time
import re
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, Response, g
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import psycopg2
from psycopg2.extras import DictCursor
from psycopg2.pool import SimpleConnectionPool
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from cachetools import TTLCache
import redis as sync_redis
from concurrent_log_handler import ConcurrentRotatingFileHandler
from langdetect import detect, DetectorFactory
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from functools import wraps
import threading
from celery_app import celery_app

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
    print(f"❌ Missing required environment variables: {', '.join(missing_vars)}", file=sys.stderr)
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

SECRET_KEY = os.getenv("SECRET_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SERVICE_ACCOUNT_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- LOGGER SETUP (moved up to ensure all errors are logged) ---
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
    from concurrent_log_handler import ConcurrentRotatingFileHandler
    file_handler = ConcurrentRotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except ImportError:
    logger.warning("concurrent_log_handler not installed; file logging disabled.")
logger.info(f"Logging initialized. Level: {LOG_LEVEL}, File: {LOG_FILE_PATH}")

# --- REDIS CLIENT ---
try:
    redis_client = sync_redis.Redis.from_url(REDIS_URL)
    redis_client.ping()
    logger.info("Redis client initialized and connection verified.")
except Exception as e:
    logger.error(f"Redis connection failed: {e}")
    redis_client = None

# --- DATABASE CONNECTION POOL ---
database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
db_pool = None
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, database_url)
    test_conn = db_pool.getconn()
    db_pool.putconn(test_conn)
    logger.info("PostgreSQL connection pool initialized and connection verified.")
except Exception as e:
    logger.error(f"PostgreSQL connection failed: {e}")
    db_pool = None

# --- CELERY TASKS (REMOVED) ---
# Removed the direct import of tasks to prevent circular dependencies.
# Tasks will be called by name using celery_app.send_task().

# --- REDIS CLIENT VALIDATION ---
try:
    if redis_client is not None:
        redis_client.ping()
        logger.info("✅ Redis connection validated.")
    else:
        logger.error("❌ Redis client is not initialized.")
        raise RuntimeError("Redis client is not initialized.")
except Exception as e:
    logger.error(f"❌ Redis connection failed: {e}")
    raise

# --- DATABASE CONNECTION POOL VALIDATION ---
if db_pool is not None:
    try:
        test_conn = db_pool.getconn()
        with test_conn.cursor() as c:
            c.execute("SELECT 1")
        db_pool.putconn(test_conn)
        logger.info("✅ Database connection pool validated.")
    except Exception as e:
        logger.error(f"❌ Database connection pool failed: {e}")
        raise
else:
    logger.error("❌ db_pool is not initialized.")
    raise RuntimeError("db_pool is not initialized.")

# --- GOOGLE SERVICE ACCOUNT KEY VALIDATION ---
try:
    # Check GOOGLE_SERVICE_ACCOUNT_KEY is not None before json.loads
    google_key = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
    if not google_key:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")
    service_account_info = json.loads(google_key)
    SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=SCOPES
    )
    service = build('calendar', 'v3', credentials=credentials)
    logger.info("✅ Google service account credentials loaded.")
except Exception as e:
    logger.error(f"❌ Google service account credentials failed: {e}")
    raise

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = SECRET_KEY
CORS(app)

socketio = SocketIO(
    app,
    cors_allowed_origins=["http://localhost:5000", "https://hotel-chatbot-1qj5.onrender.com"],
    async_mode="gevent",
    message_queue=os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'), # Added message queue for Celery
    ping_timeout=60,
    ping_interval=15,
    logger=True,
    engineio_logger=True
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


# --- GLOBAL CACHES & CONFIGS ---

# Cache for ai_enabled setting with 5-second TTL
settings_cache = TTLCache(maxsize=1, ttl=5) # type: ignore

# Load or define the Q&A reference document
try:
    logger.debug("Attempting to load qa_reference.txt")
    with open("qa_reference.txt", "r", encoding='utf-8') as file:
        TRAINING_DOCUMENT = file.read()
    logger.info("✅ Loaded Q&A reference document")
except Exception as e:
    logger.warning(f"⚠️ qa_reference.txt not found or failed to load: {e}")
    TRAINING_DOCUMENT = "Amapola Resort Chatbot Training Document... (default)"

# Semaphore for controlling concurrent OpenAI API calls
try:
    OPENAI_CONCURRENCY = int(os.getenv("OPENAI_CONCURRENCY", "5"))
except Exception:
    OPENAI_CONCURRENCY = 5
logger.info(f"OpenAI concurrency limit: {OPENAI_CONCURRENCY}")


# --- DATABASE INITIALIZATION ---
def initialize_database():
    """Create all necessary tables if they don't exist."""
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Users table
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(80) UNIQUE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        logger.info("Table 'users' checked/created.")

        # Conversations table
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255),
                chat_id VARCHAR(255) UNIQUE,
                channel VARCHAR(50),
                last_updated TIMESTAMP WITH TIME ZONE,
                ai_enabled INTEGER DEFAULT 1,
                language VARCHAR(10),
                needs_agent INTEGER DEFAULT 0,
                booking_intent VARCHAR(255)
            );
        """)
        logger.info("Table 'conversations' checked/created.")

        # Messages table
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                convo_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
                username VARCHAR(255),
                message TEXT,
                sender VARCHAR(50),
                timestamp TIMESTAMP WITH TIME ZONE
            );
        """)
        logger.info("Table 'messages' checked/created.")

        # Settings table
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key VARCHAR(50) PRIMARY KEY,
                value TEXT,
                last_updated TIMESTAMP WITH TIME ZONE
            );
        """)
        logger.info("Table 'settings' checked/created.")

        # Seed initial global AI setting if not present
        c.execute("INSERT INTO settings (key, value, last_updated) VALUES ('ai_enabled', '1', %s) ON CONFLICT (key) DO NOTHING", (datetime.now(timezone.utc),))
        logger.info("Initial setting 'ai_enabled' checked/seeded.")

        conn.commit()
        logger.info("✅ Database initialization complete.")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}", exc_info=True)
        # Allow the app to continue, but log the critical error
    finally:
        if conn:
            release_db_connection(conn)

# --- DATABASE HELPER FUNCTIONS ---
def get_db_connection():
    global db_pool
    if db_pool is None:
        logger.error("db_pool is not initialized.")
        raise RuntimeError("db_pool is not initialized.")
    try:
        conn = db_pool.getconn()
        if conn.closed:
            logger.warning("Connection retrieved from pool is closed, reinitializing pool")
            if db_pool is not None:
                db_pool.closeall()
            db_pool = SimpleConnectionPool(
                minconn=1,
                maxconn=5,
                dsn=database_url,
                sslmode="require",
                sslrootcert=None,
                connect_timeout=10,
                options="-c statement_timeout=10000"
            )
            conn = db_pool.getconn()
        # Test the connection with a simple query
        with conn.cursor() as c:
            c.execute("SELECT 1")
        conn.cursor_factory = DictCursor
        logger.info("✅ Retrieved database connection from pool")
        return conn
    except Exception as e:
        logger.error(f"❌ Failed to get database connection: {str(e)}", exc_info=True)
        # If the error is SSL-related, reinitialize the pool
        error_str = str(e).lower()
        if any(err in error_str for err in ["ssl syscall error", "eof detected", "decryption failed", "bad record mac"]):
            logger.warning("SSL error detected, reinitializing connection pool")
            try:
                if db_pool is not None and hasattr(db_pool, 'closeall'):
                    db_pool.closeall()
                db_pool = SimpleConnectionPool(
                    minconn=1,
                    maxconn=5,
                    dsn=database_url,
                    sslmode="require",
                    sslrootcert=None,
                    connect_timeout=10,
                    options="-c statement_timeout=10000"
                )
                conn = db_pool.getconn()
                with conn.cursor() as c:
                    c.execute("SELECT 1")
                conn.cursor_factory = DictCursor
                logger.info("✅ Reinitialized database connection pool and retrieved new connection")
                return conn
            except Exception as e2:
                logger.error(f"❌ Failed to reinitialize database connection pool: {str(e2)}", exc_info=True)
        raise e

def release_db_connection(conn):
    global db_pool
    if db_pool is None:
        logger.error("db_pool is not initialized.")
        return
    if conn:
        try:
            if conn.closed:
                logger.warning("Attempted to release a closed connection")
            else:
                db_pool.putconn(conn)
                logger.info("✅ Database connection returned to pool")
        except Exception as e:
            logger.error(f"❌ Failed to return database connection to pool: {str(e)}")

def with_db_retry(func):
    """Decorator to retry database operations on failure."""
    def wrapper(*args, **kwargs):
        retries = 5
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"❌ Database operation failed (Attempt {attempt + 1}/{retries}): {str(e)}")
                error_str = str(e).lower()
                if any(err in error_str for err in ["ssl syscall error", "eof detected", "decryption failed", "bad record mac", "connection already closed"]):
                    global db_pool
                    try:
                        if db_pool:
                            db_pool.closeall()
                        db_pool = SimpleConnectionPool(
                            minconn=5,
                            maxconn=30,
                            dsn=database_url,
                            sslmode="require",
                            sslrootcert=None
                        )
                        logger.info("✅ Reinitialized database connection pool due to SSL or connection error")
                    except Exception as e2:
                        logger.error(f"❌ Failed to reinitialize database connection pool: {str(e2)}")
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                raise e
    return wrapper

# --- APPLICATION HELPERS ---
@with_db_retry
def get_ai_enabled():
    if "ai_enabled" in settings_cache:
        cached_value, cached_timestamp = settings_cache["ai_enabled"]
        logger.debug(f"CACHE HIT: ai_enabled='{cached_value}', timestamp='{cached_timestamp}'")
        return cached_value, cached_timestamp
    
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value, last_updated FROM settings WHERE key = %s", ("ai_enabled",))
        result = c.fetchone()
        
        if result:
            value = result['value']
            timestamp = result['last_updated']
            settings_cache["ai_enabled"] = (value, timestamp)
            logger.debug(f"CACHE MISS: Updated ai_enabled='{value}', timestamp='{timestamp}'")
            return value, timestamp
        else:
            # Default to enabled if no setting found
            logger.warning("No ai_enabled setting found in database, defaulting to enabled")
            return "1", datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.error(f"Failed to get ai_enabled setting: {str(e)}", exc_info=True)
        # Default to enabled on error
        return "1", datetime.now(timezone.utc).isoformat()
    finally:
        if conn:
            release_db_connection(conn)

# --- USER AUTHENTICATION ---
# User model for Flask-Login
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
        if conn:
            release_db_connection(conn)

# --- ROUTES ---

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
            if conn:
                release_db_connection(conn)
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    username = current_user.username
    logout_user()
    logger.info(f"User '{username}' logged out")
    return redirect('/login')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
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
        if conn:
            release_db_connection(conn)

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
        if conn:
            release_db_connection(conn)

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
        if conn:
            release_db_connection(conn)

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
            VALUES ('ai_enabled', %s, %s)
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
        if conn:
            release_db_connection(conn)

def _send_agent_message(convo_id, message, username):
    """Helper function to send a message from an agent."""
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        # Get conversation details
        c.execute(
            "SELECT username, chat_id, channel FROM conversations WHERE id = %s",
            (convo_id,)
        )
        conversation = c.fetchone()

        if not conversation:
            return False, "Conversation not found", 404

        # Log the message from the agent (human)
        timestamp = datetime.now(timezone.utc).isoformat()
        c.execute(
            "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (convo_id, username, message, "agent", timestamp)
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
            'username': username,
            'message': message,
            'sender': 'agent',
            'timestamp': timestamp
        }, to=f"convo_{convo_id}")

        logger.info(f"Agent message sent to conversation {convo_id} by {username}")

        # Forward message to WhatsApp if the conversation is from WhatsApp
        if conversation['channel'] == 'whatsapp' and conversation['chat_id']:
            try:
                celery_app.send_task(
                    'tasks.send_whatsapp_message_task',
                    args=[
                        conversation['chat_id'],
                        message,
                        f"Agent: {username}"
                    ]
                )
                logger.info(f"Message forwarded to WhatsApp for {conversation['chat_id']}")
            except Exception as e:
                logger.error(f"Failed to queue message for WhatsApp: {str(e)}", exc_info=True)

        message_data = {
            "id": message_id,
            "username": username,
            "message": message,
            "sender": "agent",
            "timestamp": timestamp
        }
        return True, message_data, 200
    except Exception as e:
        logger.error(f"Failed to send message to conversation {convo_id}: {str(e)}", exc_info=True)
        return False, "Failed to send message", 500
    finally:
        if conn:
            release_db_connection(conn)

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
    
    success, result, status_code = _send_agent_message(convo_id, message, current_user.username)
    
    if success:
        return jsonify({"success": True, "message": result}), status_code
    else:
        return jsonify({"error": result}), status_code

# Socket.IO events
@socketio.on('connect')
def handle_connect():
    # Allow all connections, but gate actions by authentication
    logger.info(f"SocketIO: A client connected with sid: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"SocketIO: Client {request.sid} disconnected")

@socketio.on('join')
def handle_join(data):
    if 'convo_id' not in data:
        logger.warning(f"Join event missing convo_id from {request.sid}")
        return

    convo_id = data['convo_id']
    room = f"convo_{convo_id}"
    join_room(room)
    logger.info(f"Client {request.sid} joined room: {room}")

@socketio.on('leave')
def handle_leave(data):
    if 'convo_id' not in data:
        logger.warning(f"Leave event missing convo_id from {request.sid}")
        return

    convo_id = data['convo_id']
    room = f"convo_{convo_id}"
    leave_room(room)
    logger.info(f"Client {request.sid} left room: {room}")

@socketio.on('guest_message')
def handle_guest_message(data):
    message = data.get('message')
    chat_id = data.get('chat_id')  # Persisted chat_id from client-side

    if not message:
        logger.warning(f"Guest message from {request.sid} is empty.")
        return

    # If no chat_id is provided (e.g., first message), create one.
    if not chat_id:
        chat_id = f"web_{request.sid}"
        logger.info(f"New guest session. Assigning chat_id: {chat_id}")

    # Emit back the chat_id so the client can persist it for the session
    emit('session_assigned', {'chat_id': chat_id})

    try:
        # Use Celery task to process the message asynchronously
        celery_app.send_task(
            'tasks.process_incoming_message',
            args=[
                chat_id,  # from_number (identifier for web)
                chat_id,  # chat_id
                message,
                datetime.now(timezone.utc).isoformat(),
                'web'  # channel
            ]
        )
        logger.info(f"Guest message from {chat_id} queued for processing.")
    except Exception as e:
        logger.error(f"Failed to queue guest message for processing: {e}", exc_info=True)


@socketio.on('agent_message')
@login_required
def handle_agent_message(data):
    if not current_user.is_authenticated:
        logger.warning(f"Unauthenticated agent_message attempt from {request.sid}")
        return

    convo_id = data.get('convo_id')
    message = data.get('message')

    if not convo_id or not message:
        logger.warning(f"Agent message missing convo_id or message from {request.sid}")
        return

    # Use the centralized helper function to handle the message
    _send_agent_message(convo_id, message, current_user.username)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Initialize the database on startup
    initialize_database()
    
    # Get port from environment or default to 5000
    port = int(os.environ.get("PORT", 5000))
    
    # Use Gunicorn for production, Flask dev server for local
    if os.getenv("FLASK_ENV") == "production":
        logger.info(f"Starting Gunicorn on port {port}")
        # Gunicorn is expected to be run from the command line, e.g.,
        # gunicorn --worker-class gevent --bind 0.0.0.0:5000 chat_server:app
        # The following is for local execution if needed, but not typical for prod.
        socketio.run(app, host='0.0.0.0', port=port)
    else:
        logger.info(f"Starting Flask development server on port {port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=True, use_reloader=True)