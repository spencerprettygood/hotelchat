# Gevent monkey-patching at the very top
import gevent
from gevent import monkey
monkey.patch_all()

# Now proceed with other imports
from flask import Flask, render_template, request, jsonify, session, redirect, Response
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import psycopg2
from psycopg2.extras import DictCursor
import os
import requests
import json
from datetime import datetime, timezone, timedelta
import time
import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from cachetools import TTLCache
from werkzeug.security import generate_password_hash, check_password_hash
import asyncio
from openai import AsyncOpenAI, RateLimitError, APIError, AuthenticationError
import redis.asyncio as redis
import redis as sync_redis  # Add synchronous Redis client
from concurrent_log_handler import ConcurrentRotatingFileHandler
from langdetect import detect, DetectorFactory
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

DetectorFactory.seed = 0

# Set up logging with both stream and file handlers
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chat_server")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(stream_handler)
file_handler = ConcurrentRotatingFileHandler("chat_server.log", maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(file_handler)

# Validate critical environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("⚠️ DATABASE_URL not set in environment variables")
    raise ValueError("DATABASE_URL not set")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    logger.error("⚠️ SECRET_KEY not set in environment variables")
    raise ValueError("SECRET_KEY not set")

SERVER_URL = os.getenv("SERVER_URL", "https://hotel-chatbot-1qj5.onrender.com")

# Redis clients
# Synchronous Redis client for Flask routes
redis_client = sync_redis.Redis.from_url(
    os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    decode_responses=True,
    max_connections=20
)

# Async Redis client for ai_respond
async_redis_client = redis.Redis.from_url(
    os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    decode_responses=True,
    max_connections=20
)

# Simplified Redis sync functions
def redis_get_sync(key):
    try:
        return redis_client.get(key)
    except Exception as e:
        logger.error(f"❌ Error in redis_get_sync for key {key}: {str(e)}")
        return None

def redis_setex_sync(key, ttl, value):
    try:
        redis_client.setex(key, ttl, value)
    except Exception as e:
        logger.error(f"❌ Error in redis_setex_sync for key {key}: {str(e)}")

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = SECRET_KEY
CORS(app)

socketio = SocketIO(
    app,
    cors_allowed_origins=["http://localhost:5000", "https://hotel-chatbot-1qj5.onrender.com"],
    async_mode="gevent",
    ping_timeout=60,
    ping_interval=15,
    logger=True,
    engineio_logger=True
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize database URL (no connection pool for now)
try:
    database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    logger.info(f"Using DATABASE_URL: {database_url}")
except NameError:
    logger.error("❌ DATABASE_URL environment variable not set")
    raise ValueError("DATABASE_URL environment variable not set")

# Disable SSL for debugging
if "sslmode" not in database_url:
    database_url += "?sslmode=disable"
    logger.info(f"Added sslmode=disable to DATABASE_URL: {database_url}")

# Cache for ai_enabled setting with 5-second TTL
settings_cache = TTLCache(maxsize=1, ttl=5)

# Import tasks after app and logger are initialized to avoid circular imports
from tasks import process_whatsapp_message, send_whatsapp_message_task

# Validate OpenAI API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("⚠️ OPENAI_API_KEY not set in environment variables")
    raise ValueError("OPENAI_API_KEY not set")

# Initialize OpenAI client with a timeout
openai_client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    timeout=30.0
)

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
    with open("qa_reference.txt", "r") as file:
        TRAINING_DOCUMENT = file.read()
    logger.info("✅ Loaded Q&A reference document")
except FileNotFoundError:
    TRAINING_DOCUMENT = """
    **Amapola Resort Chatbot Training Document**

    You are a friendly and professional chatbot for Amapola Resort, a luxury beachfront hotel. Your role is to assist guests with inquiries, help with bookings, and provide information about the resort’s services and amenities. Below is a set of common questions and answers to guide your responses. Always maintain conversation context, ask follow-up questions to clarify user intent, and provide helpful, concise answers. If a query is too complex or requires human assistance (e.g., specific booking modifications, complaints, or detailed itinerary planning), escalate to a human by saying: "I’m sorry, that’s a bit complex for me to handle. Let me get a human to assist you."

    **Business Information**
    - **Location**: Amapola Resort, 123 Ocean Drive, Sunny Beach, FL 33160
    - **Check-In/Check-Out**: Check-in at 3:00 PM, Check-out at 11:00 AM
    - **Room Types**:
      - Standard Room: $150/night, 2 guests, 1 queen bed
      - Deluxe Room: $250/night, 4 guests, 2 queen beds, ocean view
      - Suite: $400/night, 4 guests, 1 king bed, living area, oceanfront balcony
    - **Amenities**:
      - Beachfront access, outdoor pool, spa, gym, on-site restaurant (Amapola Bistro), free Wi-Fi, parking ($20/day)
    - **Activities**:
      - Snorkeling ($50/person), kayak rentals ($30/hour), sunset cruises ($100/person)
    - **Policies**:
      - Cancellation: Free cancellation up to 48 hours before arrival
      - Pets: Not allowed
      - Children: Kids under 12 stay free with an adult

    **Common Q&A**

    Q: What are your room rates?
    A: We offer several room types:
    - Standard Room: $150/night for 2 guests
    - Deluxe Room: $250/night for 4 guests, with an ocean view
    - Suite: $400/night for 4 guests, with an oceanfront balcony
    Would you like to book a room, or do you have questions about a specific room type?

    Q: How do I book a room?
    A: I can help you start the booking process! Please let me know:
    1. Your preferred dates (e.g., check-in and check-out dates)
    2. The number of guests
    3. Your preferred room type (Standard, Deluxe, or Suite)
    For example, you can say: "I’d like a Deluxe Room for 2 guests from March 10 to March 15." Once I have this information, I’ll check availability and guide you through the next steps. If you’d prefer to speak with a human to finalize your booking, let me know!

    Q: What is the check-in time?
    A: Check-in at Amapola Resort is at 3:00 PM, and check-out is at 11:00 AM. If you need an early check-in or late check-out, I can check availability for you—just let me know your dates!

    Q: Do you have a pool?
    A: Yes, we have a beautiful outdoor pool with beachfront views! It’s open from 8:00 AM to 8:00 PM daily. We also have a spa and gym if you’re interested in other amenities. Would you like to know more?

    Q: Can I bring my pet?
    A: I’m sorry, but pets are not allowed at Amapola Resort. If you need recommendations for pet-friendly accommodations nearby, I can help you find some options!

    Q: What activities do you offer?
    A: We have a variety of activities for our guests:
    - Snorkeling: $50 per person
    - Kayak rentals: $30 per hour
    - Sunset cruises: $100 per person
    Would you like to book an activity, or do you have questions about any of these?

    Q: What are the cancellation policies?
    A: You can cancel your reservation for free up to 48 hours before your arrival. After that, you may be charged for the first night. If you need to modify or cancel a booking, I can get a human to assist you with the details.

    Q: Do you have a restaurant?
    A: Yes, Amapola Bistro is our on-site restaurant, serving breakfast, lunch, and dinner with a focus on fresh seafood and local flavors. It’s open from 7:00 AM to 10:00 PM. Would you like to make a reservation or see the menu?

    **Conversational Guidelines**
    - Always greet new users with: "Thank you for contacting us."
    - For follow-up messages, do not repeat the greeting. Instead, respond based on the context of the conversation.
    - Ask clarifying questions if the user’s intent is unclear (e.g., "Could you tell me your preferred dates for booking?").
    - Use a friendly and professional tone, and keep responses concise (under 150 tokens, as set by max_tokens).
    - If the user asks multiple questions in one message, address each question systematically.
    - If the user provides partial information (e.g., "I want to book a room"), ask for missing details (e.g., dates, number of guests, room type).
    - If a query is ambiguous, ask for clarification (e.g., "Did you mean you’d like to book a room, or are you asking about our rates?").
    - Escalate to a human for complex requests, such as modifying an existing booking, handling complaints, or providing detailed recommendations.
    """
    logger.warning("⚠️ qa_reference.txt not found, using default training document")

def get_db_connection():
    try:
        # Create a new connection for each request
        conn = psycopg2.connect(
            dsn=database_url,
            sslmode="disable",
            cursor_factory=DictCursor
        )
        # Test the connection
        with conn.cursor() as c:
            c.execute("SELECT 1")
        logger.info("✅ Established new database connection")
        return conn
    except Exception as e:
        logger.error(f"❌ Failed to establish database connection: {str(e)}")
        raise e

def release_db_connection(conn):
    if conn:
        try:
            conn.close()
            logger.info("✅ Closed database connection")
        except Exception as e:
            logger.error(f"❌ Failed to close database connection: {str(e)}")

# Cache the ai_enabled setting
def get_ai_enabled():
    if "ai_enabled" in settings_cache:
        logger.info("✅ Retrieved ai_enabled from cache")
        return settings_cache["ai_enabled"]
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT value, last_updated FROM settings WHERE key = %s", ("ai_enabled",))
            result = c.fetchone()
            global_ai_enabled = result['value'] if result else "1"
            ai_toggle_timestamp = result['last_updated'] if result else "1970-01-01T00:00:00Z"
            settings_cache["ai_enabled"] = (global_ai_enabled, ai_toggle_timestamp)
            logger.info(f"✅ Cached ai_enabled: {global_ai_enabled}, last_updated: {ai_toggle_timestamp}")
            release_db_connection(conn)
        return settings_cache["ai_enabled"]
    except Exception as e:
        logger.error(f"❌ Failed to fetch ai_enabled from database: {str(e)}")
        return ("1", "1970-01-01T00:00:00Z")

def init_db():
    logger.info("Initializing database")
    with get_db_connection() as conn:
        c = conn.cursor()

        # Check if tables exist before creating them
        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'conversations'
            )
        """)
        conversations_table_exists = c.fetchone()[0]

        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'messages'
            )
        """)
        messages_table_exists = c.fetchone()[0]

        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'agents'
            )
        """)
        agents_table_exists = c.fetchone()[0]

        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'settings'
            )
        """)
        settings_table_exists = c.fetchone()[0]

        # Create tables only if they don't exist
        if not conversations_table_exists:
            c.execute('''CREATE TABLE conversations (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                assigned_agent TEXT,
                ai_enabled INTEGER DEFAULT 1,
                needs_agent INTEGER DEFAULT 0,
                booking_intent TEXT,
                handoff_notified INTEGER DEFAULT 0,
                visible_in_conversations INTEGER DEFAULT 1,
                language TEXT DEFAULT 'en',
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
            logger.info("Created conversations table")
        else:
            # Migration: Add needs_agent, language, and other columns if they don't exist
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'needs_agent'
                )
            """)
            needs_agent_exists = c.fetchone()[0]
            if not needs_agent_exists:
                c.execute("ALTER TABLE conversations ADD COLUMN needs_agent INTEGER DEFAULT 0")
                logger.info("Added needs_agent column to conversations table")

            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'language'
                )
            """)
            language_exists = c.fetchone()[0]
            if not language_exists:
                c.execute("ALTER TABLE conversations ADD COLUMN language TEXT DEFAULT 'en'")
                logger.info("Added language column to conversations table")

            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'booking_intent'
                )
            """)
            booking_intent_exists = c.fetchone()[0]
            if not booking_intent_exists:
                c.execute("ALTER TABLE conversations ADD COLUMN booking_intent TEXT")
                logger.info("Added booking_intent column to conversations table")

            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'handoff_notified'
                )
            """)
            handoff_notified_exists = c.fetchone()[0]
            if not handoff_notified_exists:
                c.execute("ALTER TABLE conversations ADD COLUMN handoff_notified INTEGER DEFAULT 0")
                logger.info("Added handoff_notified column to conversations table")

        if not messages_table_exists:
            c.execute('''CREATE TABLE messages (
                id SERIAL PRIMARY KEY,
                convo_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                sender TEXT NOT NULL,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (convo_id) REFERENCES conversations (id)
            )''')
            logger.info("Created messages table")

        if not agents_table_exists:
            c.execute('''CREATE TABLE agents (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )''')
            logger.info("Created agents table")
        else:
            # Migration: Add password_hash column if it doesn't exist
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'agents' AND column_name = 'password_hash'
                )
            """)
            password_hash_exists = c.fetchone()[0]
            if not password_hash_exists:
                c.execute("ALTER TABLE agents ADD COLUMN password_hash TEXT")
                # Update existing rows with a default password hash
                c.execute(
                    "UPDATE agents SET password_hash = %s WHERE password_hash IS NULL",
                    (generate_password_hash("password123"),)
                )
                logger.info("Added password_hash column to agents table and updated existing rows")

        if not settings_table_exists:
            c.execute('''CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
            logger.info("Created settings table")
        else:
            # Migration: Add last_updated column if it doesn't exist
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'settings' AND column_name = 'last_updated'
                )
            """)
            last_updated_exists = c.fetchone()[0]
            if not last_updated_exists:
                c.execute("ALTER TABLE settings ADD COLUMN last_updated TEXT DEFAULT CURRENT_TIMESTAMP")
                logger.info("Added last_updated column to settings table")

        # Create indexes for frequently queried columns
        c.execute("CREATE INDEX IF NOT EXISTS idx_conversations_chat_id ON conversations (chat_id);")
        logger.info("Created index idx_conversations_chat_id")

        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_convo_id ON messages (convo_id);")
        logger.info("Created index idx_messages_convo_id")

        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp);")
        logger.info("Created index idx_messages_timestamp")

        c.execute("CREATE INDEX IF NOT EXISTS idx_settings_key ON settings (key);")
        logger.info("Created index idx_settings_key")

        # Seed initial data (only in development or if explicitly enabled)
        if os.getenv("SEED_INITIAL_DATA", "false").lower() == "true":
            c.execute("SELECT COUNT(*) FROM settings")
            if c.fetchone()[0] == 0:
                c.execute(
                    "INSERT INTO settings (key, value, last_updated) VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING",
                    ('ai_enabled', '1', datetime.now(timezone.utc).isoformat())
                )
                logger.info("Inserted default settings")

            c.execute("SELECT COUNT(*) FROM agents")
            if c.fetchone()[0] == 0:
                c.execute(
                    "INSERT INTO agents (username, password_hash) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
                    ('admin', generate_password_hash('password123'))
                )
                logger.info("Inserted default admin user")

            c.execute("SELECT COUNT(*) FROM conversations WHERE channel = %s", ('whatsapp',))
            if c.fetchone()[0] == 0:
                logger.info("ℹ️ Inserting test conversations")
                test_timestamp1 = "2025-03-22T00:00:00Z"
                c.execute(
                    "INSERT INTO conversations (username, chat_id, channel, assigned_agent, ai_enabled, needs_agent, booking_intent, last_updated, language) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    ('TestUser1', '123456789', 'whatsapp', None, 1, 0, None, test_timestamp1, 'en')
                )
                convo_id1 = c.fetchone()['id']
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                    (convo_id1, 'TestUser1', 'Hello, I need help!', 'user', test_timestamp1)
                )
                test_timestamp2 = "2025-03-22T00:00:01Z"
                c.execute(
                    "INSERT INTO conversations (username, chat_id, channel, assigned_agent, ai_enabled, needs_agent, booking_intent, last_updated, language) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    ('TestUser2', '987654321', 'whatsapp', None, 1, 0, None, test_timestamp2, 'es')
                )
                convo_id2 = c.fetchone()['id']
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                    (convo_id2, 'TestUser2', 'Hola, ¿puedo reservar una habitación?', 'user', test_timestamp2)
                )
                logger.info("Inserted test conversations")

        conn.commit()
        logger.info("✅ Database initialized")
        release_db_connection(conn)

# Add test conversations (for development purposes)
def add_test_conversations():
    if os.getenv("SEED_INITIAL_DATA", "false").lower() != "true":
        logger.info("Skipping test conversations (SEED_INITIAL_DATA not enabled)")
        return
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM conversations WHERE channel = %s", ('test',))
            count = c.fetchone()['count']
            if count == 0:
                for i in range(1, 6):
                    c.execute(
                        "INSERT INTO conversations (username, chat_id, channel, ai_enabled, needs_agent, visible_in_conversations, last_updated) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (f"test_user_{i}", f"test_chat_{i}", "test", 1, 0, 0, datetime.now(timezone.utc).isoformat())
                    )
                    convo_id = c.fetchone()['id']
                    c.execute(
                        "INSERT INTO messages (convo_id, username, sender, message, timestamp) VALUES (%s, %s, %s, %s, %s)",
                        (convo_id, f"test_user_{i}", "user", f"Test message {i}", datetime.now(timezone.utc).isoformat())
                    )
                conn.commit()
                logger.info("✅ Added test conversations")
            else:
                logger.info("✅ Test conversations already exist, skipping insertion")
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"❌ Error adding test conversations: {e}")
        raise

# Initialize database and add test conversations
init_db()
add_test_conversations()

def log_message(convo_id, username, message, sender):
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        logger.info(f"Attempting to log message for convo_id {convo_id}: {message} (Sender: {sender}, Timestamp: {timestamp})")
        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute("BEGIN")
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (convo_id, username, message, sender, timestamp)
                )
                message_id = c.fetchone()['id']
                c.execute("COMMIT")
                logger.info(f"✅ Logged message for convo_id {convo_id}, message_id {message_id}: {message} (Sender: {sender})")
                release_db_connection(conn)
                return timestamp
            except Exception as e:
                c.execute("ROLLBACK")
                logger.error(f"❌ Failed to log message for convo_id {convo_id}: {str(e)}")
                raise
    except Exception as e:
        logger.error(f"❌ Failed to log message for convo_id {convo_id}: {str(e)}")
        raise

class Agent(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(agent_id):
    start_time = time.time()
    logger.info(f"Starting load_user for agent_id {agent_id}")
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, username FROM agents WHERE id = %s", (agent_id,))
        agent = c.fetchone()
        if agent:
            logger.info(f"Finished load_user for agent_id {agent_id} in {time.time() - start_time:.2f} seconds")
            return Agent(agent['id'], agent['username'])
        logger.info(f"Finished load_user for agent_id {agent_id} (not found) in {time.time() - start_time:.2f} seconds")
        return None
    except Exception as e:
        logger.error(f"❌ Error in load_user: {str(e)}")
        return None
    finally:
        release_db_connection(conn)

@app.route("/login", methods=["GET", "POST"])
def login():
    logger.info("✅ /login endpoint registered and called")
    start_time = time.time()
    logger.info("Starting /login endpoint")
    try:
        if request.method == "GET":
            if current_user.is_authenticated:
                logger.info(f"User already authenticated, redirecting in {time.time() - start_time:.2f} seconds")
                return redirect(request.args.get("next", "/conversations"))
            logger.info(f"Rendering login page in {time.time() - start_time:.2f} seconds")
            return render_template("login.html")
        
        # Log the request headers and content type
        logger.info(f"Request headers: {request.headers}")
        logger.info(f"Request content type: {request.content_type}")

        # Try to get JSON data
        data = request.get_json(silent=True)
        if data is None:
            # Fallback to form data
            logger.info("No JSON data found, trying form data")
            data = request.form
            logger.info(f"Form data: {dict(data)}")
        
        if not data:
            logger.error("❌ No valid JSON or form data in /login request")
            return jsonify({"message": "Invalid request format, expected JSON or form data"}), 400

        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            logger.error("❌ Missing username or password in /login request")
            return jsonify({"message": "Missing username or password"}), 400
        
        logger.info(f"Attempting to log in user: {username}")
        with get_db_connection() as conn:
            c = conn.cursor()
            # Check if password_hash column exists
            c.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'agents' AND column_name = 'password_hash'
            """)
            has_password_hash = bool(c.fetchone())
            if not has_password_hash:
                logger.error("❌ password_hash column does not exist in agents table")
                return jsonify({"error": "Server configuration error: password_hash column missing"}), 500
            c.execute(
                "SELECT id, username, password_hash FROM agents WHERE username = %s",
                (username,)
            )
            agent = c.fetchone()
            logger.info(f"Agent query result: {agent}")
            if agent and check_password_hash(agent['password_hash'], password):
                agent_obj = Agent(agent['id'], agent['username'])
                login_user(agent_obj)
                logger.info(f"✅ Login successful for agent: {agent['username']}")
                next_page = request.args.get("next", "/conversations")
                release_db_connection(conn)
                logger.info(f"Finished /login (success) in {time.time() - start_time:.2f} seconds")
                return jsonify({"message": "Login successful", "agent": agent['username'], "redirect": next_page})
            logger.error("❌ Invalid credentials in /login request")
            release_db_connection(conn)
            logger.info(f"Finished /login (failed) in {time.time() - start_time:.2f} seconds")
            return jsonify({"message": "Invalid credentials"}), 401
    except Exception as e:
        logger.error(f"❌ Error in /login: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to login due to a server error"}), 500

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    start_time = time.time()
    logger.info("Starting /logout endpoint")
    try:
        username = current_user.username
        logout_user()
        logger.info(f"✅ Logout successful for agent: {username}")
        logger.info(f"Finished /logout in {time.time() - start_time:.2f} seconds")
        return jsonify({"message": "Logged out successfully"})
    except Exception as e:
        logger.error(f"❌ Error in /logout: {e}")
        return jsonify({"error": "Failed to logout"}), 500

