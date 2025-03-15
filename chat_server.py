import gevent.monkey
gevent.monkey.patch_all()

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import openai
import sqlite3
import os
import requests
import json
from datetime import datetime, timedelta
import time
import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from contextlib import contextmanager
from dateutil.parser import parse as parse_date
import json
from googleapiclient.discovery import build
from googleapiclient.discovery_cache.base import Cache
class NullCache(Cache):
    def get(self, url):
        return None
    def set(self, url, content):
        pass

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecretkey")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.init_app(app)

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

# Parse the service account key from the environment variable
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("⚠️ TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_API_URL = "https://graph.instagram.com/v20.0"

# Placeholder for WhatsApp API (to be configured later)
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", None)
WHATSAPP_API_URL = "https://api.whatsapp.com"  # Update with actual URL

DB_NAME = "chatbot.db"

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

# Parse room types and prices from TRAINING_DOCUMENT
ROOM_TYPES = []
ROOM_PRICES = {}
try:
    room_types_section = TRAINING_DOCUMENT.split("**Room Types**")[1].split("**Amenities**")[0]
    for line in room_types_section.splitlines():
        line = line.strip()
        if line.startswith("-"):
            # Extract room type and price information
            parts = line.split(":")[1].strip().split("(")
            room_type_full = parts[0].split(":")[0].strip()  # e.g., "Standard Room $170/night"
            room_type = room_type_full.split("$")[0].strip().lower()  # e.g., "standard room"
            ROOM_TYPES.append(room_type)

            # Parse the price (regular and promotion if present)
            price_part = room_type_full.split("$")[1].split("/")[0].strip()  # e.g., "170"
            regular_price = float(price_part)

            promo_price = None
            promo_end_date = None
            if len(parts) > 1:  # Check for promotion
                promo_info = parts[1].replace(")", "").strip()
                if "Promotion" in promo_info:
                    promo_parts = promo_info.split("until")
                    promo_price_str = promo_parts[0].replace("Promotion:", "").strip().split("$")[1].split("/")[0].strip()
                    promo_price = float(promo_price_str)
                    promo_end_date_str = promo_parts[1].strip()
                    promo_end_date = datetime.strptime(promo_end_date_str, "%B %d, %Y").date()

            ROOM_PRICES[room_type] = {
                "regular_price": regular_price,
                "promo_price": promo_price,
                "promo_end_date": promo_end_date
            }
            logger.info(f"✅ Parsed room type: {room_type}, prices: {ROOM_PRICES[room_type]}")
except IndexError:
    # Fallback to a default list of room types and prices matching the training document
    ROOM_TYPES = ["standard room", "junior suite", "apartment", "villa"]
    ROOM_PRICES = {
        "standard room": {"regular_price": 170.0, "promo_price": None, "promo_end_date": None},
        "junior suite": {"regular_price": 200.0, "promo_price": None, "promo_end_date": None},
        "apartment": {"regular_price": 280.0, "promo_price": None, "promo_end_date": None},
        "villa": {"regular_price": 280.0, "promo_price": None, "promo_end_date": None}
    }
    logger.warning("⚠️ Failed to parse room types and prices from TRAINING_DOCUMENT; using default room types and prices")

@contextmanager
def get_db_connection():
    """Context manager for SQLite database connection to ensure proper handling."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)  # Allow connection from any thread
    try:
        yield conn
    finally:
        conn.close()

def initialize_database():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            latest_message TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            assigned_agent TEXT DEFAULT NULL,
            channel TEXT DEFAULT 'dashboard',
            opted_in INTEGER DEFAULT 0,
            ai_enabled INTEGER DEFAULT 1,
            handoff_notified INTEGER DEFAULT 0,
            visible_in_conversations INTEGER DEFAULT 0,
            booking_state TEXT DEFAULT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            check_in DATE,
            check_out DATE,
            guests INTEGER,
            room_type TEXT,
            total_cost REAL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )''')
        c.execute("DROP TABLE IF EXISTS messages")
        c.execute('''CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user TEXT NOT NULL,
            message TEXT NOT NULL,
            sender TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id))''')
        c.execute("SELECT COUNT(*) FROM agents")
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO agents (username, password) VALUES (?, ?)", ("agent1", "password123"))
            logger.info("✅ Added test agent: agent1/password123")
        conn.commit()
    logger.info("✅ Database initialized")

initialize_database()

def add_test_conversations():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM conversations")
        if c.fetchone()[0] == 0:
            test_conversations = [
                ("guest1", "Hi, can I book a room?"),
                ("guest2", "What’s the check-in time?"),
                ("guest3", "Do you have a pool?")]
            convo_ids = []
            for username, message in test_conversations:
                c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                          (username, message, "dashboard"))
                convo_ids.append(c.lastrowid)
            test_messages = [
                (convo_ids[0], "guest1", "Hi, can I book a room?", "user"),
                (convo_ids[0], "AI", "Yes, I can help with that! What dates are you looking for?", "ai"),
                (convo_ids[1], "guest2", "What’s the check-in time?", "user"),
                (convo_ids[1], "AI", "Check-in is at 3 PM.", "ai"),
                (convo_ids[2], "guest3", "Do you have a pool?", "user"),
                (convo_ids[2], "AI", "Yes, we have an outdoor pool!", "ai")]
            for convo_id, user, message, sender in test_messages:
                c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (?, ?, ?, ?)", 
                          (convo_id, user, message, sender))
            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_ids[0],))
            conn.commit()
            logger.info("✅ Test conversations added.")

add_test_conversations()

class Agent(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(agent_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, username FROM agents WHERE id = ?", (agent_id,))
        agent = c.fetchone()
        if agent:
            return Agent(agent[0], agent[1])
    return None

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        logger.error("❌ Missing username or password in /login request")
        return jsonify({"message": "Missing username or password"}), 400
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, username FROM agents WHERE username = ? AND password = ?", (username, password))
        agent = c.fetchone()
        if agent:
            agent_obj = Agent(agent[0], agent[1])
            login_user(agent_obj)
            logger.info(f"✅ Login successful for agent: {agent[1]}")
            return jsonify({"message": "Login successful", "agent": agent[1]})
    logger.error("❌ Invalid credentials in /login request")
    return jsonify({"message": "Invalid credentials"}), 401

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    logger.info("✅ Logout successful")
    return jsonify({"message": "Logged out successfully"})

@app.route("/conversations", methods=["GET"])
def get_conversations():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, username, latest_message, assigned_agent, channel, visible_in_conversations FROM conversations ORDER BY last_updated DESC")
            raw_conversions = c.fetchall()
            logger.info(f"✅ Raw conversations from database: {raw_conversions}")
            c.execute("SELECT id, username, channel, assigned_agent FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC")
            conversations = [{"id": row[0], "username": row[1], "channel": row[2], "assigned_agent": row[3]} for row in c.fetchall()]
            logger.info(f"✅ Fetched conversations for dashboard: {conversations}")
        return jsonify(conversations)
    except Exception as e:
        logger.error(f"❌ Error fetching conversations: {e}")
        return jsonify({"error": "Failed to fetch conversations"}), 500

@app.route("/check-visibility", methods=["GET"])
def check_visibility():
    convo_id = request.args.get("conversation_id")
    if not convo_id:
        logger.error("❌ Missing conversation ID in check-visibility request")
        return jsonify({"error": "Missing conversation ID"}), 400
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
        if result:
            logger.info(f"✅ Visibility check for convo ID {convo_id}: {bool(result[0])}")
            return jsonify({"visible": bool(result[0])})
        logger.error(f"❌ Conversation not found: {convo_id}")
        return jsonify({"error": "Conversation not found"}), 404
    except Exception as e:
        logger.error(f"❌ Error checking visibility for convo ID {convo_id}: {e}")
        return jsonify({"error": "Failed to check visibility"}), 500

@app.route("/messages", methods=["GET"])
def get_messages():
    convo_id = request.args.get("conversation_id")
    if not convo_id:
        logger.error("❌ Missing conversation ID in get-messages request")
        return jsonify({"error": "Missing conversation ID"}), 400
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        # Fetch messages
        c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC", (convo_id,))
        messages = [{"message": row[0], "sender": row[1], "timestamp": row[2]} for row in c.fetchall()]
        # Fetch username
        c.execute("SELECT username FROM conversations WHERE id = ?", (convo_id,))
        username_result = c.fetchone()
        username = username_result[0] if username_result else "Unknown"
        conn.close()
        logger.info(f"✅ Fetched {len(messages)} messages for convo ID {convo_id}")
        return jsonify({"messages": messages, "username": username})
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo ID {convo_id}: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

def log_message(convo_id, user, message, sender):
    try:
        if message is None:
            logger.error(f"❌ Attempted to log a None message for convo_id {convo_id}, user {user}, sender {sender}")
            message = "Error: Message content unavailable"
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (?, ?, ?, ?)", 
                      (convo_id, user, message, sender))
            c.execute("UPDATE conversations SET latest_message = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", 
                      (message, convo_id))
            if sender == "agent":
                c.execute("UPDATE conversations SET ai_enabled = 0 WHERE id = ?", (convo_id,))
                logger.info(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
            conn.commit()
        logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
    except Exception as e:
        logger.error(f"❌ Error logging message for convo_id {convo_id}: {e}")
        raise

def send_telegram_message(chat_id, text):
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"✅ Sent Telegram message to {chat_id}: {text}")
        time.sleep(0.5)
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message to {chat_id}: {str(e)}")
        raise

# Placeholder for WhatsApp message sending (to be implemented later)
def send_whatsapp_message(phone_number, text):
    raise NotImplementedError("WhatsApp messaging not yet implemented")

# Placeholder for Instagram message sending (to be implemented later)
def send_instagram_message(user_id, text):
    raise NotImplementedError("Instagram messaging not yet implemented")

def check_availability(check_in, check_out):
    """Check Google Calendar for 'Fully Booked' events between check_in and check_out."""
    try:
        start_date = check_in.strftime("%Y-%m-%dT00:00:00Z")
        end_date = check_out.strftime("%Y-%m-%dT23:59:59Z")
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        for event in events:
            if event.get('summary') == "Fully Booked":
                logger.info(f"✅ Found 'Fully Booked' event for {check_in} to {check_out}")
                return True
        logger.info(f"✅ No 'Fully Booked' event found for {check_in} to {check_out}")
        return False
    except Exception as e:
        logger.error(f"❌ Google Calendar API error: {str(e)}")
        return True  # Assume unavailable on error to be safe

def ai_respond(message, convo_id):
    """
    Generate an AI response for the given message and conversation ID using OpenAI.
    Args:
        message (str): The user's message.
        convo_id (int): The conversation ID.
    Returns:
        str: The AI's response.
    """
    logger.info(f"Generating AI response for convo_id {convo_id}: {message}")
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT user, message, sender FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 10", (convo_id,))
            messages = c.fetchall()
        conversation_history = [
            {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Maintain conversation context and provide relevant follow-up responses. Escalate to a human if the query is complex or requires personal assistance."}
        ]
        for msg in messages:
            user, message_text, sender = msg
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
                model_used = response.model
                logger.info(f"✅ AI reply: {ai_reply}, Model: {model_used}")
                return ai_reply
            except Exception as e:
                logger.error(f"❌ OpenAI error (Attempt {attempt + 1}): {str(e)}")
                if attempt == retry_attempts - 1:
                    logger.info("✅ Set default AI reply due to repeated errors")
                    return "I’m sorry, I’m having trouble processing your request right now. Let me tranfer you to another agent."
                time.sleep(1)
                continue
    except Exception as e:
        logger.error(f"❌ Error in ai_respond for convo_id {convo_id}: {str(e)}")
        return "I’m sorry, I’m having trouble processing your request right now. Let me tranfer you to another agent."
        
# Insert extract_room_type_with_ai function here (starts at line 554)
def extract_room_type_with_ai(message):
    """
    Use OpenAI to extract the intended room type from the user's message.
    Args:
        message (str): The user's message.
    Returns:
        str: The matched room type, or None if no match is found.
    """
    prompt = f"""
    You are a hotel chatbot. The user has provided the following message: '{message}'
    The valid room types are: Standard Room, Deluxe Room, Suite.
    Determine which room type the user is referring to, if any. Respond with the exact room type (e.g., "Standard Room") or "None" if no match is found.
    Examples:
    - "I want a standard room" → "Standard Room"
    - "Give me a deluxe" → "Deluxe Room"
    - "I’d like a suite please" → "Suite"
    - "I want a basic room" → "Standard Room"
    - "I’d like a luxury room" → "Suite"
    - "Something else" → "None"
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a hotel chatbot helping with bookings."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=10
        )
        ai_reply = response.choices[0].message.content.strip()
        logger.info(f"AI extracted room type: {ai_reply} from message: {message}")
        if ai_reply == "None":
            return None
        # Ensure the AI's response matches one of the valid room types (case-insensitive)
        ai_reply_lower = ai_reply.lower()
        for room_type in ROOM_TYPES:
            if room_type in ai_reply_lower:
                return room_type
        return None
    except Exception as e:
        logger.error(f"❌ Error extracting room type with AI: {str(e)}")
        return None

