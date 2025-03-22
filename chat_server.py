import gevent
from gevent import monkey
monkey.patch_all()

from flask import Flask, render_template, request, jsonify, session, redirect
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask import Response
import openai
import psycopg2
from psycopg2.extras import DictCursor
import os
import requests
import json
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from datetime import datetime, timezone, timedelta
import time
import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from psycopg2.pool import SimpleConnectionPool
from cachetools import TTLCache
from werkzeug.security import generate_password_hash, check_password_hash

# Set up logging with both stream and file handlers
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chat_server")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(stream_handler)
# Add file handler for persistent logging on Render
file_handler = logging.FileHandler("chat_server.log")
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

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = SECRET_KEY
CORS(app)
socketio = SocketIO(
    app,
    cors_allowed_origins=["http://localhost:5000", "https://hotel-chatbot-1qj5.onrender.com"],  # Restrict in production
    async_mode="gevent",
    ping_timeout=120,
    ping_interval=30,
    logger=True,
    engineio_logger=True
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize connection pool
database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
db_pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,  # Reduced to avoid exceeding Render's connection limits
    dsn=database_url
)

# Cache for ai_enabled setting with 5-second TTL
settings_cache = TTLCache(maxsize=1, ttl=5)

# Import tasks after app and logger are initialized to avoid circular imports
from tasks import process_whatsapp_message, send_whatsapp_message_task

