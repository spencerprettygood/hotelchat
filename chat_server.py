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
import openai
from openai import OpenAI, RateLimitError, APIError, AuthenticationError, APITimeoutError
import redis as sync_redis
from concurrent_log_handler import ConcurrentRotatingFileHandler
from langdetect import detect, DetectorFactory
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

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
    redis_client = redis.Redis.from_url(REDIS_URL)
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

# --- CELERY TASKS ---
try:
    from tasks import process_whatsapp_message, send_whatsapp_message_task
except Exception as e:
    logger.error(f"Celery tasks import failed: {e}")
    process_whatsapp_message = None
    send_whatsapp_message_task = None

# --- OPENAI CLIENT INITIALIZATION (v1.x COMPATIBLE) ---
try:
    # Try v1.x import and usage
    from openai import OpenAI, RateLimitError, APIError, AuthenticationError, APITimeoutError
    openai_client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=30.0
        # proxies argument removed; use HTTP_PROXY/HTTPS_PROXY env vars if needed
    )
    logger.info("OpenAI client (v1.x) initialized.")
except ImportError:
    # Fallback for v0.x
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    openai_client = openai
    # Define error types for v0.x
    class RateLimitError(Exception): pass
    class APIError(Exception): pass
    class AuthenticationError(Exception): pass
    class APITimeoutError(Exception): pass
    logger.info("OpenAI client (v0.x) initialized.")

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

# --- ENVIRONMENT VARIABLES ---
SECRET_KEY = os.getenv("SECRET_KEY", "dev_secret_key")
app.config["SECRET_KEY"] = SECRET_KEY

# --- DEPENDENCY CHECKS ---
# Ensure required packages are installed: redis, psycopg2
try:
    import redis
except ImportError:
    logger.error("Missing dependency: redis. Please install with 'pip install redis'.")
    raise
try:
    import psycopg2
    import psycopg2.pool
except ImportError:
    logger.error("Missing dependency: psycopg2. Please install with 'pip install psycopg2-binary'.")
    raise

# --- REDIS CLIENT ---
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    logger.error("REDIS_URL not set in environment variables")
    raise ValueError("REDIS_URL not set")
try:
    redis_client = redis.Redis.from_url(REDIS_URL)
    redis_client.ping()
    logger.info("Redis client initialized and connection verified.")
except Exception as e:
    logger.error(f"Redis connection failed: {e}")
    redis_client = None

# --- DATABASE CONNECTION POOL ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL not set in environment variables")
    raise ValueError("DATABASE_URL not set")
database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, database_url)
    test_conn = db_pool.getconn()
    db_pool.putconn(test_conn)
    logger.info("PostgreSQL connection pool initialized and connection verified.")
except Exception as e:
    logger.error(f"PostgreSQL connection failed: {e}")
    db_pool = None

# --- CELERY TASKS ---
# Import Celery tasks at the top to avoid shadowing and ensure proper initialization
try:
    from tasks import process_whatsapp_message, send_whatsapp_message_task
except Exception as e:
    logger.error(f"Celery tasks import failed: {e}")
    process_whatsapp_message = None
    send_whatsapp_message_task = None

# Initialize connection pool
try:
    database_url = database_url.replace("postgres://", "postgresql://", 1)
    logger.info(f"Using DATABASE_URL: {database_url}")
except NameError:
    logger.error("❌ DATABASE_URL environment variable not set")
    raise ValueError("DATABASE_URL environment variable not set")

# Use sslmode=require
if "sslmode" not in database_url:
    database_url += "?sslmode=require"
    logger.info(f"Added sslmode=require to DATABASE_URL: {database_url}")

db_pool = SimpleConnectionPool(
    minconn=1,  # Start with 1 connection
    maxconn=5,  # Limit to 5 connections to avoid overloading
    dsn=database_url,
    sslmode="require",  # Enforce SSL
    sslrootcert=None,  # Let psycopg2 handle SSL certificates
    connect_timeout=10,  # 10-second timeout for connections
    options="-c statement_timeout=10000"  # Set a 10-second statement timeout
)
logger.info("✅ Database connection pool initialized with minconn=1, maxconn=5, connect_timeout=10")

# Cache for ai_enabled setting with 5-second TTL
settings_cache = TTLCache(maxsize=1, ttl=5) # type: ignore

# Import tasks after app and logger are initialized to avoid circular imports
# logger.debug("Importing tasks module...")
# from tasks import process_whatsapp_message, send_whatsapp_message_task # Commented out global import
# logger.debug("Tasks module imported.")