@app.route("/check-auth", methods=["GET"])
def check_auth():
    start_time = time.time()
    logger.info("Starting /check-auth endpoint")
    try:
        result = {
            "is_authenticated": current_user.is_authenticated,
            "agent": current_user.username if current_user.is_authenticated else None
        }
        logger.info(f"Finished /check-auth in {time.time() - start_time:.2f} seconds")
        return jsonify(result)
    except Exception as e:
        logger.error(f"❌ Error in /check-auth: {e}")
        return jsonify({"error": "Failed to check authentication"}), 500

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    start_time = time.time()
    logger.info("Starting /settings endpoint")
    try:
        if request.method == "GET":
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT key, value, last_updated FROM settings")
                settings = {row['key']: {'value': row['value'], 'last_updated': row['last_updated']} for row in c.fetchall()}
                release_db_connection(conn)
                logger.info(f"Finished /settings GET in {time.time() - start_time:.2f} seconds")
                return jsonify({key: val['value'] for key, val in settings.items()})

        elif request.method == "POST":
            data = request.get_json()
            key = data.get("key")
            value = data.get("value")
            if not key or value is None:
                logger.error("Missing key or value in /settings POST")
                return jsonify({"error": "Missing key or value"}), 400

            current_timestamp = datetime.now(timezone.utc).isoformat()
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("BEGIN")
                c.execute(
                    "INSERT INTO settings (key, value, last_updated) VALUES (%s, %s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = %s, last_updated = %s",
                    (key, value, current_timestamp, value, current_timestamp)
                )
                c.execute("COMMIT")
                if key == "ai_enabled":
                    settings_cache.pop("ai_enabled", None)
                    logger.info("✅ Invalidated ai_enabled cache after update")
                release_db_connection(conn)
                socketio.emit("settings_updated", {key: value})
                logger.info(f"Finished /settings POST in {time.time() - start_time:.2f} seconds")
                return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"❌ Error in /settings: {str(e)}")
        return jsonify({"error": "Failed to update settings"}), 500

