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
            chat_id TEXT,  -- Add this line to store the raw chat_id
            latest_message TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            assigned_agent TEXT DEFAULT NULL,
            channel TEXT DEFAULT 'dashboard',
            opted_in INTEGER DEFAULT 0,
            ai_enabled INTEGER DEFAULT 1,
            handoff_notified INTEGER DEFAULT 0,
            visible_in_conversations INTEGER DEFAULT 0,
            booking_intent TEXT DEFAULT NULL
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
            c.execute("SELECT id, username, chat_id, channel, assigned_agent FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC")
            conversations = []
            for row in c.fetchall():
                convo_id, username, chat_id, channel, assigned_agent = row
                # Create a display_name by removing the 'telegram_' prefix if channel is 'telegram'
                display_name = chat_id if channel == "telegram" else username
                conversations.append({
                    "id": convo_id,
                    "username": username,
                    "chat_id": chat_id,
                    "channel": channel,
                    "assigned_agent": assigned_agent,
                    "display_name": f"{display_name} ({channel})" if channel else display_name
                })
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
        # Fetch username and chat_id
        c.execute("SELECT username, chat_id FROM conversations WHERE id = ?", (convo_id,))
        result = c.fetchone()
        username = result[0] if result else "Unknown"
        chat_id = result[1] if result else None
        conn.close()
        logger.info(f"✅ Fetched {len(messages)} messages for convo_id {convo_id}")
        return jsonify({"messages": messages, "username": username, "chat_id": chat_id})
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo_id {convo_id}: {e}")
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
    """
    Send a message to a Telegram chat using the bot API.
    Args:
        chat_id (str): The Telegram chat ID.
        text (str): The message text to send.
    Returns:
        bool: True if successful, False otherwise.
    """
    # Ensure TELEGRAM_API_URL includes the bot token
    if not TELEGRAM_API_URL or not TELEGRAM_API_URL.startswith("https://api.telegram.org/bot"):
        logger.error(f"❌ TELEGRAM_API_URL is invalid or missing bot token: {TELEGRAM_API_URL}")
        return False

    url = f"{TELEGRAM_API_URL}/sendMessage"
    if not chat_id or not isinstance(chat_id, str):
        logger.error(f"❌ Invalid chat_id: {chat_id}")
        return False
    if not text or not isinstance(text, str):
        logger.error(f"❌ Invalid text: {text}")
        return False
    if len(text) > 4096:
        logger.error(f"❌ Text exceeds 4096 characters: {len(text)}")
        text = text[:4093] + "..."  # Truncate to fit within limit

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"  # Optional: Add if you use Markdown formatting
    }
    headers = {"Content-Type": "application/json"}

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(f"✅ Sent Telegram message to {chat_id}: {text}")
            time.sleep(0.5)  # Small delay to avoid rate limiting
            return True
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ Telegram API error (Attempt {attempt + 1}/{max_retries}): {str(e)}, Response: {e.response.text}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                continue
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Telegram request failed (Attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            return False

    logger.error(f"❌ Failed to send Telegram message after {max_retries} attempts")
    return False

# Placeholder for WhatsApp message sending (to be implemented later)
def send_whatsapp_message(phone_number, text):
    raise NotImplementedError("WhatsApp messaging not yet implemented")

# Placeholder for Instagram message sending (to be implemented later)
def send_instagram_message(user_id, text):
    raise NotImplementedError("Instagram messaging not yet implemented")
    
def check_availability(check_in, check_out):
    """
    Check availability between check-in and check-out dates by iterating through each day.
    Returns a string message indicating availability or unavailability.
    """
    logger.info(f"✅ Checking availability from {check_in} to {check_out}")
    try:
        current_date = check_in
        logger.info(f"✅ Date range to check: {current_date.strftime('%Y-%m-%d')} to {(check_out - timedelta(days=1)).strftime('%Y-%m-%d')}")
        while current_date < check_out:
            start_time = current_date.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
            end_time = (current_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'

            logger.info(f"✅ Checking calendar for {current_date.strftime('%Y-%m-%d')}")
            events_result = service.events().list(
                calendarId='a33289c61cf358216690e7cc203d116cec4c44075788fab3f2b200f5bbcd89cc@group.calendar.google.com',  # Replace with your actual calendar ID
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
            logger.info(f"✅ Events found on {current_date.strftime('%Y-%m-%d')}: {events}")

            if any(event.get('summary') == "Fully Booked" for event in events):
                logger.info(f"✅ Found 'Fully Booked' event on {current_date.strftime('%Y-%m-%d')}, range unavailable")
                return f"Sorry, the dates from {check_in.strftime('%B %d, %Y')} to {(check_out - timedelta(days=1)).strftime('%B %d, %Y')} are not available. We are fully booked on {current_date.strftime('%B %d, %Y')}."

            current_date += timedelta(days=1)

        logger.info(f"✅ No 'Fully Booked' events found from {check_in.strftime('%Y-%m-%d')} to {(check_out - timedelta(days=1)).strftime('%Y-%m-%d')}, range available")
        return f"Yes, the dates from {check_in.strftime('%B %d, %Y')} to {(check_out - timedelta(days=1)).strftime('%B %d, %Y')} are available."
    except Exception as e:
        logger.error(f"❌ Google Calendar API error: {str(e)}")
        return "Sorry, I’m having trouble checking availability right now. I’ll connect you with a team member to assist you."
        
def ai_respond(message, convo_id):
    """
    Generate an AI response for the given message and conversation ID using OpenAI,
    with logic to handle availability checks based on Google Calendar.
    Supports both English and Spanish date formats.
    """
    logger.info(f"✅ Generating AI response for convo_id {convo_id}: {message}")
    try:
        # Enhanced regex to capture dates in English (e.g., "March 20") and Spanish (e.g., "20 de marzo")
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
            # Groups for English: (month1, day1, None, None, month2, day2, None, None)
            # Groups for Spanish: (None, None, day1, month1, None, None, day2, month2)
            month1_en, day1_en, day1_es, month1_es, month2_en, day2_en, day2_es, month2_es = date_match.groups()
            current_year = datetime.now().year  # 2025 as of March 17, 2025
            logger.info(f"✅ Extracted date strings: month1_en={month1_en}, day1_en={day1_en}, day1_es={day1_es}, month1_es={month1_es}, "
                        f"month2_en={month2_en}, day2_en={day2_en}, day2_es={day2_es}, month2_es={month2_es}")

            # Spanish to English month mapping
            spanish_to_english_months = {
                "enero": "January", "febrero": "February", "marzo": "March", "abril": "April",
                "mayo": "May", "junio": "June", "julio": "July", "agosto": "August",
                "septiembre": "September", "octubre": "October", "noviembre": "November", "diciembre": "December"
            }

            # Parse check-in date
            if month1_en and day1_en:  # English format: "March 20"
                check_in_str = f"{month1_en} {day1_en}"
                check_in_str = re.sub(r'(st|nd|rd|th)', '', check_in_str).strip()
                check_in = datetime.strptime(f"{check_in_str} {current_year}", '%B %d %Y')
            elif day1_es and month1_es:  # Spanish format: "20 de marzo"
                month1_en = spanish_to_english_months.get(month1_es.lower(), month1_es)
                check_in_str = f"{month1_en} {day1_es}"
                check_in = datetime.strptime(f"{check_in_str} {current_year}", '%B %d %Y')
            else:
                logger.error(f"❌ Could not parse check-in date from: {message}")
                return "Sorry, I couldn’t understand the dates. Please use a format like 'March 20' or '20 de marzo'." if "sorry" in message.lower() else \
                       "Lo siento, no entendí las fechas. Por favor, usa un formato como '20 de marzo' o 'March 20'."

            # Parse check-out date (if provided)
            if month2_en and day2_en:  # English format: "March 25"
                check_out_str = f"{month2_en} {day2_en}"
                check_out_str = re.sub(r'(st|nd|rd|th)', '', check_out_str).strip()
                check_out = datetime.strptime(f"{check_out_str} {current_year}", '%B %d %Y')
            elif day2_es and month2_es:  # Spanish format: "25 de marzo"
                month2_en = spanish_to_english_months.get(month2_es.lower(), month2_es)
                check_out_str = f"{month2_en} {day2_es}"
                check_out = datetime.strptime(f"{check_out_str} {current_year}", '%B %d %Y')
            else:
                check_out = check_in + timedelta(days=1)  # Default to 1-day stay if no check-out date
            logger.info(f"✅ Parsed dates: check_in={check_in.strftime('%Y-%m-%d')}, check_out={check_out.strftime('%Y-%m-%d')}")

            # Handle year rollover (e.g., December to January)
            if check_out < check_in:
                check_out = check_out.replace(year=check_out.year + 1)
                logger.info(f"✅ Adjusted check_out for year rollover: {check_out.strftime('%Y-%m-%d')}")

            if check_out <= check_in:
                return "The check-out date must be after the check-in date. Please provide a valid range." if "sorry" in message.lower() else \
                       "La fecha de salida debe ser posterior a la fecha de entrada. Por favor, proporciona un rango válido."

            # Call check_availability with the parsed dates
            availability = check_availability(check_in, check_out)
            if "are available" in availability.lower():
                booking_intent = f"{check_in.strftime('%Y-%m-%d')} to {check_out.strftime('%Y-%m-%d')}"
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET booking_intent = ? WHERE id = ?", (booking_intent, convo_id))
                    conn.commit()
                response = f"{availability}. Would you like to book?" if "sorry" in message.lower() else \
                           f"{availability.replace('are available', 'están disponibles')}. ¿Te gustaría reservar?"
            else:
                response = availability if "sorry" in message.lower() else \
                           availability.replace("are not available", "no están disponibles").replace("fully booked", "completamente reservado")

            logger.info(f"✅ Availability check result: {response}")
            return response

        # Detect language for booking keywords
        is_spanish = any(spanish_word in message.lower() for spanish_word in ["reservar", "habitación", "disponibilidad"])
        if "book" in message.lower() or "booking" in message.lower() or "reservar" in message.lower():
            logger.info(f"✅ Detected booking request, handing off to dashboard")
            return "I’ll connect you with a team member to assist with your booking." if not is_spanish else \
                   "Te conectaré con un miembro del equipo para que te ayude con tu reserva."

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT user, message, sender FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 10", (convo_id,))
            messages = c.fetchall()
        conversation_history = [
            {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel customer service and sales agent for Amapola Resort. Use the provided business information and Q&A to answer guest questions. Maintain conversation context. Detect the user's language (English or Spanish) based on their input and respond in the same language. If you don’t know the answer or the query is complex, respond with the appropriate escalation message in the user's language. Do not mention room types or pricing unless specifically asked."}
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
                if "sorry" in ai_reply.lower() or "lo siento" in ai_reply.lower():
                    return ai_reply  # Trigger handoff if OpenAI apologizes
                return ai_reply
            except Exception as e:
                logger.error(f"❌ OpenAI error (Attempt {attempt + 1}): {str(e)}")
                if attempt == retry_attempts - 1:
                    logger.info("✅ Setting default AI reply due to repeated errors")
                    return "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if not is_spanish else \
                           "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
                time.sleep(1)
                continue
    except Exception as e:
        logger.error(f"❌ Error in ai_respond for convo_id {convo_id}: {str(e)}")
        return "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if "sorry" in message.lower() else \
               "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
    
def detect_language(message, convo_id):
    """
    Detect the user's language (English or Spanish) based on the message or conversation history.
    Returns 'es' for Spanish, 'en' for English.
    """
    spanish_keywords = ["hola", "gracias", "reservar", "habitación", "disponibilidad", "marzo", "abril"]
    if any(keyword in message.lower() for keyword in spanish_keywords):
        return "es"
    
    # Check conversation history for language cues
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT message FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 5", (convo_id,))
        messages = c.fetchall()
        for msg in messages:
            if any(keyword in msg[0].lower() for keyword in spanish_keywords):
                return "es"
    
    return "en"

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
            c.execute("SELECT username, chat_id, channel, assigned_agent, ai_enabled, booking_intent FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
            username, chat_id, channel, assigned_agent, ai_enabled, booking_intent = result if result else (None, None, None, None, None, None)
        if not username:
            logger.error(f"❌ Conversation not found: {convo_id}")
            return jsonify({"error": "Conversation not found"}), 404
        
        sender = "agent" if current_user.is_authenticated else "user"
        logger.info(f"✅ Processing /chat message as sender: {sender}")
        
        prefixed_username = username
        log_message(convo_id, prefixed_username, user_message, sender)

        language = detect_language(user_message, convo_id)

        if sender == "agent":
            logger.info("✅ Sender is agent, emitting new_message event")
            socketio.emit("new_message", {"convo_id": convo_id, "message": user_message, "sender": "agent", "channel": channel})
            if channel == "telegram":
                if not chat_id:
                    logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                    socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send message to Telegram: No chat_id found", "channel": channel})
                else:
                    logger.info(f"Sending agent message to Telegram - To: {chat_id}, Body: {user_message}")
                    if not send_telegram_message(chat_id, user_message):
                        logger.error(f"❌ Failed to send agent message to Telegram for chat_id {chat_id}")
                        socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send message to Telegram", "channel": channel})
                    else:
                        logger.info("✅ Agent message sent to Telegram: " + user_message)
            logger.info("✅ Agent message processed successfully")
            return jsonify({"status": "success"})

        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if ai_enabled:
            logger.info("✅ AI is enabled, proceeding with AI response")
            if booking_intent and ("yes" in user_message.lower() or "proceed" in user_message.lower() or "sí" in user_message.lower()):
                response = f"Great! An agent will assist you with booking a room for {booking_intent}. Please wait." if language == "en" else \
                          f"¡Excelente! Un agente te ayudará con la reserva de una habitación para {booking_intent}. Por favor, espera."
                log_message(convo_id, "AI", response, "ai")
                socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": channel})
                if channel == "telegram":
                    if not chat_id:
                        logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                        socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to Telegram: No chat_id found", "channel": channel})
                    else:
                        if not send_telegram_message(chat_id, response):
                            logger.error(f"❌ Failed to send handoff message to Telegram for chat_id {chat_id}")
                            socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to Telegram", "channel": channel})
                        else:
                            logger.info(f"✅ Telegram message sent - To: {chat_id}, Body: {response}")
                with get_db_connection() as conn:
                    c = conn.cursor()
                    try:
                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                        conn.commit()
                    except sqlite3.OperationalError as e:
                        if "database is locked" in str(e):
                            time.sleep(1)
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                            conn.commit()
                        else:
                            logger.error(f"❌ Database error: {e}")
                            raise
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
                logger.info(f"✅ Handoff triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                return jsonify({"reply": response})

            if "book" in user_message.lower() or "reservar" in user_message.lower():
                ai_reply = "I’ll connect you with a team member who can assist with your booking." if language == "en" else \
                          "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
                logger.info(f"✅ Detected booking request, handing off to dashboard: {ai_reply}")
                log_message(convo_id, "AI", ai_reply, "ai")
                socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": channel})
                if channel == "telegram":
                    if not chat_id:
                        logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                        socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to Telegram: No chat_id found", "channel": channel})
                    else:
                        if not send_telegram_message(chat_id, ai_reply):
                            logger.error(f"❌ Failed to send booking handoff message to Telegram for chat_id {chat_id}")
                            socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to Telegram", "channel": channel})
                        else:
                            logger.info(f"✅ Telegram message sent - To: {chat_id}, Body: {ai_reply}")
                with get_db_connection() as conn:
                    c = conn.cursor()
                    try:
                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                        conn.commit()
                    except sqlite3.OperationalError as e:
                        if "database is locked" in str(e):
                            time.sleep(1)
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                            conn.commit()
                        else:
                            logger.error(f"❌ Database error: {e}")
                            raise
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
                logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                return jsonify({"reply": ai_reply})

            if "HELP" in user_message.upper() or "AYUDA" in user_message.upper():
                ai_reply = "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you." if language == "en" else \
                          "Lo siento, no pude procesar eso. Te conectaré con un miembro del equipo para que te ayude."
                logger.info("✅ Forcing handoff for keyword 'HELP/AYUDA', AI reply set to: " + ai_reply)
            else:
                ai_reply = ai_respond(user_message, convo_id)

            log_message(convo_id, "AI", ai_reply, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": channel})
            if channel == "telegram":
                if not chat_id:
                    logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                    socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to Telegram: No chat_id found", "channel": channel})
                else:
                    if not send_telegram_message(chat_id, ai_reply):
                        logger.error(f"❌ Failed to send AI response to Telegram for chat_id {chat_id}")
                        socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to Telegram", "channel": channel})
                    else:
                        logger.info(f"✅ Telegram message sent - To: {chat_id}, Body: {ai_reply}")
            if "sorry" in ai_reply.lower() or "lo siento" in ai_reply.lower():
                try:
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                        handoff_notified = c.fetchone()[0]
                    if not handoff_notified:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            try:
                                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                conn.commit()
                            except sqlite3.OperationalError as e:
                                if "database is locked" in str(e):
                                    time.sleep(1)
                                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                    conn.commit()
                                else:
                                    logger.error(f"❌ Database error: {e}")
                                    raise
                        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
                        logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                except Exception as e:
                    logger.error(f"❌ Error during handoff for convo_id {convo_id}: {e}")
            logger.info("✅ AI response processed successfully")
            return jsonify({"reply": ai_reply})
        else:
            logger.info(f"❌ AI disabled for convo_id: {convo_id}")
            return jsonify({"status": "Message received, AI disabled"})
    except Exception as e:
        logger.error(f"❌ Error in /chat endpoint: {str(e)}")
        return jsonify({"error": "Failed to process chat message"}), 500

def log_message(convo_id, user, message, sender):
    with get_db_connection() as conn:
        c = conn.cursor()
        try:
            c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (?, ?, ?, ?)", 
                      (convo_id, user, message, sender))
            c.execute("UPDATE conversations SET latest_message = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", 
                      (message, convo_id))
            conn.commit()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                time.sleep(1)  # Retry after a short delay
                c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (?, ?, ?, ?)", 
                          (convo_id, user, message, sender))
                c.execute("UPDATE conversations SET latest_message = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", 
                          (message, convo_id))
                conn.commit()
            else:
                logger.error(f"❌ Database error: {e}")
                raise
        
@app.route("/telegram", methods=["POST"])
def telegram():
    update = request.get_json()
    logger.info(f"Received Telegram update: {update}")

    if "message" not in update:
        logger.warning("No message in Telegram update, returning OK")
        return jsonify({"status": "ok"}), 200

    message_data = update["message"]
    chat_id = str(message_data["chat"]["id"])
    text = message_data.get("text", "")
    message_id = str(message_data.get("message_id", ""))
    convo_id = None

    with get_db_connection() as conn:
        c = conn.cursor()
        prefixed_chat_id = f"telegram_{chat_id}"
        c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent, booking_intent FROM conversations WHERE username = ? AND channel = 'telegram'", (prefixed_chat_id,))
        result = c.fetchone()

        if not result:
            try:
                c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, 'telegram', 1, 0)", (prefixed_chat_id, chat_id))
                conn.commit()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    time.sleep(1)
                    c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, 'telegram', 1, 0)", (prefixed_chat_id, chat_id))
                    conn.commit()
                else:
                    logger.error(f"❌ Database error: {e}")
                    raise
            convo_id = c.lastrowid
            ai_enabled = 1
            handoff_notified = 0
            assigned_agent = None
            booking_intent = None
            language = detect_language(text, convo_id)
            welcome_message = "Gracias por contactarnos." if language == "es" else "Thank you for contacting us."
            log_message(convo_id, chat_id, welcome_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, welcome_message):
                logger.error(f"❌ Failed to send welcome message to Telegram for chat_id {chat_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send welcome message to Telegram", "channel": "telegram"})
        else:
            convo_id, ai_enabled, handoff_notified, assigned_agent, booking_intent = result

        log_message(convo_id, chat_id, text, "user")
        socketio.emit("new_message", {"convo_id": convo_id, "message": text, "sender": "user", "channel": "telegram"})

        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}, handoff_notified={handoff_notified}, assigned_agent={assigned_agent}")
        if not ai_enabled:
            logger.info(f"❌ AI disabled for convo_id: {convo_id}, Skipping AI response")
            return jsonify({}), 200

        # Generate AI response
        response = ai_respond(text, convo_id)

        # Handle handoff if booking intent is set and user confirms
        language = detect_language(text, convo_id)
        if booking_intent and ("yes" in text.lower() or "proceed" in text.lower() or "sí" in text.lower()):
            handoff_message = f"Great! An agent will assist you with booking for {booking_intent}. Please wait." if language == "en" else \
                             f"¡Excelente! Un agente te ayudará con la reserva para {booking_intent}. Por favor, espera."
            log_message(convo_id, "AI", handoff_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, handoff_message):
                logger.error(f"❌ Failed to send handoff message to Telegram for chat_id {chat_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to Telegram", "channel": "telegram"})
            with get_db_connection() as conn:
                c = conn.cursor()
                try:
                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                    conn.commit()
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e):
                        time.sleep(1)
                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                        conn.commit()
                    else:
                        logger.error(f"❌ Database error: {e}")
                        raise
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
            logger.info(f"✅ Handoff triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
            return jsonify({}), 200

        # Handle direct booking request
        if "book" in text.lower() or "booking" in text.lower() or "reservar" in text.lower():
            handoff_message = "I’ll connect you with a team member to assist with your booking." if language == "en" else \
                             "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
            log_message(convo_id, "AI", handoff_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, handoff_message):
                logger.error(f"❌ Failed to send booking handoff message to Telegram for chat_id {chat_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to Telegram", "channel": "telegram"})
            with get_db_connection() as conn:
                c = conn.cursor()
                try:
                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                    conn.commit()
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e):
                        time.sleep(1)
                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                        conn.commit()
                    else:
                        logger.error(f"❌ Database error: {e}")
                        raise
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
            logger.info(f"✅ Handoff triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
            return jsonify({}), 200

        # Send AI response if not a handoff case
        if response:
            log_message(convo_id, "AI", response, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, response):
                logger.error(f"❌ Failed to send AI response to Telegram for chat_id {chat_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to Telegram", "channel": "telegram"})

        if "sorry" in response.lower() or "lo siento" in response.lower():
            if not handoff_notified:
                with get_db_connection() as conn:
                    c = conn.cursor()
                    try:
                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                        conn.commit()
                    except sqlite3.OperationalError as e:
                        if "database is locked" in str(e):
                            time.sleep(1)
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                            conn.commit()
                        else:
                            logger.error(f"❌ Database error: {e}")
                            raise
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
                logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")

        return jsonify({}), 200
    
        