# Initialize OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    logger.error("⚠️ OPENAI_API_KEY not set in environment variables")
    raise ValueError("OPENAI_API_KEY not set")

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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("⚠️ TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
if not INSTAGRAM_ACCESS_TOKEN:
    logger.error("⚠️ INSTAGRAM_ACCESS_TOKEN not set in environment variables")
    raise ValueError("INSTAGRAM_ACCESS_TOKEN not set")
INSTAGRAM_API_URL = "https://graph.instagram.com/v20.0"

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

# Database connection with connection pooling
def get_db_connection():
    try:
        conn = db_pool.getconn()
        conn.cursor_factory = DictCursor
        logger.info("✅ Database connection retrieved from pool")
        return conn
    except Exception as e:
        logger.error(f"❌ Failed to get database connection: {str(e)}")
        raise Exception(f"Failed to get database connection: {str(e)}")

def release_db_connection(conn):
    if conn:
        try:
            db_pool.putconn(conn)
            logger.info("✅ Database connection returned to pool")
        except Exception as e:
            logger.error(f"❌ Failed to return database connection to pool: {str(e)}")

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
                booking_intent INTEGER DEFAULT 0,
                handoff_notified INTEGER DEFAULT 0,
                visible_in_conversations INTEGER DEFAULT 1,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
            logger.info("Created conversations table")

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
                password_hash TEXT NOT NULL  -- Renamed to password_hash for clarity
            )''')
            logger.info("Created agents table")

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
                    "INSERT INTO conversations (username, chat_id, channel, assigned_agent, ai_enabled, booking_intent, last_updated) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    ('TestUser1', '123456789', 'whatsapp', None, 1, 0, test_timestamp1)
                )
                convo_id1 = c.fetchone()['id']
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                    (convo_id1, 'TestUser1', 'Hello, I need help!', 'user', test_timestamp1)
                )
                test_timestamp2 = "2025-03-22T00:00:01Z"
                c.execute(
                    "INSERT INTO conversations (username, chat_id, channel, assigned_agent, ai_enabled, booking_intent, last_updated) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    ('TestUser2', '987654321', 'whatsapp', None, 1, 0, test_timestamp2)
                )
                convo_id2 = c.fetchone()['id']
                c.execute(
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                    (convo_id2, 'TestUser2', 'Can I book a room?', 'user', test_timestamp2)
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
                        "INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations, last_updated) "
                        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        (f"test_user_{i}", f"test_chat_{i}", "test", 1, 0, datetime.now(timezone.utc).isoformat())
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
            c = conn.cursor()
            c.execute(
                "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
                "VALUES (%s, %s, %s, %s, %s)",
                (convo_id, username, message, sender, timestamp)
            )
            conn.commit()
            logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
            release_db_connection(conn)
        return timestamp
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
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, username FROM agents WHERE id = %s", (agent_id,))
            agent = c.fetchone()
            if agent:
                release_db_connection(conn)
                logger.info(f"Finished load_user for agent_id {agent_id} in {time.time() - start_time:.2f} seconds")
                return Agent(agent['id'], agent['username'])
            release_db_connection(conn)
        logger.info(f"Finished load_user for agent_id {agent_id} (not found) in {time.time() - start_time:.2f} seconds")
        return None
    except Exception as e:
        logger.error(f"❌ Error in load_user: {str(e)}")
        return None

@app.route("/login", methods=["GET", "POST"])
def login():
    start_time = time.time()
    logger.info("Starting /login endpoint")
    try:
        if request.method == "GET":
            if current_user.is_authenticated:
                logger.info(f"User already authenticated, redirecting in {time.time() - start_time:.2f} seconds")
                return redirect(request.args.get("next", "/conversations"))
            logger.info(f"Rendering login page in {time.time() - start_time:.2f} seconds")
            return render_template("login.html")
        
        data = request.get_json()
        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            logger.error("❌ Missing username or password in /login request")
            return jsonify({"message": "Missing username or password"}), 400
        
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, username, password_hash FROM agents WHERE username = %s",
                (username,)
            )
            agent = c.fetchone()
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
        logger.error(f"❌ Error in /login: {e}")
        return jsonify({"error": "Failed to login"}), 500

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
                c.execute(
                    "INSERT INTO settings (key, value, last_updated) VALUES (%s, %s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = %s, last_updated = %s",
                    (key, value, current_timestamp, value, current_timestamp)
                )
                conn.commit()
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
                "SELECT id, username, channel, assigned_agent FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC"
            )
            conversations = [{"id": row["id"], "username": row["username"], "channel": row["channel"], "assigned_agent": row["assigned_agent"]} for row in c.fetchall()]
            release_db_connection(conn)
            logger.info(f"Finished /conversations in {time.time() - start_time:.2f} seconds")
            return jsonify(conversations)
    except Exception as e:
        logger.error(f"❌ Error in /conversations: {e}")
        return jsonify({"error": "Failed to fetch conversations"}), 500

@app.route("/handoff", methods=["POST"])
@login_required
def handoff():
    start_time = time.time()
    logger.info("Starting /handoff endpoint")
    try:
        data = request.get_json()
        convo_id = data.get("conversation_id")
        if not convo_id:
            logger.error("Missing conversation_id in /handoff")
            return jsonify({"error": "Missing conversation_id"}), 400
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE conversations SET assigned_agent = %s, ai_enabled = 0, last_updated = %s WHERE id = %s",
                (current_user.username, datetime.now(timezone.utc).isoformat(), convo_id)
            )
            conn.commit()
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
        if not convo_id:
            logger.error("Missing conversation_id in /handback-to-ai")
            return jsonify({"error": "Missing conversation_id"}), 400
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE conversations SET assigned_agent = NULL, ai_enabled = 1, last_updated = %s WHERE id = %s",
                (datetime.now(timezone.utc).isoformat(), convo_id)
            )
            conn.commit()
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
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT visible_in_conversations FROM conversations WHERE id = %s",
                (convo_id,)
            )
            result = c.fetchone()
            release_db_connection(conn)
            logger.info(f"Finished /check-visibility in {time.time() - start_time:.2f} seconds")
            return jsonify({"visible": bool(result["visible_in_conversations"])})
    except Exception as e:
        logger.error(f"❌ Error in /check-visibility: {e}")
        return jsonify({"error": "Failed to check visibility"}), 500

@app.route("/all-whatsapp-messages", methods=["GET"])
@login_required
def get_all_whatsapp_messages():
    start_time = time.time()
    logger.info("Starting /all-whatsapp-messages endpoint")
    try:
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
            logger.info(f"Found {len(conversations)} conversations: {[(c[0], c[1]) for c in conversations]}")
            result = [
                {
                    "convo_id": convo["id"],
                    "chat_id": convo["chat_id"],
                    "username": convo["username"],
                    "last_updated": convo["last_updated"]
                }
                for convo in conversations
            ]
            release_db_connection(conn)
            logger.info(f"Finished /all-whatsapp-messages in {time.time() - start_time:.2f} seconds")
            return jsonify({"conversations": result})
    except Exception as e:
        logger.error(f"Error fetching all WhatsApp messages: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to fetch conversations"}), 500

@app.route("/messages/<int:convo_id>", methods=["GET"])
@login_required
def get_messages_for_conversation(convo_id):
    start_time = time.time()
    logger.info(f"Starting /messages/{convo_id} endpoint")
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT username FROM conversations WHERE id = %s",
                (convo_id,)
            )
            convo = c.fetchone()
            if not convo:
                logger.error(f"❌ Conversation not found: {convo_id}")
                release_db_connection(conn)
                return jsonify({"error": "Conversation not found"}), 404

            c.execute(
                "SELECT message, sender, timestamp FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 50",
                (convo_id,)
            )
            messages = c.fetchall()
            messages = [
                {"message": msg["message"], "sender": msg["sender"], "timestamp": msg["timestamp"]}
                for msg in messages
            ][::-1]
            logger.info(f"✅ Fetched {len(messages)} messages for convo_id {convo_id}")
            release_db_connection(conn)

            logger.info(f"Finished /messages/{convo_id} in {time.time() - start_time:.2f} seconds")
            return jsonify({
                "username": convo["username"],
                "messages": messages
            })
    except Exception as e:
        logger.error(f"❌ Error in /messages/{convo_id}: {str(e)}")
        return jsonify({"error": "Failed to fetch messages"}), 500

# Messaging Helper Functions
def send_telegram_message(chat_id, text):
    start_time = time.time()
    logger.info(f"Starting send_telegram_message to {chat_id}")
    url = f"{TELEGRAM_API_URL}/sendMessage"
    if not chat_id or not isinstance(chat_id, str):
        logger.error(f"❌ Invalid chat_id: {chat_id}")
        return False
    if not text or not isinstance(text, str):
        logger.error(f"❌ Invalid text: {text}")
        return False
    if len(text) > 4096:
        logger.error(f"❌ Text exceeds 4096 characters: {len(text)}")
        text = text[:4093] + "..."

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    headers = {"Content-Type": "application/json"}

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(f"✅ Sent Telegram message to {chat_id}: {text}")
            time.sleep(0.5)
            logger.info(f"Finished send_telegram_message in {time.time() - start_time:.2f} seconds")
            return True
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ Telegram API error (Attempt {attempt + 1}/{max_retries}): {str(e)}, Response: {e.response.text}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Telegram request failed (Attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return False
    logger.error(f"❌ Failed to send Telegram message after {max_retries} attempts")
    return False

def send_whatsapp_message(phone_number, text):
    logger.info(f"Offloading WhatsApp message to Celery task for {phone_number}")
    return send_whatsapp_message_task.delay(phone_number, text)

def send_instagram_message(user_id, text):
    start_time = time.time()
    logger.info(f"Starting send_instagram_message to {user_id}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                json={"recipient": {"id": user_id}, "message": {"text": text}},
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"✅ Sent Instagram message to {user_id}: {text}")
            logger.info(f"Finished send_instagram_message in {time.time() - start_time:.2f} seconds")
            return True
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ Instagram API error (Attempt {attempt + 1}/{max_retries}): {str(e)}, Response: {e.response.text}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Instagram request failed (Attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return False
    logger.error(f"❌ Failed to send Instagram message after {max_retries} attempts")
    return False

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
                break  # Success, move to next date
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

def ai_respond(message, convo_id):
    start_time = time.time()
    logger.info(f"Starting ai_respond for convo_id {convo_id}: {message}")
    try:
        date_match = re.search(
            r'(?:are rooms available|availability|do you have any rooms|rooms available|what about|'
            r'¿hay habitaciones disponibles?|disponibilidad|¿tienen habitaciones?|habitaciones disponibles?|qué tal)?\s*'
            r'(?:from|on|de|el)?\s*'
            r'(?:(?:([A-Za-z]{3,9})\s+(\d{1,2}(?:st|nd|rd|th)?))|(?:(\d{1,2})\s*(?:de)?\s*([A-Za-z]{3,9})))'
            r'(?:\s*(?:to|a|al|until|hasta)?\s*'
            r'(?:(?:([A-Za-z]{3,9})\s+(\d{1,2}(?:st|nd|rd|th)?))|(?:(\d{1,2})\s*(?:de)?\s*([A-Za-z]{3,9}))))?',
            message.lower()
        )
        if date_match:
            month1_en, day1_en, day1_es, month1_es, month2_en, day2_en, day2_es, month2_es = date_match.groups()
            current_year = datetime.now().year
            spanish_to_english_months = {
                "enero": "January", "febrero": "February", "marzo": "March", "abril": "April",
                "mayo": "May", "junio": "June", "julio": "July", "agosto": "August",
                "septiembre": "September", "octubre": "October", "noviembre": "November", "diciembre": "December"
            }

            if month1_en and day1_en:
                check_in_str = f"{month1_en} {day1_en}"
                check_in_str = re.sub(r'(st|nd|rd|th)', '', check_in_str).strip()
                check_in = datetime.strptime(f"{check_in_str} {current_year}", '%B %d %Y')
            elif day1_es and month1_es:
                month1_en = spanish_to_english_months.get(month1_es.lower(), month1_es)
                check_in_str = f"{month1_en} {day1_es}"
                check_in = datetime.strptime(f"{check_in_str} {current_year}", '%B %d %Y')
            else:
                result = "Sorry, I couldn’t understand the dates. Please use a format like 'March 20' or '20 de marzo'." if "sorry" in message.lower() else \
                       "Lo siento, no entendí las fechas. Por favor, usa un formato como '20 de marzo' o 'March 20'."
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
                result = "The check-out date must be after the check-in date. Please provide a valid range." if "sorry" in message.lower() else \
                       "La fecha de salida debe ser posterior a la fecha de entrada. Por favor, proporciona un rango válido."
                logger.info(f"Finished ai_respond (invalid date range) in {time.time() - start_time:.2f} seconds")
                return result

            availability = check_availability(check_in, check_out)
            if "are available" in availability.lower():
                booking_intent = f"{check_in.strftime('%Y-%m-%d')} to {check_out.strftime('%Y-%m-%d')}"
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE conversations SET booking_intent = %s WHERE id = %s",
                        (booking_intent, convo_id)
                    )
                    conn.commit()
                    release_db_connection(conn)
                response = f"{availability}. Would you like to book?" if "sorry" in message.lower() else \
                           f"{availability.replace('are available', 'están disponibles')}. ¿Te gustaría reservar?"
            else:
                response = availability if "sorry" in message.lower() else \
                           availability.replace("are not available", "no están disponibles").replace("fully booked", "completamente reservado")
            logger.info(f"Finished ai_respond (availability check) in {time.time() - start_time:.2f} seconds")
            return response

        is_spanish = any(spanish_word in message.lower() for spanish_word in ["reservar", "habitación", "disponibilidad"])
        if "book" in message.lower() or "booking" in message.lower() or "reservar" in message.lower():
            result = "I’ll connect you with a team member to assist with your booking." if not is_spanish else \
                   "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
            logger.info(f"Finished ai_respond (booking intent) in {time.time() - start_time:.2f} seconds")
            return result

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT message, sender, timestamp FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 10",
                (convo_id,)
            )
            messages = c.fetchall()
            release_db_connection(conn)
        conversation_history = [
            {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel customer service and sales agent for Amapola Resort. Use the provided business information and Q&A to answer guest questions. Maintain conversation context. Detect the user's language (English or Spanish) based on their input and respond in the same language. If you don’t know the answer or the query is complex, respond with the appropriate escalation message in the user's language. Do not mention room types or pricing unless specifically asked."}
        ]
        for msg in messages:
            message_text, sender, timestamp = msg['message'], msg['sender'], msg['timestamp']
            role = "user" if sender == "user" else "assistant"
            conversation_history.append({"role": role, "content": message_text})
        conversation_history.append({"role": "user", "content": message})

        retry_attempts = 2
        for attempt in range(retry_attempts):
            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=conversation_history,
                    max_tokens=150
                )
                ai_reply = response.choices[0].message.content.strip()
                logger.info(f"✅ AI reply: {ai_reply}")
                if "sorry" in ai_reply.lower() or "lo siento" in ai_reply.lower():
                    logger.info(f"Finished ai_respond (AI sorry) in {time.time() - start_time:.2f} seconds")
                    return ai_reply
                logger.info(f"Finished ai_respond (AI success) in {time.time() - start_time:.2f} seconds")
                return ai_reply
            except Exception as e:
                logger.error(f"❌ OpenAI error (Attempt {attempt + 1}): {str(e)}")
                if attempt == retry_attempts - 1:
                    result = "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if not is_spanish else \
                           "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
                    logger.info(f"Finished ai_respond (OpenAI error) in {time.time() - start_time:.2f} seconds")
                    return result
                time.sleep(1)
                continue
    except Exception as e:
        logger.error(f"❌ Error in ai_respond for convo_id {convo_id}: {str(e)}")
        result = "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if "sorry" in message.lower() else \
               "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
        logger.info(f"Finished ai_respond (general error) in {time.time() - start_time:.2f} seconds")
        return result

def detect_language(message, convo_id):
    start_time = time.time()
    logger.info(f"Starting detect_language for convo_id {convo_id}")
    spanish_keywords = ["hola", "gracias", "reservar", "habitación", "disponibilidad", "marzo", "abril"]
    if any(keyword in message.lower() for keyword in spanish_keywords):
        logger.info(f"Finished detect_language (Spanish detected) in {time.time() - start_time:.2f} seconds")
        return "es"
    
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT message FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 5",
            (convo_id,)
        )
        messages = c.fetchall()
        release_db_connection(conn)
        for msg in messages:
            if any(keyword in msg['message'].lower() for keyword in spanish_keywords):
                logger.info(f"Finished detect_language (Spanish detected in history) in {time.time() - start_time:.2f} seconds")
                return "es"
    
    logger.info(f"Finished detect_language (English detected) in {time.time() - start_time:.2f} seconds")
    return "en"

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

# Socket.IO Event Handlers
@socketio.on("connect")
def handle_connect():
    logger.info("✅ Client connected to Socket.IO")
    emit("status", {"message": "Connected to server"})

@socketio.on("disconnect")
def handle_disconnect():
    logger.info("ℹ️ Client disconnected from Socket.IO")

@socketio.on("join_conversation")
def handle_join_conversation(data):
    start_time = time.time()
    logger.info(f"Starting handle_join_conversation: {data}")
    try:
        convo_id = data.get("conversation_id")
        if not convo_id:
            logger.error("❌ Missing conversation_id in join_conversation event")
            emit("error", {"message": "Missing conversation ID"})
            return
        join_room(str(convo_id))
        logger.info(f"✅ Client joined conversation {convo_id}")
        emit("status", {"message": f"Joined conversation {convo_id}"})
        logger.info(f"Finished handle_join_conversation in {time.time() - start_time:.2f} seconds")
    except Exception as e:
        logger.error(f"❌ Error in handle_join_conversation: {str(e)}")
        emit("error", {"message": "Failed to join conversation"})

@socketio.on("leave_conversation")
def handle_leave_conversation(data):
    start_time = time.time()
    logger.info(f"Starting handle_leave_conversation: {data}")
    try:
        convo_id = data.get("conversation_id")
        if not convo_id:
            logger.error("❌ Missing conversation_id in leave_conversation event")
            emit("error", {"message": "Missing conversation ID"})
            return
        leave_room(str(convo_id))
        logger.info(f"✅ Client left conversation room: {convo_id}")
        emit("status", {"message": f"Left conversation {convo_id}"})
        logger.info(f"Finished handle_leave_conversation in {time.time() - start_time:.2f} seconds")
    except Exception as e:
        logger.error(f"❌ Error in handle_leave_conversation: {str(e)}")
        emit("error", {"message": "Failed to leave conversation"})

@socketio.on("agent_message")
def handle_agent_message(data):
    start_time = time.time()
    logger.info(f"Starting handle_agent_message: {data}")
    try:
        convo_id = data.get("convo_id")
        message = data.get("message")
        channel = data.get("channel", "whatsapp")
        if not convo_id or not message:
            logger.error("❌ Missing convo_id or message in agent_message event")
            emit("error", {"message": "Missing required fields"})
            return

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT username, chat_id, channel FROM conversations WHERE id = %s",
                (convo_id,)
            )
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                release_db_connection(conn)
                return
            username, chat_id, convo_channel = result
            release_db_connection(conn)

        agent_timestamp = log_message(convo_id, username, message, "agent")
        emit("new_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": "agent",
            "channel": convo_channel,
            "timestamp": agent_timestamp,
        }, room=str(convo_id))
        emit("live_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": "agent",
            "chat_id": chat_id,
            "username": username,
            "timestamp": agent_timestamp,
        })

        if convo_channel == "whatsapp":
            if not chat_id:
                logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp: No chat_id found", "channel": "whatsapp"})
                return
            task = send_whatsapp_message(chat_id, message)
            logger.info(f"Offloaded WhatsApp message for convo_id {convo_id} to Celery task")

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE conversations SET last_updated = %s WHERE id = %s",
                (agent_timestamp, convo_id)
            )
            conn.commit()
            release_db_connection(conn)
        
        logger.info(f"Finished handle_agent_message in {time.time() - start_time:.2f} seconds")
    except Exception as e:
        logger.error(f"❌ Error in agent_message event: {str(e)}")
        emit("error", {"message": "Failed to process agent message"})

if __name__ == "__main__":
    try:
        logger.info("✅ Starting Flask-SocketIO server")
        socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)  # Debug disabled for production
    except Exception as e:
        logger.error(f"❌ Failed to start server: {str(e)}")
        raise