# Page Endpoints
@app.route("/live-messages/")
@login_required
def live_messages_page():
    start_time = time.time()
    logger.info("Starting /live-messages endpoint")
    try:
        result = render_template("live-messages.html")
        logger.info(f"Finished /live-messages in {time.time() - start_time:.2f} seconds")
        return result
    except Exception as e:
        logger.error(f"❌ Error rendering live-messages page: {e}")
        return jsonify({"error": "Failed to load live-messages page"}), 500

@app.route("/")
def index():
    start_time = time.time()
    logger.info("Starting / endpoint")
    try:
        result = render_template("dashboard.html")
        logger.info(f"Finished / in {time.time() - start_time:.2f} seconds")
        return result
    except Exception as e:
        logger.error(f"❌ Error rendering dashboard page: {e}")
        return jsonify({"error": "Failed to load dashboard page"}), 500

@app.route("/conversations", methods=["GET"])
@login_required
def get_conversations():
    start_time = time.time()
    logger.info("Starting /conversations endpoint")
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, username, channel, assigned_agent, needs_agent, ai_enabled "
                "FROM conversations "
                "WHERE needs_agent = 1 "
                "ORDER BY last_updated DESC"
            )
            conversations = [
                {
                    "id": row["id"],
                    "username": row["username"],
                    "channel": row["channel"],
                    "assigned_agent": row["assigned_agent"],
                    "needs_agent": row["needs_agent"],
                    "ai_enabled": row["ai_enabled"]
                }
                for row in c.fetchall()
            ]
            release_db_connection(conn)
            logger.info(f"Finished /conversations in {time.time() - start_time:.2f} seconds")
            return jsonify(conversations)
    except Exception as e:
        logger.error(f"❌ Error in /conversations: {e}")
        return jsonify({"error": "Failed to fetch conversations"}), 500