# Validate OpenAI API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("⚠️ OPENAI_API_KEY not set in environment variables")
    raise ValueError("OPENAI_API_KEY not set")

# OpenAI client initialization (v1.x and v0.x compatibility)
logger.info("Initializing OpenAI client...")
try:
    # Try v1.x style
    openai_client = OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=30.0
    )
    # Test attribute to ensure v1.x
    _ = openai_client.chat.completions
    logger.info("OpenAI client (v1.x) initialized.")
except AttributeError:
    # Fallback to v0.x style
    openai.api_key = OPENAI_API_KEY
    openai_client = openai
    logger.info("OpenAI client (v0.x) initialized.")

# Semaphore for controlling concurrent OpenAI API calls
try:
    OPENAI_CONCURRENCY = int(os.getenv("OPENAI_CONCURRENCY", "5"))
except Exception:
    OPENAI_CONCURRENCY = 5
logger.info(f"OpenAI concurrency limit: {OPENAI_CONCURRENCY}")

# Define the AI response function
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError, APIError))
)
def get_ai_response(convo_id, username, conversation_history, user_message, chat_id, channel, language="en"):
    """
    Generates an AI response using the synchronous OpenAI client.
    This version is compatible with gevent and Celery.
    """
    start_time_ai = time.time()
    logger.info(f"GET_AI_RESPONSE initiated for convo_id: {convo_id}, user: {username}, channel: {channel}, lang: {language}. Message: '{user_message[:50]}...'")
    
    # Ensure conversation_history is a list of dicts
    if not isinstance(conversation_history, list) or not all(isinstance(msg, dict) for msg in conversation_history):
        logger.warning(f"Invalid conversation_history format for convo_id {convo_id}. Resetting to current message only.")
        conversation_history = [{"role": "user", "content": user_message}]
    elif not conversation_history: # Ensure it's not empty
        conversation_history = [{"role": "user", "content": user_message}]
    
    # Add the current user message to the history if it's not already the last message
    if not conversation_history or conversation_history[-1].get("content") != user_message or conversation_history[-1].get("role") != "user":
        conversation_history.append({"role": "user", "content": user_message})
    
    # Limit history length to avoid excessive token usage (e.g., last 10 messages)
    MAX_HISTORY_LEN = 10
    if len(conversation_history) > MAX_HISTORY_LEN:
        conversation_history = conversation_history[-MAX_HISTORY_LEN:]
        logger.debug(f"Trimmed conversation history to last {MAX_HISTORY_LEN} messages for convo_id {convo_id}")
    
    system_prompt = f"You are a helpful assistant for Amapola Resort. Current language for response: {language}. Training document: {TRAINING_DOCUMENT[:200]}..." # Truncate for logging
    messages_for_openai = [
        {"role": "system", "content": system_prompt}
    ] + conversation_history
    
    ai_reply = None
    detected_intent = None # Placeholder for intent detection
    handoff_triggered = False # Placeholder for handoff logic
    
    request_start_time = time.time()
    logger.info(f"[OpenAI Request] Convo ID: {convo_id} - Model: gpt-4o-mini - User Message: '{user_message[:100]}...'") # Log request details
    logger.debug(f"[OpenAI Request Details] Convo ID: {convo_id} - Full History: {conversation_history}")
    
    try:
        logger.info(f"Calling OpenAI API for convo_id {convo_id}. Model: gpt-4o-mini. History length: {len(messages_for_openai)}")
        
        # Call OpenAI API synchronously
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", # Using the specified model
            messages=messages_for_openai,
            max_tokens=300, # Max tokens for the response
            temperature=0.7
        )
        
        ai_reply = response.choices[0].message.content.strip()
        usage = response.usage
        processing_time = (time.time() - request_start_time) * 1000
        logger.info(
            f"[OpenAI Response] Convo ID: {convo_id} - Reply: '{ai_reply[:100]}...' - Tokens: P{usage.prompt_tokens}/C{usage.completion_tokens}/T{usage.total_tokens} - Time: {processing_time:.2f}ms"
        )
        logger.debug(f"[OpenAI Response Details] Convo ID: {convo_id} - Full Reply: {ai_reply} - Full Response Object: {response.model_dump_json(indent=2)}")
        
        # Basic intent detection (example - can be expanded)
        if "book a room" in user_message.lower() or "reservation" in user_message.lower():
            detected_intent = "booking_inquiry"
        if "human" in user_message.lower() or "agent" in user_message.lower() or "speak to someone" in user_message.lower():
            handoff_triggered = True
            logger.info(f"Handoff to human agent triggered by user message for convo_id {convo_id}")
            
    except RateLimitError as e:
        logger.error(f"❌ OpenAI RateLimitError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "I'm currently experiencing high demand. Please try again in a moment."
    except APITimeoutError as e:
        logger.error(f"❌ OpenAI APITimeoutError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "I'm having trouble connecting to my brain right now. Please try again shortly."
    except APIError as e: # General API error
        logger.error(f"❌ OpenAI APIError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "Sorry, I encountered an issue while processing your request."
    except AuthenticationError as e:
        logger.error(f"❌ OpenAI AuthenticationError for convo_id {convo_id}: {str(e)} (Check API Key)", exc_info=True)
        ai_reply = "There's an issue with my configuration. Please notify an administrator."
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_ai_response for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "An unexpected error occurred. I've logged the issue."
    
    processing_time = time.time() - start_time_ai
    logger.info(f"GET_AI_RESPONSE for convo_id {convo_id} completed in {processing_time:.2f}s. Intent: {detected_intent}, Handoff: {handoff_triggered}")
    return ai_reply, detected_intent, handoff_triggered

# Google Calendar setup with Service Account
GOOGLE_SERVICE_ACCOUNT_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
if not GOOGLE_SERVICE_ACCOUNT_KEY:
    logger.error("⚠️ GOOGLE_SERVICE_ACCOUNT_KEY not set in environment variables")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")
try:
    service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_KEY)
except json.JSONDecodeError as e:
    logger.error(f"⚠️ Invalid GOOGLE_SERVICE_ACCOUNT_KEY format: {e}")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY must be a valid JSON string")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
credentials = service_account.Credentials.from_service_account_info(
    service_account_info, scopes=SCOPES
)
service = build('calendar', 'v3', credentials=credentials)

# Messaging API tokens
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
if not TWILIO_ACCOUNT_SID:
    logger.error("⚠️ TWILIO_ACCOUNT_SID not set in environment variables")
    raise ValueError("TWILIO_ACCOUNT_SID not set")
if not TWILIO_AUTH_TOKEN:
    logger.error("⚠️ TWILIO_AUTH_TOKEN not set in environment variables")
    raise ValueError("TWILIO_AUTH_TOKEN not set")
if not TWILIO_WHATSAPP_NUMBER:
    logger.error("⚠️ TWILIO_WHATSAPP_NUMBER not set in environment variables")
    raise ValueError("TWILIO_WHATSAPP_NUMBER not set")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN")
if not WHATSAPP_API_TOKEN:
    logger.warning("⚠️ WHATSAPP_API_TOKEN not set, some WhatsApp features may not work")
WHATSAPP_API_URL = "https://api.whatsapp.com"

# Load or define the Q&A reference document
try:
    logger.debug("Attempting to load qa_reference.txt")
    with open("qa_reference.txt", "r", encoding='utf-8') as file: # Added encoding
        TRAINING_DOCUMENT = file.read()
    logger.info("✅ Loaded Q&A reference document")
except Exception as e:
    logger.warning(f"⚠️ qa_reference.txt not found or failed to load: {e}")
    TRAINING_DOCUMENT = "Amapola Resort Chatbot Training Document... (default)"

def get_db_connection():
    global db_pool
    try:
        conn = db_pool.getconn()
        if conn.closed:
            logger.warning("Connection retrieved from pool is closed, reinitializing pool")
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

# Cache the ai_enabled setting
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
                # Ensure send_whatsapp_message_task is a Celery task
                from tasks import send_whatsapp_message_task
                send_whatsapp_message_task.delay(
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
        if conn:
            release_db_connection(conn)

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
        logger.info(f"Received WhatsApp webhook: {json.dumps(data)[:200]}...")
        
        # Validate the request
        if 'From' not in data or 'Body' not in data:
            logger.error("Invalid WhatsApp webhook payload")
            return jsonify({"error": "Invalid payload"}), 400
        
        from_number = data['From']
        message_body = data['Body']
        
        # Import here to avoid circular imports
        from tasks import process_whatsapp_message
        
        # Extract chat_id from the phone number
        chat_id = from_number.replace('whatsapp:', '')
        
        # Process the message asynchronously with Celery
        task = process_whatsapp_message.delay(
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
        task = process_whatsapp_message.delay(
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
        if conn:
            release_db_connection(conn)

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
        tokens = response.usage.total_tokens
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
        release_db_connection(conn)
        # Check Redis connection
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