@app.route("/instagram", methods=["POST"])
def instagram():
    logger.info("✅ Entering /instagram endpoint")
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
                    prefixed_sender_id = f"instagram_{sender_id}"  # Prefix with channel
                    c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent FROM conversations WHERE username = ? AND channel = 'instagram'", (prefixed_sender_id,))
                    convo = c.fetchone()
                if not convo:
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        try:
                            c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                                      (prefixed_sender_id, incoming_msg, "instagram"))
                            conn.commit()
                        except sqlite3.OperationalError as e:
                            if "database is locked" in str(e):
                                time.sleep(1)  # Retry after a short delay
                                c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                                          (prefixed_sender_id, incoming_msg, "instagram"))
                                conn.commit()
                            else:
                                logger.error(f"❌ Database error: {e}")
                                raise
                        convo_id = c.lastrowid
                        ai_enabled = 1
                        handoff_notified = 0
                        assigned_agent = None
                        welcome_message = "Thank you for contacting us."
                        log_message(convo_id, "AI", welcome_message, "ai")
                        socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "instagram"})
                        try:
                            requests.post(
                                f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                                json={"recipient": {"id": sender_id}, "message": {"text": welcome_message}}
                            )
                            logger.info(f"✅ Instagram welcome message sent - To: {sender_id}, Body: {welcome_message}")
                        except Exception as e:
                            logger.error(f"❌ Instagram error sending welcome message: {str(e)}")
                            socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Instagram: {str(e)}", "channel": "instagram"})
                else:
                    convo_id, ai_enabled, handoff_notified, assigned_agent = convo

                log_message(convo_id, prefixed_sender_id, incoming_msg, "user")
                socketio.emit("new_message", {"convo_id": convo_id, "message": incoming_msg, "sender": "user", "channel": "instagram"})

                logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}, handoff_notified={handoff_notified}, assigned_agent={assigned_agent}")
                if not ai_enabled:
                    logger.info(f"❌ AI disabled for convo_id: {convo_id}, Skipping AI response")
                    continue

                if "book" in incoming_msg.lower():
                    response = "I’ll connect you with a team member who can assist with your booking."
                    logger.info(f"✅ Detected booking request, handing off to dashboard: {response}")
                    log_message(convo_id, "AI", response, "ai")
                    socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "instagram"})
                    try:
                        requests.post(
                            f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                            json={"recipient": {"id": sender_id}, "message": {"text": response}}
                        )
                        logger.info(f"✅ Instagram message sent - To: {sender_id}, Body: {response}")
                    except Exception as e:
                        logger.error(f"❌ Instagram error sending message: {str(e)}")
                        socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Instagram: {str(e)}", "channel": "instagram"})
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        try:
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                            conn.commit()
                        except sqlite3.OperationalError as e:
                            if "database is locked" in str(e):
                                time.sleep(1)  # Retry after a short delay
                                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                conn.commit()
                            else:
                                logger.error(f"❌ Database error: {e}")
                                raise
                        c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                        updated_result = c.fetchone()
                        logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                    socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "instagram"})
                    logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                    continue

                if "HELP" in incoming_msg.upper():
                    response = "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you."
                    logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + response)
                else:
                    response = ai_respond(incoming_msg, convo_id)

                logger.info("✅ Logging Instagram AI response")
                log_message(convo_id, "AI", response, "ai")
                logger.info("✅ Emitting new_message event for Instagram")
                socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "instagram"})
                try:
                    logger.info(f"Sending Instagram message - To: {sender_id}, Body: {response}")
                    requests.post(
                        f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                        json={"recipient": {"id": sender_id}, "message": {"text": response}}
                    )
                    logger.info(f"✅ Instagram message sent - To: {sender_id}, Body: {response}")
                except Exception as e:
                    logger.error(f"❌ Instagram error sending message: {str(e)}")
                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Instagram: {str(e)}", "channel": "instagram"})

                if "sorry" in response.lower() or "HELP" in incoming_msg.upper():
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                        handoff_notified = c.fetchone()[0]
                    if not handoff_notified:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            try:
                                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                conn.commit()
                            except sqlite3.OperationalError as e:
                                if "database is locked" in str(e):
                                    time.sleep(1)  # Retry after a short delay
                                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                    conn.commit()
                                else:
                                    logger.error(f"❌ Database error: {e}")
                                    raise
                        time.sleep(3.0)
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                            updated_result = c.fetchone()
                        logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "instagram"})
                        logger.info(f"✅ Instagram handoff triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")

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
    logger.info("✅ Entering /whatsapp endpoint")
    data = request.get_json()
    logger.info(f"Received WhatsApp update: {data}")

    if "entry" not in data:
        logger.info("✅ Not a valid WhatsApp event, returning OK")
        return "OK", 200

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if "value" in change and "messages" in change["value"]:
                message = change["value"]["messages"][0]
                if "from" in message and "text" in message:
                    sender_id = message["from"]
                    incoming_msg = message["text"]["body"]
                    logger.info(f"✅ Received WhatsApp message: {incoming_msg}, from: {sender_id}")
                    try:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            prefixed_sender_id = f"whatsapp_{sender_id}"  # Prefix with channel
                            c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent FROM conversations WHERE username = ? AND channel = 'whatsapp'", (prefixed_sender_id,))
                            convo = c.fetchone()
                        if not convo:
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                try:
                                    c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                                              (prefixed_sender_id, incoming_msg, "whatsapp"))
                                    conn.commit()
                                except sqlite3.OperationalError as e:
                                    if "database is locked" in str(e):
                                        time.sleep(1)  # Retry after a short delay
                                        c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                                                  (prefixed_sender_id, incoming_msg, "whatsapp"))
                                        conn.commit()
                                    else:
                                        logger.error(f"❌ Database error: {e}")
                                        raise
                                convo_id = c.lastrowid
                                ai_enabled = 1
                                handoff_notified = 0
                                assigned_agent = None
                                welcome_message = "Thank you for contacting us."
                                log_message(convo_id, "AI", welcome_message, "ai")
                                socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "whatsapp"})
                                try:
                                    raise NotImplementedError("WhatsApp messaging not yet implemented")
                                    logger.info(f"✅ WhatsApp welcome message sent - To: {sender_id}, Body: {welcome_message}")
                                except Exception as e:
                                    logger.error(f"❌ WhatsApp error sending welcome message: {str(e)}")
                                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to WhatsApp: {str(e)}", "channel": "whatsapp"})
                        else:
                            convo_id, ai_enabled, handoff_notified, assigned_agent = convo

                        log_message(convo_id, prefixed_sender_id, incoming_msg, "user")
                        socketio.emit("new_message", {"convo_id": convo_id, "message": incoming_msg, "sender": "user", "channel": "whatsapp"})

                        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}, handoff_notified={handoff_notified}, assigned_agent={assigned_agent}")
                        if not ai_enabled:
                            logger.info(f"❌ AI disabled for convo_id: {convo_id}, Skipping AI response")
                            continue

                        if "book" in incoming_msg.lower():
                            response = "I’ll connect you with a team member who can assist with your booking."
                            logger.info(f"✅ Detected booking request, handing off to dashboard: {response}")
                            log_message(convo_id, "AI", response, "ai")
                            socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "whatsapp"})
                            try:
                                raise NotImplementedError("WhatsApp messaging not yet implemented")
                                logger.info(f"✅ WhatsApp message sent - To: {sender_id}, Body: {response}")
                            except Exception as e:
                                logger.error(f"❌ WhatsApp error sending message: {str(e)}")
                                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to WhatsApp: {str(e)}", "channel": "whatsapp"})
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                try:
                                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                    conn.commit()
                                except sqlite3.OperationalError as e:
                                    if "database is locked" in str(e):
                                        time.sleep(1)  # Retry after a short delay
                                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                        conn.commit()
                                    else:
                                        logger.error(f"❌ Database error: {e}")
                                        raise
                                c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                                updated_result = c.fetchone()
                                logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "whatsapp"})
                            logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
                            continue

                        if "HELP" in incoming_msg.upper():
                            response = "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you."
                            logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + response)
                        else:
                            response = ai_respond(incoming_msg, convo_id)

                        log_message(convo_id, "AI", response, "ai")
                        socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "whatsapp"})
                        try:
                            raise NotImplementedError("WhatsApp messaging not yet implemented")
                            logger.info(f"✅ WhatsApp message sent - To: {sender_id}, Body: {response}")
                        except Exception as e:
                            logger.error(f"❌ WhatsApp error sending message: {str(e)}")
                            socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to WhatsApp: {str(e)}", "channel": "whatsapp"})

                        if "sorry" in response.lower() or "HELP" in incoming_msg.upper():
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                                handoff_notified = c.fetchone()[0]
                            if not handoff_notified:
                                with get_db_connection() as conn:
                                    c = conn.cursor()
                                    try:
                                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                        conn.commit()
                                    except sqlite3.OperationalError as e:
                                        if "database is locked" in str(e):
                                            time.sleep(1)  # Retry after a short delay
                                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                            conn.commit()
                                        else:
                                            logger.error(f"❌ Database error: {e}")
                                            raise
                                time.sleep(3.0)
                                with get_db_connection() as conn:
                                    c = conn.cursor()
                                    c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                                    updated_result = c.fetchone()
                                logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "whatsapp"})
                                logger.info(f"✅ WhatsApp handoff triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")

                    except Exception as e:
                        logger.error(f"❌ Error in /whatsapp endpoint: {e}")
            else:
                logger.info("✅ No messages found in WhatsApp update, skipping processing")
    logger.info("✅ Returning OK for WhatsApp")
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
        c.execute("SELECT id, chat_id FROM conversations WHERE username = ? AND channel = 'telegram'", (to_number,))
        convo = c.fetchone()
    if not convo:
        logger.error("❌ Conversation not found in /send-welcome")
        return jsonify({"error": "Conversation not found"}), 404
    convo_id, chat_id = convo
    if not chat_id:
        logger.error(f"❌ No chat_id found for convo_id {convo_id} in /send-welcome")
        return jsonify({"error": "No chat_id found for this conversation"}), 404
    try:
        language = detect_language("", convo_id)  # Use conversation history for language detection
        logger.info(f"✅ Sending welcome message to Telegram chat {chat_id}")
        welcome_message = f"Welcome to our hotel, {user_name}! We're here to assist with your bookings. Reply 'BOOK' to start or 'HELP' for assistance." if language == "en" else \
                         f"¡Bienvenido a nuestro hotel, {user_name}! Estamos aquí para ayudarte con tus reservas. Responde 'RESERVAR' para comenzar o 'AYUDA' para asistencia."
        if not send_telegram_message(chat_id, welcome_message):
            logger.error(f"❌ Failed to send welcome message to Telegram for chat_id {chat_id}")
            socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send welcome message to Telegram", "channel": "telegram"})
            return jsonify({"error": "Failed to send welcome message"}), 500
        logger.info("✅ Logging welcome message in /send-welcome")
        log_message(convo_id, "AI", f"Welcome to our hotel, {user_name}!" if language == "en" else f"¡Bienvenido a nuestro hotel, {user_name}!", "ai")
        logger.info("✅ Emitting new_message event in /send-welcome")
        socketio.emit("new_message", {"convo_id": convo_id, "message": f"Welcome to our hotel, {user_name}!" if language == "en" else f"¡Bienvenido a nuestro hotel, {user_name}!", "sender": "ai", "channel": "telegram"})
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
        logger.error("❌ Missing conversation_id in /handoff request")
        return jsonify({"error": "Missing conversation_id"}), 400
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                return jsonify({"error": "Conversation not found"}), 404
            username, chat_id, channel = result
            c.execute("UPDATE conversations SET assigned_agent = ?, ai_enabled = 0 WHERE id = ?", (current_user.username, convo_id))
            conn.commit()
            c.execute("SELECT assigned_agent, ai_enabled FROM conversations WHERE id = ?", (convo_id,))
            updated_result = c.fetchone()
            logger.info(f"✅ After handoff for convo_id {convo_id}: assigned_agent={updated_result[0]}, ai_enabled={updated_result[1]}")
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
        logger.info(f"✅ Agent {current_user.username} took over convo_id {convo_id}, AI disabled")
        return jsonify({"status": "success", "message": f"Agent {current_user.username} has taken over the conversation"})
    except Exception as e:
        logger.error(f"❌ Error in /handoff endpoint: {str(e)}")
        return jsonify({"error": "Failed to process handoff"}), 500

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
            c.execute("SELECT username, chat_id, channel, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                return jsonify({"error": "Conversation not found"}), 404
            username, chat_id, channel, assigned_agent = result

            if assigned_agent != current_user.username:
                logger.error(f"❌ Agent {current_user.username} is not assigned to convo_id {convo_id}")
                return jsonify({"error": "You are not assigned to this conversation"}), 403

            c.execute("UPDATE conversations SET assigned_agent = NULL, ai_enabled = 1, handoff_notified = 0, visible_in_conversations = 0 WHERE id = ?", (convo_id,))
            conn.commit()

            c.execute("SELECT ai_enabled FROM conversations WHERE id = ?", (convo_id,))
            updated_result = c.fetchone()
            if updated_result:
                ai_enabled = updated_result[0]
                logger.info(f"✅ After handback, ai_enabled for convo_id {convo_id} is {ai_enabled}")
            else:
                logger.error(f"❌ Failed to verify ai_enabled for convo_id {convo_id} after handback")

        language = detect_language("", convo_id)  # Use conversation history
        handback_message = "The sales agent has handed the conversation back to me. Anything else I can help you with?" if language == "en" else \
                          "El agente de ventas ha devuelto la conversación a mí. ¿Hay algo más en lo que pueda ayudarte?"
        log_message(convo_id, "AI", handback_message, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": handback_message, "sender": "ai", "channel": channel})
        if channel == "telegram":
            if not chat_id:
                logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handback message to Telegram: No chat_id found", "channel": channel})
            else:
                logger.info(f"Sending handback message to Telegram - To: {chat_id}, Body: {handback_message}")
                if not send_telegram_message(chat_id, handback_message):
                    logger.error(f"❌ Failed to send handback message to Telegram for chat_id {chat_id}")
                    socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handback message to Telegram", "channel": channel})
                else:
                    logger.info("✅ Handback message sent to Telegram: " + handback_message)
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

        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
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