@app.route("/messages/<convo_id>", methods=["GET"])
@login_required
def get_messages_for_conversation(convo_id):
    start_time = time.time()
    logger.info(f"Starting /messages/{convo_id} endpoint")
    try:
        try:
            convo_id = int(convo_id)
            if convo_id <= 0:
                raise ValueError("Conversation ID must be a positive integer")
        except ValueError:
            logger.error(f"❌ Invalid convo_id format: {convo_id}")
            return jsonify({"error": "Invalid conversation ID format"}), 400

        since = request.args.get("since")
        if since:
            try:
                datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                logger.error(f"❌ Invalid 'since' timestamp format: {since}")
                return jsonify({"error": "Invalid 'since' timestamp format"}), 400

        # Check Redis cache first
        cache_key = f"messages:{convo_id}:{since or 'full'}"
        cached_messages = redis_get_sync(cache_key)
        if cached_messages:
            logger.info(f"Returning cached messages for convo_id {convo_id}")
            cached_data = json.loads(cached_messages)
            return jsonify({
                "username": cached_data["username"],
                "messages": cached_data["messages"]
            })

        with get_db_connection() as conn:
            c = conn.cursor()
            # Check if the conversation exists and is visible
            c.execute(
                "SELECT username, visible_in_conversations FROM conversations WHERE id = %s",
                (convo_id,)
            )
            convo = c.fetchone()
            if not convo:
                logger.error(f"❌ Conversation not found: {convo_id}")
                release_db_connection(conn)
                return jsonify({"error": "Conversation not found"}), 404

            if not convo["visible_in_conversations"]:
                logger.info(f"Conversation {convo_id} is not visible")
                release_db_connection(conn)
                return jsonify({"username": convo["username"], "messages": []})

            username = convo["username"]

            if since:
                query = """
                    SELECT message, sender, timestamp
                    FROM messages
                    WHERE convo_id = %s AND timestamp > %s
                    ORDER BY timestamp ASC
                """
                c.execute(query, (convo_id, since))
                logger.info(f"Fetching messages for convo_id {convo_id} since {since}")
            else:
                query = """
                    SELECT message, sender, timestamp
                    FROM messages
                    WHERE convo_id = %s
                    ORDER BY timestamp ASC
                    LIMIT 50
                """
                c.execute(query, (convo_id,))
                logger.info(f"Fetching up to 50 messages for convo_id {convo_id}")

            messages = [
                {
                    "message": msg["message"],
                    "sender": msg["sender"],
                    "timestamp": msg["timestamp"] if isinstance(msg["timestamp"], str) else msg["timestamp"].isoformat()
                }
                for msg in c.fetchall()
            ]
            logger.info(f"✅ Fetched {len(messages)} messages for convo_id {convo_id}")

            # Cache the result for 300 seconds (5 minutes)
            cache_data = {"username": username, "messages": messages}
            redis_setex_sync(cache_key, 300, json.dumps(cache_data))
            release_db_connection(conn)

            logger.info(f"Finished /messages/{convo_id} in {time.time() - start_time:.2f} seconds")
            return jsonify({
                "username": username,
                "messages": messages
            })
    except Exception as e:
        logger.error(f"❌ Error in /messages/{convo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to fetch messages"}), 500

@app.route("/handoff", methods=["POST"])
@login_required
def handoff():
    start_time = time.time()
    logger.info("Starting /handoff endpoint")
    try:
        data = request.get_json()
        convo_id = data.get("conversation_id")
        disable_ai = data.get("disable_ai", True)
        if not convo_id:
            logger.error("Missing conversation_id in /handoff")
            return jsonify({"error": "Missing conversation_id"}), 400
        try:
            convo_id = int(convo_id)
            if convo_id <= 0:
                raise ValueError("Conversation ID must be a positive integer")
        except ValueError:
            logger.error(f"❌ Invalid conversation_id format: {convo_id}")
            return jsonify({"error": "Invalid conversation ID format"}), 400

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("BEGIN")
            c.execute(
                "UPDATE conversations SET assigned_agent = %s, ai_enabled = %s, last_updated = %s WHERE id = %s",
                (current_user.username, 0 if disable_ai else 1, datetime.now(timezone.utc).isoformat(), convo_id)
            )
            c.execute("COMMIT")
            release_db_connection(conn)
        socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        logger.info(f"Finished /handoff in {time.time() - start_time:.2f} seconds")
        return jsonify({"message": "Conversation assigned successfully"})
    except Exception as e:
        logger.error(f"❌ Error in /handoff: {e}")
        return jsonify({"error": "Failed to assign conversation"}), 500