# Add a global set to track processed message IDs (assuming Telegram provides a message ID)
PROCESSED_MESSAGES = set()

def handle_booking_flow(message, convo_id, chat_id, channel, message_id=None):
    """
    Handle the booking flow for a conversation.
    Args:
        message (str): The user's message.
        convo_id (int): The conversation ID.
        chat_id (str): The chat ID or username.
        channel (str): The communication channel (e.g., 'telegram').
        message_id (str, optional): The message ID to prevent duplicate processing.
    Returns:
        tuple: (bool, str) - (whether to continue with AI response, AI reply if any)
    """
    # Use message_id to prevent duplicate processing
    message_key = f"{convo_id}:{message_id}" if message_id else f"{convo_id}:{message}"
    if message_key in PROCESSED_MESSAGES:
        logger.info(f"Skipping duplicate message for convo_id {convo_id}, message_key: {message_key}")
        return (False, None)
    PROCESSED_MESSAGES.add(message_key)
    logger.info(f"Handling booking flow for convo_id {convo_id}, message: '{message}', chat_id: {chat_id}, channel: {channel}, message_id: {message_id}")

    # Load booking state from the database
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT booking_state FROM conversations WHERE id = ?", (convo_id,))
        result = c.fetchone()
        if result is None:
            logger.warning(f"No conversation found for convo_id {convo_id}, initializing new state")
            booking_state_dict = {}
        else:
            booking_state = result[0]
            booking_state_dict = json.loads(booking_state) if booking_state else {}
        logger.info(f"Initial booking state for convo_id {convo_id}: {booking_state_dict}")

    # Reset booking state if user starts a new booking
    if "book" in message.lower() and booking_state_dict.get("status") not in ["awaiting_dates", "awaiting_room_type", "awaiting_confirmation"]:
        booking_state_dict = {"status": "awaiting_dates"}
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (json.dumps(booking_state_dict), convo_id))
            c.execute("DELETE FROM bookings WHERE conversation_id = ?", (convo_id,))
            conn.commit()
        logger.info(f"Reset booking state to 'awaiting_dates' for convo_id {convo_id}")

    # Step 1: Collect and validate dates
    if booking_state_dict.get("status") == "awaiting_dates":
        logger.info(f"Processing 'awaiting_dates' state for message: '{message}'")
        # Try to parse dates from the message
        date_str = None
        for fmt in [r'(\w+\s+\d+\s+to\s+\w+\s+\d+)', r'(\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2})']:
            match = re.search(fmt, message, re.IGNORECASE)
            if match:
                date_str = match.group(0)
                break

        if not date_str:
            ai_reply = "I’d love to help you book! Please provide your check-in and check-out dates (e.g., 'March 10 to March 15' or '2025-03-10 to 2025-03-15')."
            logger.info(f"No dates found in message, prompting user: {ai_reply}")
            return (False, ai_reply)

        try:
            # Split the date string and parse dates
            date_parts = re.split(r'\s+to\s+', date_str.lower(), flags=re.IGNORECASE)
            check_in_str, check_out_str = date_parts
            check_in = parse_date(check_in_str, default=datetime.now())
            check_out = parse_date(check_out_str, default=datetime.now())
            logger.info(f"Parsed dates - check_in: {check_in}, check_out: {check_out}")

            # Ensure dates are in the future and check_out is after check_in
            now = datetime.now()
            if check_in < now:
                ai_reply = "Your check-in date must be in the future. Please provide new dates (e.g., 'March 10 to March 15')."
                logger.info(f"Invalid check-in date (past date): {ai_reply}")
                return (False, ai_reply)
            if check_out <= check_in:
                ai_reply = "Your check-out date must be after your check-in date. Please provide new dates (e.g., 'March 10 to March 15')."
                logger.info(f"Invalid date range (check_out <= check_in): {ai_reply}")
                return (False, ai_reply)

            # Store dates and move to next step
            booking_state_dict["check_in"] = check_in.strftime("%Y-%m-%d")
            booking_state_dict["check_out"] = check_out.strftime("%Y-%m-%d")
            booking_state_dict["status"] = "awaiting_room_type"
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (json.dumps(booking_state_dict), convo_id))
                c.execute("INSERT INTO bookings (conversation_id, check_in, check_out) VALUES (?, ?, ?)", 
                          (convo_id, check_in.strftime("%Y-%m-%d"), check_out.strftime("%Y-%m-%d")))
                conn.commit()
                # Verify the state was updated
                c.execute("SELECT booking_state FROM conversations WHERE id = ?", (convo_id,))
                updated_state = c.fetchone()[0]
                logger.info(f"After update, booking state for convo_id {convo_id}: {updated_state}")

            ai_reply = f"Great! For your stay from {check_in.strftime('%B %d')} to {check_out.strftime('%B %d')}, we have the following room options available:\n\n"
            room_options = []
            logger.info(f"ROOM_TYPES: {ROOM_TYPES}")  # Debug log for ROOM_TYPES
            logger.info(f"ROOM_PRICES: {ROOM_PRICES}")  # Debug log for ROOM_PRICES
            if not ROOM_TYPES or not ROOM_PRICES:
                logger.warning("ROOM_TYPES or ROOM_PRICES is empty or undefined")
                ai_reply += "Error: No room options available. Please contact support.\n\nWould you like to proceed with a specific room type or need assistance with anything else?"
            else:
                for idx, room_type in enumerate(ROOM_TYPES, 1):
                    display_type = room_type.replace(" ", " ").title()  # Convert to title case for display
                    price_info = ROOM_PRICES.get(room_type, {"regular_price": 0, "promo_price": None, "promo_end_date": None})
                    current_date = date.today()  # Use dynamic date instead of hardcoded
                    price_to_use = price_info["promo_price"] if price_info["promo_price"] and (not price_info["promo_end_date"] or price_info["promo_end_date"] >= current_date) else price_info["regular_price"]
                    room_option = f"{idx}. {display_type}: ${price_to_use}/night"
                    if price_info["promo_price"] and (not price_info["promo_end_date"] or price_info["promo_end_date"] >= current_date):
                        room_option += f" (currently on promotion for ${price_info['promo_price']}/night)"
                    room_options.append(room_option)
                ai_reply += "\n".join(room_options) + "\n\nWould you like to proceed with a specific room type or need assistance with anything else?"
            logger.info(f"Prompting for room type: {ai_reply}")
            return (False, ai_reply)

        except ValueError as e:
            ai_reply = "I couldn’t understand the dates. Please try a format like 'March 10 to March 15' or '2025-03-10 to 2025-03-15'."
            logger.info(f"Date parsing error: {str(e)}, response: {ai_reply}")
            return (False, ai_reply)

    # Step 2: Collect room type preference
    elif booking_state_dict.get("status") == "awaiting_room_type":
        logger.info(f"Processing 'awaiting_room_type' state for message: '{message}'")
        # Ensure dates are available in the state
        if "check_in" not in booking_state_dict or "check_out" not in booking_state_dict:
            ai_reply = "I need your check-in and check-out dates to proceed. Please provide them (e.g., 'March 10 to March 15')."
            booking_state_dict["status"] = "awaiting_dates"
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (json.dumps(booking_state_dict), convo_id))
                conn.commit()
            logger.info(f"Missing dates in state, resetting to 'awaiting_dates': {ai_reply}")
            return (False, ai_reply)

        # Validate the room type
        room_type_lower = message.lower()
        if room_type_lower not in ROOM_TYPES:
            ai_reply = f"Sorry, I didn’t recognize that room type. Please choose from: {', '.join([rt.title() for rt in ROOM_TYPES[:-1]])}, or {ROOM_TYPES[-1].title()}."
            logger.info(f"Invalid room type provided: '{message}', prompting again: {ai_reply}")
            return (False, ai_reply)

        # Store the selected room type
        booking_state_dict["room_type"] = room_type_lower
        booking_state_dict["status"] = "awaiting_confirmation"
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (json.dumps(booking_state_dict), convo_id))
            conn.commit()
            # Verify the state was updated
            c.execute("SELECT booking_state FROM conversations WHERE id = ?", (convo_id,))
            updated_state = c.fetchone()[0]
            logger.info(f"After room type selection, booking state for convo_id {convo_id}: {updated_state}")

        # Get the price for the selected room type
        price_info = ROOM_PRICES.get(room_type_lower, {"regular_price": 0, "promo_price": None, "promo_end_date": None})
        current_date = date.today()  # Use dynamic date instead of hardcoded
        price_to_use = price_info["promo_price"] if price_info["promo_price"] and (not price_info["promo_end_date"] or price_info["promo_end_date"] >= current_date) else price_info["regular_price"]

        check_in_date = datetime.strptime(booking_state_dict["check_in"], "%Y-%m-%d")
        check_out_date = datetime.strptime(booking_state_dict["check_out"], "%Y-%m-%d")
        ai_reply = f"The {room_type_lower.title()} is available for your stay from {check_in_date.strftime('%B %d')} to {check_out_date.strftime('%B %d')}. The {'promotional rate' if price_to_use == price_info['promo_price'] else 'rate'} is ${price_to_use}/night.\n\nWould you like to proceed with the booking? If so, please let me know, and I can assist you with the next steps!"
        logger.info(f"Prompting for confirmation: {ai_reply}")
        return (False, ai_reply)

    # Step 3: Await confirmation and hand off to agent
    elif booking_state_dict.get("status") == "awaiting_confirmation":
        logger.info(f"Processing 'awaiting_confirmation' state for message: '{message}'")
        # Ensure required state data is available
        if "check_in" not in booking_state_dict or "check_out" not in booking_state_dict or "room_type" not in booking_state_dict:
            ai_reply = "I need your check-in and check-out dates and room type to proceed. Let’s start over. Please provide your dates (e.g., 'March 10 to March 15')."
            booking_state_dict["status"] = "awaiting_dates"
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (json.dumps(booking_state_dict), convo_id))
                conn.commit()
            logger.info(f"Missing state data, resetting to 'awaiting_dates': {ai_reply}")
            return (False, ai_reply)

        # Check if the user confirms the booking
        if "yes" in message.lower():
            room_type = booking_state_dict["room_type"]
            ai_reply = f"Thanks for your room type preference! You’ve chosen {room_type.title()} from {booking_state_dict['check_in']} to {booking_state_dict['check_out']}. An agent will assist you on the dashboard to finalize your booking."
            logger.info(f"Handing off to agent with reply: '{ai_reply}'")

            # Update conversation state for handoff
            booking_state_dict["status"] = "handoff"
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET booking_state = ?, handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = ?", 
                          (json.dumps(booking_state_dict), convo_id))
                conn.commit()
                # Verify the state was updated
                c.execute("SELECT booking_state FROM conversations WHERE id = ?", (convo_id,))
                updated_state = c.fetchone()[0]
                logger.info(f"After handoff, booking state for convo_id {convo_id}: {updated_state}")

            # Trigger dashboard refresh
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": channel})
            logger.info(f"Emitted refresh_conversations event for convo_id {convo_id}")
            return (False, ai_reply)
        else:
            ai_reply = "I’m sorry, I didn’t understand your response. Would you like to proceed with the booking? Please confirm with 'Yes' or let me know how I can assist you further."
            logger.info(f"Prompting for confirmation again: {ai_reply}")
            return (False, ai_reply)

    logger.info(f"No matching booking state for convo_id {convo_id}, passing to default handler")
    return (True, None)