@app.route("/handback-to-ai", methods=["POST"])
@login_required
def handback_to_ai():
    start_time = time.time()
    logger.info("Starting /handback-to-ai endpoint")
    try:
        data = request.get_json()
        convo_id = data.get("conversation_id")
        enable_ai = data.get("enable_ai", True)
        clear_needs_agent = data.get("clear_needs_agent", True)
        if not convo_id:
            logger.error("Missing conversation_id in /handback-to-ai")
            return jsonify({"error": "Missing conversation_id"}), 400
        try:
            convo_id = int(convo_id)
            if convo_id <= 0:
                raise ValueError("Conversation ID must be a positive integer")
        except ValueError:
            logger.error(f"❌ Invalid conversation_id format: {convo_id}")
            return jsonify({"error": "Invalid conversation ID format"}), 400

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("BEGIN")
            c.execute(
                "UPDATE conversations SET assigned_agent = NULL, ai_enabled = %s, needs_agent = %s, last_updated = %s WHERE id = %s",
                (1 if enable_ai else 0, 0 if clear_needs_agent else 1, datetime.now(timezone.utc).isoformat(), convo_id)
            )
            c.execute("COMMIT")
            release_db_connection(conn)
        socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        logger.info(f"Finished /handback-to-ai in {time.time() - start_time:.2f} seconds")
        return jsonify({"message": "Conversation handed back to AI"})
    except Exception as e:
        logger.error(f"❌ Error in /handback-to-ai: {e}")
        return jsonify({"error": "Failed to hand back to AI"}), 500

@app.route("/check-visibility", methods=["GET"])
@login_required
def check_visibility():
    start_time = time.time()
    logger.info("Starting /check-visibility endpoint")
    try:
        convo_id = request.args.get("conversation_id")
        if not convo_id:
            logger.error("Missing conversation_id in /check-visibility")
            return jsonify({"error": "Missing conversation_id"}), 400
        try:
            convo_id = int(convo_id)
            if convo_id <= 0:
                raise ValueError("Conversation ID must be a positive integer")
        except ValueError:
            logger.error(f"❌ Invalid conversation_id format: {convo_id}")
            return jsonify({"error": "Invalid conversation ID format"}), 400

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT needs_agent FROM conversations WHERE id = %s",
                (convo_id,)
            )
            result = c.fetchone()
            release_db_connection(conn)
            logger.info(f"Finished /check-visibility in {time.time() - start_time:.2f} seconds")
            return jsonify({"visible": bool(result["needs_agent"])})
    except Exception as e:
        logger.error(f"❌ Error in /check-visibility: {e}")
        return jsonify({"error": "Failed to check visibility"}), 500

@app.route("/all-whatsapp-messages", methods=["GET"])
@login_required
def get_all_whatsapp_messages():
    start_time = time.time()
    logger.info("Starting /all-whatsapp-messages endpoint")
    try:
        # Check Redis cache first
        cache_key = "all_whatsapp_conversations"
        cached_conversations = redis_get_sync(cache_key)
        if cached_conversations:
            logger.info("Returning cached WhatsApp conversations")
            return jsonify({"conversations": json.loads(cached_conversations)})

        with get_db_connection() as conn:
            c = conn.cursor()
            logger.info("Executing query to fetch conversations")
            c.execute(
                "SELECT id, chat_id, username, last_updated "
                "FROM conversations "
                "WHERE channel = 'whatsapp' "
                "ORDER BY last_updated DESC"
            )
            conversations = c.fetchall()
            logger.info(f"Found {len(conversations)} conversations: {[(c['id'], c['chat_id']) for c in conversations]}")
            result = [
                {
                    "convo_id": convo["id"],
                    "chat_id": convo["chat_id"],
                    "username": convo["username"],
                    "last_updated": convo["last_updated"]
                }
                for convo in conversations
            ]
            # Cache the result for 10 seconds
            redis_setex_sync(cache_key, 10, json.dumps(result))
            release_db_connection(conn)
            logger.info(f"Finished /all-whatsapp-messages in {time.time() - start_time:.2f} seconds")
            return jsonify({"conversations": result})
    except Exception as e:
        logger.error(f"Error fetching all WhatsApp messages: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to fetch conversations"}), 500

# Messaging Helper Functions
def send_whatsapp_message(phone_number, text):
    logger.info(f"Offloading WhatsApp message to Celery task for {phone_number}")
    return send_whatsapp_message_task.delay(phone_number, text)

def check_availability(check_in, check_out):
    start_time = time.time()
    logger.info(f"Starting check_availability from {check_in} to {check_out}")
    max_retries = 3
    current_date = check_in
    while current_date < check_out:
        start_time_dt = current_date.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
        end_time_dt = (current_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'

        for attempt in range(max_retries):
            try:
                events_result = service.events().list(
                    calendarId='a33289c61cf358216690e7cc203d116cec4c44075788fab3f2b200f5bbcd89cc@group.calendar.google.com',
                    timeMin=start_time_dt,
                    timeMax=end_time_dt,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                events = events_result.get('items', [])

                if any(event.get('summary') == "Fully Booked" for event in events):
                    result = f"Sorry, the dates from {check_in.strftime('%B %d, %Y')} to {(check_out - timedelta(days=1)).strftime('%B %d, %Y')} are not available. We are fully booked on {current_date.strftime('%B %d, %Y')}."
                    logger.info(f"Finished check_availability (not available) in {time.time() - start_time:.2f} seconds")
                    return result
                break
            except HttpError as e:
                logger.error(f"❌ Google Calendar API error (Attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return "Sorry, I’m having trouble checking availability right now. I’ll connect you with a team member to assist you."
            except Exception as e:
                logger.error(f"❌ Unexpected error in check_availability (Attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return "Sorry, I’m having trouble checking availability right now. I’ll connect you with a team member to assist you."
        current_date += timedelta(days=1)

    result = f"Yes, the dates from {check_in.strftime('%B %d, %Y')} to {(check_out - timedelta(days=1)).strftime('%B %d, %Y')} are available."
    logger.info(f"Finished check_availability (available) in {time.time() - start_time:.2f} seconds")
    return result

# Semaphore to limit concurrent OpenAI API requests
OPENAI_CONCURRENT_LIMIT = 20
openai_semaphore = asyncio.Semaphore(OPENAI_CONCURRENT_LIMIT)

def detect_language(message, convo_id):
    start_time = time.time()
    logger.info(f"Starting detect_language for convo_id {convo_id}")
    try:
        # First, check if the conversation has a stored language
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT language FROM conversations WHERE id = %s",
                (convo_id,)
            )
            result = c.fetchone()
            if result and result['language']:
                logger.info(f"Using stored language for convo_id {convo_id}: {result['language']}")
                release_db_connection(conn)
                return result['language']

        # If no stored language, detect the language of the current message
        detected_lang = detect(message)
        logger.info(f"Detected language for message '{message}': {detected_lang}")

        # If detection confidence is low, check conversation history
        if detected_lang not in ['en', 'es']:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT message FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 5",
                    (convo_id,)
                )
                messages = c.fetchall()
                release_db_connection(conn)
                for msg in messages:
                    try:
                        hist_lang = detect(msg['message'])
                        if hist_lang in ['en', 'es']:
                            detected_lang = hist_lang
                            logger.info(f"Using language from history for convo_id {convo_id}: {detected_lang}")
                            break
                    except:
                        continue

        # Default to English if still undetermined
        if detected_lang not in ['en', 'es']:
            detected_lang = 'en'
            logger.info(f"Defaulting to English for convo_id {convo_id}")

        # Store the detected language in the conversations table
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE conversations SET language = %s WHERE id = %s",
                (detected_lang, convo_id)
            )
            conn.commit()
            release_db_connection(conn)
            logger.info(f"Stored detected language for convo_id {convo_id}: {detected_lang}")

        logger.info(f"Finished detect_language in {time.time() - start_time:.2f} seconds")
        return detected_lang
    except Exception as e:
        logger.error(f"Error in detect_language for convo_id {convo_id}: {str(e)}")
        return 'en'

# Update the ai_respond function with tenacity for retries
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((RateLimitError, asyncio.TimeoutError))
)
async def ai_respond(message, convo_id):
    start_time = time.time()
    logger.info(f"Starting ai_respond for convo_id {convo_id}: {message}")
    try:
        # Detect language
        language = detect_language(message, convo_id)
        is_spanish = (language == "es")

        # Check cache for common messages
        cache_key = f"ai_response:{message}:{convo_id}"
        cached_response = await async_redis_client.get(cache_key)
        if cached_response:
            logger.info(f"Returning cached response for message: {message}")
            return cached_response

        # Enhanced date parsing logic
        date_match = re.search(
            r'(?:are rooms available|availability|do you have any rooms|rooms available|what about|next week|this|'
            r'¿hay habitaciones disponibles?|disponibilidad|¿tienen habitaciones?|habitaciones disponibles?|qué tal|la próxima semana|este)?\s*'
            r'(?:from|on|de|el)?\s*'
            r'(?:(?:([A-Za-z]{3,9})\s+(\d{1,2}(?:st|nd|rd|th)?))|(?:(\d{1,2})\s*(?:de)?\s*([A-Za-z]{3,9})))'
            r'(?:\s*(?:to|a|al|until|hasta)?\s*'
            r'(?:(?:([A-Za-z]{3,9})\s+(\d{1,2}(?:st|nd|rd|th)?))|(?:(\d{1,2})\s*(?:de)?\s*([A-Za-z]{3,9}))))?',
            message.lower()
        )
        if date_match or "next week" in message.lower() or "la próxima semana" in message.lower() or "this" in message.lower() or "este" in message.lower():
            spanish_to_english_months = {
                "enero": "January", "febrero": "February", "marzo": "March", "abril": "April",
                "mayo": "May", "junio": "June", "julio": "July", "agosto": "August",
                "septiembre": "September", "octubre": "October", "noviembre": "November", "diciembre": "December"
            }
            current_year = datetime.now().year
            today = datetime.now()

            if "next week" in message.lower() or "la próxima semana" in message.lower():
                # Calculate next week's Monday as check-in and Sunday as check-out
                days_until_monday = (7 - today.weekday()) % 7 or 7
                check_in = today + timedelta(days=days_until_monday)
                check_out = check_in + timedelta(days=6)
            elif "this" in message.lower() or "este" in message.lower():
                # Parse "this Friday" or "este viernes"
                day_match = re.search(r'(this|este)\s*(monday|lunes|Tuesday|martes|wednesday|miércoles|thursday|jueves|friday|viernes|saturday|sábado|sunday|domingo)', message.lower())
                if day_match:
                    day_name = day_match.group(2)
                    day_mapping = {
                        "monday": 0, "lunes": 0, "tuesday": 1, "martes": 1, "wednesday": 2, "miércoles": 2,
                        "thursday": 3, "jueves": 3, "friday": 4, "viernes": 4, "saturday": 5, "sábado": 5,
                        "sunday": 6, "domingo": 6
                    }
                    target_day = day_mapping.get(day_name)
                    days_until_target = (target_day - today.weekday()) % 7
                    if days_until_target == 0 and today.hour >= 12:
                        days_until_target = 7  # If it's already that day past noon, assume next week
                    check_in = today + timedelta(days=days_until_target)
                    check_out = check_in + timedelta(days=1)
                else:
                    result = "I’m not sure which day you meant by 'this'. Can you specify, like 'this Friday' or 'este viernes'?" if not is_spanish else \
                             "No estoy seguro de qué día te refieres con 'este'. ¿Puedes especificar, como 'este viernes' o 'this Friday'?"
                    await async_redis_client.setex(cache_key, 3600, result)
                    logger.info(f"Finished ai_respond (ambiguous 'this' date) in {time.time() - start_time:.2f} seconds")
                    return result
            else:
                month1_en, day1_en, day1_es, month1_es, month2_en, day2_en, day2_es, month2_es = date_match.groups()

                if month1_en and day1_en:
                    check_in_str = f"{month1_en} {day1_en}"
                    check_in_str = re.sub(r'(st|nd|rd|th)', '', check_in_str).strip()
                    check_in = datetime.strptime(f"{check_in_str} {current_year}", '%B %d %Y')
                elif day1_es and month1_es:
                    month1_en = spanish_to_english_months.get(month1_es.lower(), month1_es)
                    check_in_str = f"{month1_en} {day1_es}"
                    check_in = datetime.strptime(f"{check_in_str} {current_year}", '%B %d %Y')
                else:
                    result = "Sorry, I couldn’t understand the dates. Please use a format like 'March 20' or '20 de marzo'." if not is_spanish else \
                           "Lo siento, no entendí las fechas. Por favor, usa un formato como '20 de marzo' o 'March 20'."
                    await async_redis_client.setex(cache_key, 3600, result)
                    logger.info(f"Finished ai_respond (date error) in {time.time() - start_time:.2f} seconds")
                    return result

                if month2_en and day2_en:
                    check_out_str = f"{month2_en} {day2_en}"
                    check_out_str = re.sub(r'(st|nd|rd|th)', '', check_out_str).strip()
                    check_out = datetime.strptime(f"{check_out_str} {current_year}", '%B %d %Y')
                elif day2_es and month2_es:
                    month2_en = spanish_to_english_months.get(month2_es.lower(), month2_es)
                    check_out_str = f"{month2_en} {day2_es}"
                    check_out = datetime.strptime(f"{check_out_str} {current_year}", '%B %d %Y')
                else:
                    check_out = check_in + timedelta(days=1)

            if check_out < check_in:
                check_out = check_out.replace(year=check_out.year + 1)

            if check_out <= check_in:
                result = "The check-out date must be after the check-in date. Please provide a valid range." if not is_spanish else \
                       "La fecha de salida debe ser posterior a la fecha de entrada. Por favor, proporciona un rango válido."
                await async_redis_client.setex(cache_key, 3600, result)
                logger.info(f"Finished ai_respond (invalid date range) in {time.time() - start_time:.2f} seconds")
                return result

            availability = check_availability(check_in, check_out)
            if "are available" in availability.lower():
                booking_intent = f"{check_in.strftime('%Y-%m-%d')} to {check_out.strftime('%Y-%m-%d')}"
                with get_db_connection() as conn:
                    try:
                        c = conn.cursor()
                        c.execute("BEGIN")
                        c.execute(
                            "UPDATE conversations SET booking_intent = %s WHERE id = %s",
                            (booking_intent, convo_id)
                        )
                        c.execute("COMMIT")
                    finally:
                        release_db_connection(conn)
                response = f"{availability} Would you like to proceed with the booking? I’ll need to connect you with a team member to finalize it." if not is_spanish else \
                           f"{availability.replace('are available', 'están disponibles')} ¿Te gustaría proceder con la reserva? Necesitaré conectarte con un miembro del equipo para finalizarla."
            else:
                response = availability if not is_spanish else \
                           availability.replace("are not available", "no están disponibles").replace("fully booked", "completamente reservado")
            await async_redis_client.setex(cache_key, 3600, response)
            logger.info(f"Finished ai_respond (availability check) in {time.time() - start_time:.2f} seconds")
            return response

        # Check for booking intent with partial information
        booking_match = re.search(
            r'(?:book|booking|reserve|reservar)\s*(?:a\s*)?(room|habitación)?\s*(?:for\s*)?(\d+)\s*(?:people|personas|guests|huéspedes)?',
            message.lower()
        )
        if booking_match or "book" in message.lower() or "booking" in message.lower() or "reservar" in message.lower():
            # Check if we have partial booking info
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT booking_intent FROM conversations WHERE id = %s",
                    (convo_id,)
                )
                result = c.fetchone()
                booking_intent = result['booking_intent'] if result else None
                release_db_connection(conn)

            if booking_match:
                _, num_guests = booking_match.groups()
                if num_guests:
                    # Store the number of guests in booking_intent
                    booking_intent = f"guests:{num_guests}" if not booking_intent else f"{booking_intent},guests:{num_guests}"
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE conversations SET booking_intent = %s WHERE id = %s",
                            (booking_intent, convo_id)
                        )
                        conn.commit()
                        release_db_connection(conn)

            if not booking_intent or "guests" not in booking_intent or "to" not in booking_intent:
                missing_info = []
                if not booking_intent or "to" not in booking_intent:
                    missing_info.append("your preferred dates (like March 10 to March 15)" if not is_spanish else "tus fechas preferidas (como del 10 al 15 de marzo)")
                if not booking_intent or "guests" not in booking_intent:
                    missing_info.append("the number of guests" if not is_spanish else "el número de huéspedes")
                missing_info.append("the room type you’d like (Standard, Deluxe, or Suite)" if not is_spanish else "el tipo de habitación que te gustaría (Estándar, Deluxe o Suite)")

                result = f"I’d love to help with your booking! Can you tell me {', '.join(missing_info)}?" if not is_spanish else \
                         f"¡Me encantaría ayudarte con tu reserva! ¿Me puedes decir {', '.join(missing_info)}?"
                await async_redis_client.setex(cache_key, 3600, result)
                logger.info(f"Finished ai_respond (partial booking info) in {time.time() - start_time:.2f} seconds")
                return result

            with get_db_connection() as conn:
                try:
                    c = conn.cursor()
                    c.execute("BEGIN")
                    c.execute(
                        "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                        (datetime.now(timezone.utc).isoformat(), convo_id)
                    )
                    c.execute("COMMIT")
                finally:
                    release_db_connection(conn)
            socketio.emit("refresh_conversations", {"conversation_id": convo_id})
            result = "I have all the details for your booking! I’ll connect you with a team member to finalize it for you." if not is_spanish else \
                   "¡Tengo todos los detalles para tu reserva! Te conectaré con un miembro del equipo para que la finalice por ti."
            await async_redis_client.setex(cache_key, 3600, result)
            logger.info(f"Finished ai_respond (booking intent, needs agent) in {time.time() - start_time:.2f} seconds")
            return result

        # Fetch conversation history from cache or database
        history_cache_key = f"conversation_history:{convo_id}"
        cached_history = await async_redis_client.get(history_cache_key)
        if cached_history:
            messages = json.loads(cached_history)
            logger.info(f"Retrieved conversation history from cache for convo_id {convo_id}")
        else:
            with get_db_connection() as conn:
                try:
                    c = conn.cursor()
                    c.execute(
                        "SELECT message, sender, timestamp FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 10",
                        (convo_id,)
                    )
                    messages = c.fetchall()
                    await async_redis_client.setex(history_cache_key, 300, json.dumps([dict(msg) for msg in messages]))
                    logger.info(f"Cached conversation history for convo_id {convo_id}")
                finally:
                    release_db_connection(conn)

        # Build conversation history
        conversation_history = [
            {"role": "system", "content": TRAINING_DOCUMENT}
        ]
        for msg in messages:
            message_text, sender, timestamp = msg['message'], msg['sender'], msg['timestamp']
            role = "user" if sender == "user" else "assistant"
            conversation_history.append({"role": role, "content": message_text})
        conversation_history.append({"role": "user", "content": message})

        # Call OpenAI API asynchronously with rate limiting
        async with openai_semaphore:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=conversation_history,
                max_tokens=300,
                temperature=0.7
            )
            ai_reply = response.choices[0].message.content.strip()
            logger.info(f"✅ AI reply: {ai_reply}")
            if "sorry" in ai_reply.lower() or "lo siento" in ai_reply.lower():
                with get_db_connection() as conn:
                    try:
                        c = conn.cursor()
                        c.execute("BEGIN")
                        c.execute(
                            "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                            (datetime.now(timezone.utc).isoformat(), convo_id)
                        )
                        c.execute("COMMIT")
                    finally:
                        release_db_connection(conn)
                socketio.emit("refresh_conversations", {"conversation_id": convo_id})
                await async_redis_client.setex(cache_key, 3600, ai_reply)
                logger.info(f"Finished ai_respond (AI sorry, needs agent) in {time.time() - start_time:.2f} seconds")
                return ai_reply
            await async_redis_client.setex(cache_key, 3600, ai_reply)
            logger.info(f"Finished ai_respond (AI success) in {time.time() - start_time:.2f} seconds")
            return ai_reply

    except RateLimitError as e:
        logger.error(f"❌ OpenAI RateLimitError: {str(e)}")
        raise
    except asyncio.TimeoutError as e:
        logger.error(f"❌ OpenAI API request timed out: {str(e)}")
        raise
    except APIError as e:
        logger.error(f"❌ OpenAI APIError: {str(e)}")
        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute("BEGIN")
                c.execute(
                    "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                    (datetime.now(timezone.utc).isoformat(), convo_id)
                )
                c.execute("COMMIT")
            finally:
                release_db_connection(conn)
        socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        result = "I’m sorry, I’m having trouble processing your request right now due to an API error. I’ll connect you with a team member to assist you." if not is_spanish else \
               "Lo siento, tengo problemas para procesar tu solicitud ahora mismo debido a un error de API. Te conectaré con un miembro del equipo para que te ayude."
        await async_redis_client.setex(cache_key, 3600, result)
        logger.info(f"Finished ai_respond (APIError, needs agent) in {time.time() - start_time:.2f} seconds")
        return result
    except AuthenticationError as e:
        logger.error(f"❌ OpenAI AuthenticationError: {str(e)}")
        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute("BEGIN")
                c.execute(
                    "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                    (datetime.now(timezone.utc).isoformat(), convo_id)
                )
                c.execute("COMMIT")
            finally:
                release_db_connection(conn)
        socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        result = "I’m sorry, I’m having trouble authenticating with the AI service. I’ll connect you with a team member to assist you." if not is_spanish else \
               "Lo siento, tengo problemas para autenticarme con el servicio de IA. Te conectaré con un miembro del equipo para que te ayude."
        await async_redis_client.setex(cache_key, 3600, result)
        logger.info(f"Finished ai_respond (AuthenticationError, needs agent) in {time.time() - start_time:.2f} seconds")
        return result
    except Exception as e:
        logger.error(f"❌ Error in ai_respond for convo_id {convo_id}: {str(e)}")
        with get_db_connection() as conn:
            try:
                c = conn.cursor()
                c.execute("BEGIN")
                c.execute(
                    "UPDATE conversations SET needs_agent = 1, last_updated = %s WHERE id = %s",
                    (datetime.now(timezone.utc).isoformat(), convo_id)
                )
                c.execute("COMMIT")
            finally:
                release_db_connection(conn)
        socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        result = "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if not is_spanish else \
               "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
        await async_redis_client.setex(cache_key, 3600, result)
        logger.info(f"Finished ai_respond (general error, needs agent) in {time.time() - start_time:.2f} seconds")
        return result