@app.route("/check-auth", methods=["GET"])
def check_auth():
    return jsonify({"is_authenticated": current_user.is_authenticated, "agent": current_user.username if current_user.is_authenticated else None})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    user_message = data.get("message")
    if not convo_id or not user_message:
        logger.error("❌ Missing required fields in /chat request")
        return jsonify({"error": "Missing required fields"}), 400
    try:
        logger.info("✅ Entering /chat endpoint")
        logger.info(f"✅ Fetching conversation details for convo_id {convo_id}")
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, channel, assigned_agent, ai_enabled FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
            username, channel, assigned_agent, ai_enabled = result if result else (None, None, None, None)
        if not username:
            logger.error(f"❌ Conversation not found: {convo_id}")
            return jsonify({"error": "Conversation not found"}), 404
        
        sender = "agent" if current_user.is_authenticated else "user"
        logger.info(f"✅ Processing /chat message as sender: {sender}")
        log_message(convo_id, username, user_message, sender)

        if sender == "agent":
            logger.info("✅ Sender is agent, emitting new_message event")
            socketio.emit("new_message", {"convo_id": convo_id, "message": user_message, "sender": "agent", "channel": channel})
            if channel == "telegram":
                try:
                    logger.info(f"Sending agent message to Telegram - To: {username}, Body: {user_message}")
                    send_telegram_message(username, user_message)
                    logger.info("✅ Agent message sent to Telegram: " + user_message)
                except Exception as e:
                    logger.error(f"❌ Telegram error sending agent message: {str(e)}")
                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": channel})
            logger.info("✅ Agent message processed successfully")
            return jsonify({"status": "success"})

        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if ai_enabled:
            logger.info("✅ AI is enabled, proceeding with AI response")
            # Check for HELP keyword
            if "HELP" in user_message.upper():
                ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
                logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + ai_reply)
            else:
                # Check booking flow
                continue_with_ai, ai_reply = handle_booking_flow(user_message, convo_id, username, channel)
                if not continue_with_ai:
                    logger.info("✅ Booking flow handled, using booking flow reply")
                else:
                    ai_reply = ai_respond(user_message, convo_id)

            logger.info("✅ Logging and emitting AI response")
            log_message(convo_id, "AI", ai_reply, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": channel})
            if channel == "telegram":
                try:
                    logger.info(f"Sending AI message to Telegram - To: {username}, Body: {ai_reply}")
                    send_telegram_message(username, ai_reply)
                    logger.info("✅ AI message sent to Telegram: " + ai_reply)
                except Exception as e:
                    logger.error(f"❌ Telegram error sending AI message: {str(e)}")
                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": channel})
            logger.info("✅ Checking for handoff condition")
            if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
                try:
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("SELECT handoff_notified, visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
                        result = c.fetchone()
                        handoff_notified, visible_in_conversations = result if result else (0, 0)
                    logger.info(f"✅ Handoff check for convo_id {convo_id}: handoff_notified={handoff_notified}, visible_in_conversations={visible_in_conversations}")
                    if not handoff_notified:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            c.execute("UPDATE conversations SET handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                            conn.commit()
                        time.sleep(3.0)
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                            updated_result = c.fetchone()
                        logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
                        logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                except Exception as e:
                    logger.error(f"❌ Error during handoff for convo_id {convo_id}: {e}")
            logger.info("✅ AI response processed successfully")
            return jsonify({"reply": ai_reply})
        else:
            logger.info("❌ AI disabled for convo_id: " + str(convo_id))
            return jsonify({"status": "Message received, AI disabled"})
    except Exception as e:
        logger.error(f"❌ Error in /chat endpoint: {str(e)}")
        return jsonify({"error": "Failed to process chat message"}), 500

@app.route("/telegram", methods=["POST"])
def telegram():
    update = request.get_json()
    logger.info(f"Received Telegram update: {update}")

    # Extract message details
    if "message" not in update:
        logger.warning("No message in Telegram update, returning OK")
        return jsonify({"status": "ok"}), 200

    message_data = update["message"]
    chat_id = str(message_data["chat"]["id"])
    text = message_data.get("text", "")
    message_id = str(message_data.get("message_id", ""))  # Extract message_id, default to empty string if not present

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent FROM conversations WHERE username = ? AND channel = 'telegram'", (chat_id,))
        result = c.fetchone()

        if not result:
            c.execute("INSERT INTO conversations (username, channel, ai_enabled, visible_in_conversations) VALUES (?, 'telegram', 1, 0)", (chat_id,))
            conn.commit()
            convo_id = c.lastrowid
            ai_enabled = 1
            handoff_notified = 0
            assigned_agent = None
            # Send welcome message for new conversation
            welcome_message = "Thank you for contacting us."
            log_message(convo_id, "AI", welcome_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "telegram"})
            try:
                send_telegram_message(chat_id, welcome_message)
                logger.info(f"✅ Welcome message sent to Telegram: {welcome_message}")
            except Exception as e:
                logger.error(f"❌ Telegram error sending welcome message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Telegram: {str(e)}", "channel": "telegram"})
        else:
            convo_id, ai_enabled, handoff_notified, assigned_agent = result

        log_message(convo_id, "user", text, "user")
        socketio.emit("new_message", {"convo_id": convo_id, "message": text, "sender": "user", "channel": "telegram"})

        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}, handoff_notified={handoff_notified}, assigned_agent={assigned_agent}")
        if not ai_enabled:
            logger.info(f"❌ AI disabled for convo_id: {convo_id}, Skipping AI response")
            return jsonify({}), 200

        # Check for ASSIST or AGENT keywords in user message
        if any(keyword in text.upper() for keyword in ["ASSIST", "AGENT"]):
            response = "I’m sorry, I couldn’t process that. Let me get an agent to assist you."
            logger.info("✅ Forcing handoff for keywords 'ASSIST' or 'AGENT', AI reply set to: " + response)
        else:
            # Check booking flow with message_id to prevent duplicates
            continue_with_ai, response = handle_booking_flow(text, convo_id, chat_id, "telegram", message_id=message_id)
            if not continue_with_ai:
                logger.info("✅ Booking flow handled, using booking flow reply")
            else:
                response = ai_respond(text, convo_id)

        log_message(convo_id, "AI", response, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "telegram"})

        # Check for handoff triggers in AI response or user message
        if any(keyword in response.lower() for keyword in ["agent", "assistance"]) or any(keyword in text.upper() for keyword in ["ASSIST", "AGENT"]):
            if not handoff_notified:
                c.execute("UPDATE conversations SET handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                conn.commit()
                # Verify the update
                c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                updated_result = c.fetchone()
                logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
                logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")

        try:
            send_telegram_message(chat_id, response)
            logger.info(f"✅ Telegram message sent - To: {chat_id}, Body: {response}")
        except Exception as e:
            logger.error(f"❌ Telegram error: {str(e)}")
            socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": "telegram"})

        return jsonify({}), 200

        # Check for HELP keyword
        if "HELP" in text.upper():
            response = "I’m sorry, I couldn’t process that. Let me get another agent to assist you."
            logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + response)
        else:
            # Check booking flow with message_id to prevent duplicates
            continue_with_ai, response = handle_booking_flow(text, convo_id, chat_id, "telegram", message_id=message_id)
            if not continue_with_ai:
                logger.info("✅ Booking flow handled, using booking flow reply")
            else:
                response = ai_respond(text, convo_id)

        log_message(convo_id, "AI", response, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "telegram"})

        if "human" in response.lower() or "sorry" in response.lower() or "HELP" in text.upper():
            if not handoff_notified:
                c.execute("UPDATE conversations SET handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                conn.commit()
                # Verify the update
                c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                updated_result = c.fetchone()
                logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
                logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")

        try:
            send_telegram_message(chat_id, response)
            logger.info(f"✅ Telegram message sent - To: {chat_id}, Body: {response}")
        except Exception as e:
            logger.error(f"❌ Telegram error: {str(e)}")
            socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": "telegram"})

        return jsonify({}), 200

@app.route("/instagram", methods=["POST"])
def instagram():
    logger.info("✅ Entering /instagram endpoint (placeholder)")
    # TODO: Implement IG-specific logic with bookings table, parsing, and Google Calendar
    data = request.get_json()
    if "object" not in data or data["object"] != "instagram":
        logger.info("✅ Not an Instagram event, returning OK")
        return "OK", 200
    for entry in data.get("entry", []):
        for messaging in entry.get("messaging", []):
            sender_id = messaging["sender"]["id"]
            incoming_msg = messaging["message"].get("text", "")
            logger.info(f"✅ Received Instagram message: {incoming_msg}, from: {sender_id}")
            try:
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("SELECT id FROM conversations WHERE username = ?", (sender_id,))
                    convo = c.fetchone()
                if not convo:
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                                  (sender_id, incoming_msg, "instagram"))
                        convo_id = c.lastrowid
                else:
                    convo_id = convo[0]
                logger.info(f"✅ Conversation ID for Instagram: {convo_id}")
                log_message(convo_id, sender_id, incoming_msg, "user")
                try:
                    logger.info(f"Processing Instagram message with AI: {incoming_msg}")
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("SELECT user, message, sender FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 10", (convo_id,))
                        messages = c.fetchall()
                    conversation_history = [
                        {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Maintain conversation context and provide relevant follow-up responses. Escalate to a human if the query is complex or requires personal assistance."}
                    ]
                    for msg in messages:
                        user, message_text, sender = msg
                        role = "user" if sender == "user" else "assistant"
                        conversation_history.append({"role": role, "content": message_text})
                    conversation_history.append({"role": "user", "content": incoming_msg})
                    logger.info(f"✅ Sending conversation history to OpenAI: {conversation_history}")

                    response = openai.ChatCompletion.create(
                        model="gpt-4o-mini",
                        messages=conversation_history,
                        max_tokens=150
                    )
                    ai_reply = response.choices[0].message.content.strip()
                    model_used = response.model
                    logger.info(f"✅ Instagram AI reply: {ai_reply}, Model: {model_used}")
                except Exception as e:
                    ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
                    logger.error(f"❌ Instagram OpenAI error: {str(e)}")
                    logger.error(f"❌ Instagram OpenAI error type: {type(e).__name__}")
                logger.info("✅ Logging Instagram AI response")
                log_message(convo_id, "AI", ai_reply, "ai")
                logger.info("✅ Sending Instagram AI response")
                requests.post(
                    f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                    json={"recipient": {"id": sender_id}, "message": {"text": ai_reply}}
                )
                logger.info("✅ Emitting new_message event for Instagram")
                socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "instagram"})
                if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
                    try:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                            handoff_notified = c.fetchone()[0]
                        if not handoff_notified:
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                conn.commit()
                            time.sleep(3.0)
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                                updated_result = c.fetchone()
                            logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "instagram"})
                            logger.info(f"✅ Instagram handoff triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                    except Exception as e:
                        logger.error(f"❌ Error during Instagram handoff for convo_id {convo_id}: {e}")
            except Exception as e:
                logger.error(f"❌ Error in /instagram endpoint: {e}")
    logger.info("✅ Returning EVENT_RECEIVED for Instagram")
    return "EVENT_RECEIVED", 200

@app.route("/instagram", methods=["GET"])
def instagram_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.getenv("VERIFY_TOKEN", "mysecretverifytoken"):
        logger.info("✅ Instagram verification successful")
        return challenge, 200
    logger.error("❌ Instagram verification failed")
    return "Verification failed", 403

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    logger.info("✅ Entering /whatsapp endpoint (placeholder)")
    # TODO: Implement WhatsApp-specific logic with bookings table, parsing, and Google Calendar
    return "OK", 200

@app.route("/send-welcome", methods=["POST"])
def send_welcome():
    data = request.get_json()
    to_number = data.get("to_number")
    user_name = data.get("user_name", "Guest")
    if not to_number:
        logger.error("❌ Missing to_number in /send-welcome request")
        return jsonify({"error": "Missing to_number"}), 400
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM conversations WHERE username = ? AND channel = ?", (to_number, "telegram"))
        convo = c.fetchone()
    if not convo:
        logger.error("❌ Conversation not found in /send-welcome")
        return jsonify({"error": "Conversation not found"}), 404
    convo_id = convo[0]
    try:
        logger.info(f"✅ Sending welcome message to Telegram chat {to_number}")
        welcome_message = f"Welcome to our hotel, {user_name}! We're here to assist with your bookings. Reply 'BOOK' to start or 'HELP' for assistance."
        send_telegram_message(to_number, welcome_message)
        logger.info("✅ Logging welcome message in /send-welcome")
        log_message(convo_id, "AI", f"Welcome to our hotel, {user_name}!", "ai")
        logger.info("✅ Emitting new_message event in /send-welcome")
        socketio.emit("new_message", {"convo_id": convo_id, "message": f"Welcome to our hotel, {user_name}!", "sender": "ai", "channel": "telegram"})
        logger.info("✅ Welcome message sent successfully")
        return jsonify({"message": "Welcome message sent"}), 200
    except Exception as e:
        logger.error(f"❌ Telegram error in send-welcome: {str(e)}")
        socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Telegram: {str(e)}", "channel": "telegram"})
        return jsonify({"error": "Failed to send message"}), 500

@app.route("/handoff", methods=["POST"])
@login_required
def handoff():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    if not convo_id:
        logger.error("❌ Missing conversation ID in /handoff request")
        return jsonify({"message": "Missing conversation ID"}), 400
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 0 WHERE id = ?", (current_user.username, convo_id))
            c.execute("SELECT username, channel, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
            logger.info(f"✅ Handoff updated: convo_id={convo_id}, assigned_agent={result[2]}") # Debug log
            c.execute("SELECT username, channel FROM conversations WHERE id = ?", (convo_id,))
            username, channel = c.fetchone()
            conn.commit()
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
        logger.info(f"✅ Chat {convo_id} assigned to {current_user.username}")
        return jsonify({"message": f"Chat assigned to {current_user.username}"})
    except Exception as e:
        logger.error(f"❌ Error in /handoff endpoint: {e}")
        return jsonify({"error": "Failed to assign chat"}), 500

@app.route("/handback-to-ai", methods=["POST"])
@login_required
def handback_to_ai():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    if not convo_id:
        logger.error("❌ Missing conversation ID in /handback-to-ai request")
        return jsonify({"message": "Missing conversation ID"}), 400
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            # Fetch conversation details
            c.execute("SELECT username, channel, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                return jsonify({"error": "Conversation not found"}), 404
            username, channel, assigned_agent = result

            # Check if the current agent is assigned to this conversation
            if assigned_agent != current_user.username:
                logger.error(f"❌ Agent {current_user.username} is not assigned to convo_id {convo_id}")
                return jsonify({"error": "You are not assigned to this conversation"}), 403

            # Re-enable AI and clear the assigned agent, ensure visible_in_conversations remains 0
            c.execute("UPDATE conversations SET assigned_agent = NULL, ai_enabled = 1, handoff_notified = 0, visible_in_conversations = 0 WHERE id = ?", (convo_id,))
            conn.commit()

            # Verify the update
            c.execute("SELECT ai_enabled FROM conversations WHERE id = ?", (convo_id,))
            updated_result = c.fetchone()
            if updated_result:
                ai_enabled = updated_result[0]
                logger.info(f"✅ After handback, ai_enabled for convo_id {convo_id} is {ai_enabled}")
            else:
                logger.error(f"❌ Failed to verify ai_enabled for convo_id {convo_id} after handback")

        # Notify the user that the AI has taken over
        handback_message = "The sales agent has handed the conversation back to me. Anything else I can help you with?"
        log_message(convo_id, "AI", handback_message, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": handback_message, "sender": "ai", "channel": channel})
        if channel == "telegram":
            try:
                logger.info(f"Sending handback message to Telegram - To: {username}, Body: {handback_message}")
                send_telegram_message(username, handback_message)
                logger.info("✅ Handback message sent to Telegram: " + handback_message)
            except Exception as e:
                logger.error(f"❌ Telegram error sending handback message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send handback message to Telegram: {str(e)}", "channel": channel})
        elif channel == "whatsapp":
            try:
                logger.info(f"Sending handback message to WhatsApp - To: {username}, Body: {handback_message}")
                send_whatsapp_message(username, handback_message)
                logger.info("Handback message sent to WhatsApp: " + handback_message)
            except Exception as e:
                logger.error(f"WhatsApp error sending handback message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send handback message to WhatsApp: {str(e)}", "channel": channel})
        elif channel == "instagram":
            try:
                logger.info(f"Sending handback message to Instagram - To: {username}, Body: {handback_message}")
                send_instagram_message(username, handback_message)
                logger.info("Handback message sent to Instagram: " + handback_message)
            except Exception as e:
                logger.error(f"Instagram error sending handback message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send handback message to Instagram: {str(e)}", "channel": channel})

        # Emit a refresh event to update the conversation list in the dashboard
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
        logger.info(f"✅ Chat {convo_id} handed back to AI by {current_user.username}")
        return jsonify({"message": "Chat handed back to AI successfully"})
    except Exception as e:
        logger.error(f"❌ Error in /handback-to-ai endpoint: {e}")
        return jsonify({"error": "Failed to hand back to AI"}), 500
        
@app.route("/test-openai", methods=["GET"])
def test_openai():
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        ai_reply = response.choices[0].message.content.strip()
        model_used = response.model
        logger.info(f"✅ OpenAI test successful: {ai_reply}, Model: {model_used}")
        return jsonify({"status": "success", "reply": ai_reply, "model": model_used}), 200
    except Exception as e:
        logger.error(f"❌ OpenAI test failed: {str(e)}")
        logger.error(f"❌ OpenAI test error type: {type(e).__name__}")
        return jsonify({"status": "failed", "error": str(e)}), 500

@app.route("/clear-database", methods=["POST"])
def clear_database():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM conversations")
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM bookings")
            conn.commit()
        logger.info("✅ Database cleared successfully")
        return jsonify({"message": "Database cleared successfully"}), 200
    except Exception as e:
        logger.error(f"❌ Error clearing database: {str(e)}")
        return jsonify({"error": "Failed to clear database"}), 500

@app.route("/")
def index():
    return render_template("dashboard.html")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