def ai_respond_sync(message, convo_id):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(ai_respond(message, convo_id))
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"❌ Error in ai_respond_sync for convo_id {convo_id}: {str(e)}")
        return "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you."

# Export both ai_respond and ai_respond_sync for use in tasks.py
__all__ = ['ai_respond', 'ai_respond_sync']

@app.route("/whatsapp", methods=["GET", "POST"])
def whatsapp():
    start_time = time.time()
    logger.info("Starting /whatsapp endpoint")
    try:
        if request.method == "GET":
            logger.error("❌ GET method not allowed for /whatsapp")
            return Response("Method not allowed", status=405)

        form = request.form
        message_body = form.get("Body", "").strip()
        from_number = form.get("From", "")
        chat_id = from_number.replace("whatsapp:", "")
        if not message_body or not chat_id:
            logger.error("❌ Missing message body or chat_id in WhatsApp request")
            return Response("Missing required fields", status=400)

        user_timestamp = datetime.now(timezone.utc).isoformat()
        process_whatsapp_message.delay(from_number, chat_id, message_body, user_timestamp)
        logger.info(f"Finished /whatsapp (queued) in {time.time() - start_time:.2f} seconds")
        return Response("Message queued for processing", status=202)
    except Exception as e:
        logger.error(f"❌ Error in /whatsapp: {str(e)}")
        return Response("Failed to process WhatsApp message", status=500)

@app.route("/refresh_conversations", methods=["POST"])
def refresh_conversations():
    start_time = time.time()
    logger.info("Starting /refresh_conversations endpoint")
    try:
        data = request.get_json()
        convo_id = data.get("conversation_id")
        if not convo_id:
            logger.error("Missing conversation_id in /refresh_conversations")
            return jsonify({"error": "Missing conversation_id"}), 400
        socketio.emit("refresh_conversations", {"conversation_id": convo_id})
        logger.info(f"Finished /refresh_conversations in {time.time() - start_time:.2f} seconds")
        return jsonify({"message": "Conversations refresh triggered"})
    except Exception as e:
        logger.error(f"❌ Error in /refresh_conversations: {str(e)}")
        return jsonify({"error": "Failed to trigger refresh"}), 500

# SocketIO Events
@socketio.on("connect")
def handle_connect():
    logger.info("✅ Client connected to SocketIO")
    if current_user.is_authenticated:
        emit("connection_status", {"status": "connected", "agent": current_user.username})
    else:
        emit("connection_status", {"status": "connected", "agent": None})

@socketio.on("disconnect")
def handle_disconnect():
    logger.info("ℹ️ Client disconnected from SocketIO")

@socketio.on("join_conversation")
def handle_join_conversation(data):
    convo_id = data.get("conversation_id")
    if not convo_id:
        logger.error("Missing conversation_id in join_conversation")
        emit("error", {"message": "Missing conversation_id"})
        return
    room = f"conversation_{convo_id}"
    join_room(room)
    logger.info(f"Agent joined room: {room} for convo_id: {convo_id}")
    emit("room_joined", {"room": room, "convo_id": convo_id}, room=room)

@socketio.on("leave_conversation")
def handle_leave_conversation(data):
    convo_id = data.get("conversation_id")
    if not convo_id:
        logger.error("Missing conversation_id in leave_conversation")
        emit("error", {"message": "Missing conversation_id"})
        return
    room = f"conversation_{convo_id}"
    leave_room(room)
    logger.info(f"Agent left room: {room}")
    emit("room_left", {"room": room, "convo_id": convo_id})

@socketio.on("agent_message")
def handle_agent_message(data):
    start_time = time.time()
    logger.info("Starting handle_agent_message")
    try:
        if not current_user.is_authenticated:
            logger.error("Unauthorized agent message attempt")
            emit("error", {"message": "Unauthorized"})
            return

        convo_id = data.get("conversation_id")
        message = data.get("message")
        if not convo_id or not message:
            logger.error("Missing conversation_id or message in agent_message")
            emit("error", {"message": "Missing conversation_id or message"})
            return

        try:
            convo_id = int(convo_id)
            if convo_id <= 0:
                raise ValueError("Conversation ID must be a positive integer")
        except ValueError:
            logger.error(f"❌ Invalid conversation_id format: {convo_id}")
            emit("error", {"message": "Invalid conversation ID format"})
            return

        # Verify the conversation exists and get the chat_id and channel
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT chat_id, channel, username FROM conversations WHERE id = %s",
                (convo_id,)
            )
            convo = c.fetchone()
            if not convo:
                logger.error(f"Conversation not found: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                return

            chat_id = convo["chat_id"]
            channel = convo["channel"]
            username = convo["username"]
            release_db_connection(conn)

        # Log the agent's message
        timestamp = log_message(convo_id, username, message, "agent")

        # Invalidate message cache for this conversation
        cache_key = f"messages:{convo_id}:*"
        keys = redis_client.keys(cache_key)
        if keys:
            redis_client.delete(*keys)
            logger.info(f"Invalidated message cache for convo_id {convo_id}")

        # Emit the message to the room
        room = f"conversation_{convo_id}"
        emit(
            "new_message",
            {
                "convo_id": convo_id,
                "message": message,
                "sender": "agent",
                "timestamp": timestamp,
                "username": username
            },
            room=room
        )

        # If the channel is WhatsApp, send the message to the user
        if channel == "whatsapp":
            send_whatsapp_message(chat_id, message)

        logger.info(f"Finished handle_agent_message in {time.time() - start_time:.2f} seconds")
    except Exception as e:
        logger.error(f"❌ Error in handle_agent_message: {str(e)}")
        emit("error", {"message": "Failed to send message"})

# Run the application
if __name__ == "__main__":
    logger.info("Starting Flask-SocketIO server")
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
