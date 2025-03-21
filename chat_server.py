import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import openai
import psycopg2
from psycopg2.extras import DictCursor
import os
import requests
import json
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from datetime import datetime, timedelta
import time
import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from contextlib import contextmanager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app and SocketIO (removed duplicate initialization)
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecretkey")
CORS(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    ping_timeout=60,
    ping_interval=25
)

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("⚠️ TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
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

WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", None)
WHATSAPP_API_URL = "https://api.whatsapp.com"

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

# Validate required environment variables
required_vars = {
    "DB_HOST": DB_HOST,
    "DB_NAME": DB_NAME,
    "DB_USER": DB_USER,
    "DB_PASS": DB_PASS,
}
for var_name, var_value in required_vars.items():
    if not var_value:
        logger.error(f"❌ Missing required environment variable: {var_name}")
        raise ValueError(f"Missing required environment variable: {var_name}")

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
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            cursor_factory=DictCursor
        )
        logger.info("✅ Opened PostgreSQL database connection")
        yield conn
    except Exception as e:
        logger.error(f"❌ Error opening PostgreSQL database connection: {e}")
        raise
    finally:
        if conn is not None:
            conn.close()
            logger.info("✅ Closed PostgreSQL database connection")

# Initialize the database (create tables if they don't exist)
def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        logger.info(f"Connection object: {type(conn)}")
        # Drop the tables and any dependent objects
        c.execute("DROP TABLE IF EXISTS messages CASCADE")
        c.execute("DROP TABLE IF EXISTS conversations CASCADE")
        c.execute("DROP TABLE IF EXISTS agents CASCADE")
        c.execute("DROP TABLE IF EXISTS settings CASCADE")
        # Create the agents table
        c.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL
            )
        """)
        # Create the conversations table with additional columns
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                phone_number TEXT,
                message TEXT NOT NULL,
                response TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                agent_id TEXT,
                channel TEXT,
                visible_in_conversations BOOLEAN DEFAULT TRUE,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                chat_id TEXT,
                ai_enabled BOOLEAN DEFAULT TRUE,
                handoff_notified BOOLEAN DEFAULT FALSE,
                assigned_agent TEXT,
                booking_intent TEXT
            )
        """)
        # Create the messages table
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER REFERENCES conversations(id),
                user TEXT,
                message TEXT NOT NULL,
                sender TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Create the settings table
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()
        logger.info("✅ Database initialized with PostgreSQL")

        # Add test agents if the table is empty
        c.execute("SELECT COUNT(*) FROM agents")
        count = c.fetchone()[0]
        if count == 0:
            test_agents = [
                ("agent1", "password1"),
                ("agent2", "password2"),
            ]
            c.executemany(
                "INSERT INTO agents (username, password) VALUES (%s, %s)",
                test_agents
            )
            conn.commit()
            logger.info("✅ Added test agents")

        # Add test conversations if the table is empty
        c.execute("SELECT COUNT(*) FROM conversations")
        count = c.fetchone()[0]
        if count == 0:
            test_conversations = [
                ("whatsapp_+1234567890", "+1234567890", "Hello, how can I help you?", "Hi! I'd like some assistance.", "2023-10-01 10:00:00", "agent1", "whatsapp", True, "2023-10-01 10:00:00", "+1234567890", True, False, "agent1", None),
                ("whatsapp_+0987654321", "+0987654321", "What are your hours?", "Our hours are 9 AM to 5 PM.", "2023-10-01 10:05:00", "agent2", "whatsapp", True, "2023-10-01 10:05:00", "+0987654321", True, False, "agent2", None),
            ]
            c.executemany(
                "INSERT INTO conversations (username, phone_number, message, response, timestamp, agent_id, channel, visible_in_conversations, last_updated, chat_id, ai_enabled, handoff_notified, assigned_agent, booking_intent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                test_conversations
            )
            conn.commit()
            logger.info("✅ Added test conversations")

        # Add default settings if the table is empty
        c.execute("SELECT COUNT(*) FROM settings")
        count = c.fetchone()[0]
        if count == 0:
            c.execute("INSERT INTO settings (key, value) VALUES (%s, %s)", ("ai_enabled", "1"))
            conn.commit()
            logger.info("✅ Added default settings")

def log_message(convo_id, user, message, sender):
    with get_db_connection() as conn:
        c = conn.cursor()
        try:
            c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (%s, %s, %s, %s)", (convo_id, user, message, sender))
            c.execute("UPDATE conversations SET message = %s, last_updated = CURRENT_TIMESTAMP WHERE id = %s", (message, convo_id))
            if sender == "agent":
                c.execute("UPDATE conversations SET ai_enabled = FALSE WHERE id = %s", (convo_id,))
                logger.info(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
            conn.commit()
            logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
        except psycopg2.Error as e:
            logger.error(f"❌ Database error in log_message: {str(e)}")
            raise

class Agent(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(agent_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, username FROM agents WHERE id = %s", (agent_id,))
        agent = c.fetchone()
        if agent:
            return Agent(agent['id'], agent['username'])
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
        c.execute("SELECT id, username FROM agents WHERE username = %s AND password = %s", (username, password))
        agent = c.fetchone()
        if agent:
            agent_obj = Agent(agent['id'], agent['username'])
            login_user(agent_obj)
            logger.info(f"✅ Login successful for agent: {agent['username']}")
            return jsonify({"message": "Login successful", "agent": agent['username']})
    logger.error("❌ Invalid credentials in /login request")
    return jsonify({"message": "Invalid credentials"}), 401

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    logger.info("✅ Logout successful")
    return jsonify({"message": "Logged out successfully"})

@app.route("/live-messages")
def live_messages_page():
    return render_template("live-messages.html")

@app.route("/all-whatsapp-messages", methods=["GET"])
def get_all_whatsapp_messages():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, username FROM conversations WHERE channel = 'whatsapp' ORDER BY last_updated DESC")
            conversations = []
            for row in c.fetchall():
                convo_id, username = row['id'], row['username']
                c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = %s ORDER BY timestamp ASC", (convo_id,))
                messages = [{"message": msg['message'], "sender": msg['sender'], "timestamp": msg['timestamp'].isoformat()} for msg in c.fetchall()]
                conversations.append({
                    "convo_id": convo_id,
                    "username": username,
                    "messages": messages
                })
            logger.info(f"✅ Fetched {len(conversations)} WhatsApp conversations")
            return jsonify({"conversations": conversations})
    except Exception as e:
        logger.error(f"❌ Error fetching WhatsApp messages: {str(e)}")
        return jsonify({"error": "Failed to fetch WhatsApp messages"}), 500

@app.route("/conversations", methods=["GET"])
def get_conversations():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, phone_number, message, agent_id, channel, visible_in_conversations FROM conversations ORDER BY last_updated DESC")
            raw_conversations = c.fetchall()
            logger.info(f"✅ Raw conversations from database: {raw_conversations}")
            c.execute("SELECT id, phone_number, channel, agent_id FROM conversations WHERE visible_in_conversations = TRUE ORDER BY last_updated DESC")
            conversations = []
            for row in c.fetchall():
                convo_id, phone_number, channel, agent_id = row['id'], row['phone_number'], row['channel'], row['agent_id']
                display_name = phone_number
                conversations.append({
                    "id": convo_id,
                    "username": phone_number,
                    "chat_id": phone_number,
                    "channel": channel,
                    "assigned_agent": agent_id,
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
            c.execute("SELECT visible_in_conversations FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
        if result:
            logger.info(f"✅ Visibility check for convo ID {convo_id}: {bool(result['visible_in_conversations'])}")
            return jsonify({"visible": bool(result['visible_in_conversations'])})
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
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = %s ORDER BY timestamp ASC", (convo_id,))
            messages = [{"message": row['message'], "sender": row['sender'], "timestamp": row['timestamp'].isoformat()} for row in c.fetchall()]
            c.execute("SELECT username, chat_id FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            username = result['username'] if result else "Unknown"
            chat_id = result['chat_id'] if result else None
            logger.info(f"✅ Fetched {len(messages)} messages for convo_id {convo_id}")
            return jsonify({"messages": messages, "username": username, "chat_id": chat_id})
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo_id {convo_id}: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

def send_telegram_message(chat_id, text):
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
    try:
        if not TWILIO_WHATSAPP_NUMBER.startswith("whatsapp:"):
            logger.error(f"❌ TWILIO_WHATSAPP_NUMBER must start with 'whatsapp:': {TWILIO_WHATSAPP_NUMBER}")
            return False

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        if not phone_number.startswith("whatsapp:"):
            phone_number = f"whatsapp:{phone_number}"

        message = client.messages.create(
            body=text,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=phone_number
        )

        logger.info(f"✅ Sent WhatsApp message to {phone_number}: {text}, SID: {message.sid}")
        return True
    except TwilioRestException as e:
        logger.error(f"❌ Twilio error sending WhatsApp message: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp message: {str(e)}")
        return False

def send_instagram_message(user_id, text):
    raise NotImplementedError("Instagram messaging not yet implemented")

def check_availability(check_in, check_out):
    logger.info(f"✅ Checking availability from {check_in} to {check_out}")
    try:
        current_date = check_in
        while current_date < check_out:
            start_time = current_date.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
            end_time = (current_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'

            events_result = service.events().list(
                calendarId='a33289c61cf358216690e7cc203d116cec4c44075788fab3f2b200f5bbcd89cc@group.calendar.google.com',
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])

            if any(event.get('summary') == "Fully Booked" for event in events):
                return f"Sorry, the dates from {check_in.strftime('%B %d, %Y')} to {(check_out - timedelta(days=1)).strftime('%B %d, %Y')} are not available. We are fully booked on {current_date.strftime('%B %d, %Y')}."

            current_date += timedelta(days=1)

        return f"Yes, the dates from {check_in.strftime('%B %d, %Y')} to {(check_out - timedelta(days=1)).strftime('%B %d, %Y')} are available."
    except Exception as e:
        logger.error(f"❌ Google Calendar API error: {str(e)}")
        return "Sorry, I’m having trouble checking availability right now. I’ll connect you with a team member to assist you."

def ai_respond(message, convo_id):
    logger.info(f"✅ Generating AI response for convo_id {convo_id}: {message}")
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
                return "Sorry, I couldn’t understand the dates. Please use a format like 'March 20' or '20 de marzo'." if "sorry" in message.lower() else \
                       "Lo siento, no entendí las fechas. Por favor, usa un formato como '20 de marzo' o 'March 20'."

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
                return "The check-out date must be after the check-in date. Please provide a valid range." if "sorry" in message.lower() else \
                       "La fecha de salida debe ser posterior a la fecha de entrada. Por favor, proporciona un rango válido."

            availability = check_availability(check_in, check_out)
            if "are available" in availability.lower():
                booking_intent = f"{check_in.strftime('%Y-%m-%d')} to {check_out.strftime('%Y-%m-%d')}"
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET booking_intent = %s WHERE id = %s", (booking_intent, convo_id))
                    conn.commit()
                response = f"{availability}. Would you like to book?" if "sorry" in message.lower() else \
                           f"{availability.replace('are available', 'están disponibles')}. ¿Te gustaría reservar?"
            else:
                response = availability if "sorry" in message.lower() else \
                           availability.replace("are not available", "no están disponibles").replace("fully booked", "completamente reservado")
            return response

        is_spanish = any(spanish_word in message.lower() for spanish_word in ["reservar", "habitación", "disponibilidad"])
        if "book" in message.lower() or "booking" in message.lower() or "reservar" in message.lower():
            return "I’ll connect you with a team member to assist with your booking." if not is_spanish else \
                   "Te conectaré con un miembro del equipo para que te ayude con tu reserva."

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT user, message, sender FROM messages WHERE conversation_id = %s ORDER BY timestamp DESC LIMIT 10", (convo_id,))
            messages = c.fetchall()
        conversation_history = [
            {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel customer service and sales agent for Amapola Resort. Use the provided business information and Q&A to answer guest questions. Maintain conversation context. Detect the user's language (English or Spanish) based on their input and respond in the same language. If you don’t know the answer or the query is complex, respond with the appropriate escalation message in the user's language. Do not mention room types or pricing unless specifically asked."}
        ]
        for msg in messages:
            user, message_text, sender = msg['user'], msg['message'], msg['sender']
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
                    return ai_reply
                return ai_reply
            except Exception as e:
                logger.error(f"❌ OpenAI error (Attempt {attempt + 1}): {str(e)}")
                if attempt == retry_attempts - 1:
                    return "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if not is_spanish else \
                           "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."
                time.sleep(1)
                continue
    except Exception as e:
        logger.error(f"❌ Error in ai_respond for convo_id {convo_id}: {str(e)}")
        return "I’m sorry, I’m having trouble processing your request right now. I’ll connect you with a team member to assist you." if "sorry" in message.lower() else \
               "Lo siento, tengo problemas para procesar tu solicitud ahora mismo. Te conectaré con un miembro del equipo para que te ayude."

def detect_language(message, convo_id):
    spanish_keywords = ["hola", "gracias", "reservar", "habitación", "disponibilidad", "marzo", "abril"]
    if any(keyword in message.lower() for keyword in spanish_keywords):
        return "es"
    
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT message FROM messages WHERE conversation_id = %s ORDER BY timestamp DESC LIMIT 5", (convo_id,))
        messages = c.fetchall()
        for msg in messages:
            if any(keyword in msg['message'].lower() for keyword in spanish_keywords):
                return "es"
    
    return "en"

@app.route("/check-auth", methods=["GET"])
def check_auth():
    return jsonify({"is_authenticated": current_user.is_authenticated, "agent": current_user.username if current_user.is_authenticated else None})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    convo_id = data.get("convo_id")
    user_message = data.get("message")
    channel = data.get("channel", "whatsapp")
    if not convo_id or not user_message:
        logger.error("❌ Missing required fields in /chat request")
        return jsonify({"error": "Missing required fields"}), 400
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel, assigned_agent, ai_enabled, booking_intent FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                return jsonify({"error": "Conversation not found"}), 404
            username, chat_id, channel, assigned_agent, ai_enabled, booking_intent = result['username'], result['chat_id'], result['channel'], result['assigned_agent'], result['ai_enabled'], result['booking_intent']

        sender = "agent" if current_user.is_authenticated else "user"
        log_message(convo_id, username, user_message, sender)

        language = detect_language(user_message, convo_id)

        if sender == "agent":
            socketio.emit("new_message", {"convo_id": convo_id, "message": user_message, "sender": "agent", "channel": channel})
            socketio.emit("live_message", {"convo_id": convo_id, "message": user_message, "sender": "agent", "username": username})
            if channel == "whatsapp":
                if not chat_id:
                    logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                    socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp: No chat_id found", "channel": channel})
                else:
                    if send_whatsapp_message(chat_id, user_message):
                        logger.info(f"✅ Sent WhatsApp message from agent to {chat_id}: {user_message}")
                    else:
                        logger.error(f"❌ Failed to send WhatsApp message to {chat_id}")
                        socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp", "channel": channel})
            return jsonify({"status": "success"})
            
        if ai_enabled:
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
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                    conn.commit()
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
                return jsonify({"reply": response})

            if "book" in user_message.lower() or "reservar" in user_message.lower():
                ai_reply = "I’ll connect you with a team member who can assist with your booking." if language == "en" else \
                          "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
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
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                    conn.commit()
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
                return jsonify({"reply": ai_reply})

            if "HELP" in user_message.upper() or "AYUDA" in user_message.upper():
                ai_reply = "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you." if language == "en" else \
                          "Lo siento, no pude procesar eso. Te conectaré con un miembro del equipo para que te ayude."
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
            if "sorry" in ai_reply.lower() or "lo siento" in ai_reply.lower():
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                    handoff_notified = c.fetchone()['handoff_notified']
                if not handoff_notified:
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                        conn.commit()
                    socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
            return jsonify({"reply": ai_reply})
        else:
            return jsonify({"status": "Message received, AI disabled"})
    except Exception as e:
        logger.error(f"❌ Error in /chat endpoint: {str(e)}")
        return jsonify({"error": "Failed to process chat message"}), 500

@app.route("/telegram", methods=["POST"])
def telegram():
    update = request.get_json()
    if "message" not in update:
        return jsonify({"status": "ok"}), 200

    message_data = update["message"]
    chat_id = str(message_data["chat"]["id"])
    text = message_data.get("text", "")
    convo_id = None

    with get_db_connection() as conn:
        c = conn.cursor()
        prefixed_chat_id = f"telegram_{chat_id}"
        c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent, booking_intent FROM conversations WHERE username = %s AND channel = 'telegram'", (prefixed_chat_id,))
        result = c.fetchone()

        if not result:
            c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations) VALUES (%s, %s, 'telegram', TRUE, FALSE) RETURNING id", (prefixed_chat_id, chat_id))
            convo_id = c.fetchone()['id']
            conn.commit()
            ai_enabled = True
            handoff_notified = False
            assigned_agent = None
            booking_intent = None
            language = detect_language(text, convo_id)
            welcome_message = "Gracias por contactarnos." if language == "es" else "Thank you for contacting us."
            log_message(convo_id, chat_id, welcome_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, welcome_message):
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send welcome message to Telegram", "channel": "telegram"})
        else:
            convo_id, ai_enabled, handoff_notified, assigned_agent, booking_intent = result['id'], result['ai_enabled'], result['handoff_notified'], result['assigned_agent'], result['booking_intent']

        log_message(convo_id, chat_id, text, "user")
        socketio.emit("new_message", {"convo_id": convo_id, "message": text, "sender": "user", "channel": "telegram"})

        if not ai_enabled:
            return jsonify({}), 200

        response = ai_respond(text, convo_id)

        language = detect_language(text, convo_id)
        if booking_intent and ("yes" in text.lower() or "proceed" in text.lower() or "sí" in text.lower()):
            handoff_message = f"Great! An agent will assist you with booking for {booking_intent}. Please wait." if language == "en" else \
                             f"¡Excelente! Un agente te ayudará con la reserva para {booking_intent}. Por favor, espera."
            log_message(convo_id, "AI", handoff_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, handoff_message):
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to Telegram", "channel": "telegram"})
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                conn.commit()
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
            return jsonify({}), 200

        if "book" in text.lower() or "booking" in text.lower() or "reservar" in text.lower():
            handoff_message = "I’ll connect you with a team member to assist with your booking." if language == "en" else \
                             "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
            log_message(convo_id, "AI", handoff_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, handoff_message):
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to Telegram", "channel": "telegram"})
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                conn.commit()
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
            return jsonify({}), 200

        if response:
            log_message(convo_id, "AI", response, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "telegram"})
            if not send_telegram_message(chat_id, response):
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to Telegram", "channel": "telegram"})

        if "sorry" in response.lower() or "lo siento" in response.lower():
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                handoff_notified = c.fetchone()['handoff_notified']
            if not handoff_notified:
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                    conn.commit()
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": "telegram"})
        return jsonify({}), 200

@app.route("/instagram", methods=["POST"])
def instagram():
    data = request.get_json()
    if "object" not in data or data["object"] != "instagram":
        return "OK", 200
    for entry in data.get("entry", []):
        for messaging in entry.get("messaging", []):
            sender_id = messaging["sender"]["id"]
            incoming_msg = messaging["message"].get("text", "")
            with get_db_connection() as conn:
                c = conn.cursor()
                prefixed_sender_id = f"instagram_{sender_id}"
                c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent FROM conversations WHERE username = %s AND channel = 'instagram'", (prefixed_sender_id,))
                convo = c.fetchone()
            if not convo:
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("INSERT INTO conversations (username, message, channel, ai_enabled, visible_in_conversations) VALUES (%s, %s, %s, TRUE, FALSE) RETURNING id", 
                              (prefixed_sender_id, incoming_msg, "instagram"))
                    convo_id = c.fetchone()['id']
                    conn.commit()
                    ai_enabled = True
                    handoff_notified = False
                    assigned_agent = None
                    welcome_message = "Thank you for contacting us."
                    log_message(convo_id, "AI", welcome_message, "ai")
                    socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "instagram"})
                    try:
                        requests.post(
                            f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                            json={"recipient": {"id": sender_id}, "message": {"text": welcome_message}}
                        )
                    except Exception as e:
                        socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Instagram: {str(e)}", "channel": "instagram"})
            else:
                convo_id, ai_enabled, handoff_notified, assigned_agent = convo['id'], convo['ai_enabled'], convo['handoff_notified'], convo['assigned_agent']

            log_message(convo_id, prefixed_sender_id, incoming_msg, "user")
            socketio.emit("new_message", {"convo_id": convo_id, "message": incoming_msg, "sender": "user", "channel": "instagram"})

            if not ai_enabled:
                continue

            if "book" in incoming_msg.lower():
                response = "I’ll connect you with a team member who can assist with your booking."
                log_message(convo_id, "AI", response, "ai")
                socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "instagram"})
                try:
                    requests.post(
                        f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                        json={"recipient": {"id": sender_id}, "message": {"text": response}}
                    )
                except Exception as e:
                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Instagram: {str(e)}", "channel": "instagram"})
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                    conn.commit()
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "instagram"})
                continue

            if "HELP" in incoming_msg.upper():
                response = "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you."
            else:
                response = ai_respond(incoming_msg, convo_id)

            log_message(convo_id, "AI", response, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "instagram"})
            try:
                requests.post(
                    f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                    json={"recipient": {"id": sender_id}, "message": {"text": response}}
                )
            except Exception as e:
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Instagram: {str(e)}", "channel": "instagram"})

            if "sorry" in response.lower() or "HELP" in incoming_msg.upper():
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                    handoff_notified = c.fetchone()['handoff_notified']
                if not handoff_notified:
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                        conn.commit()
                    socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": sender_id, "channel": "instagram"})
    return "EVENT_RECEIVED", 200

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    validator = RequestValidator(os.environ.get("TWILIO_AUTH_TOKEN"))
    request_valid = validator.validate(
        request.url,
        request.form,
        request.headers.get("X-Twilio-Signature", "")
    )
    if not request_valid:
        logger.error("❌ Invalid Twilio signature")
        return "Invalid request", 403

    from_number = request.form.get("From")
    message_body = request.form.get("Body", "").strip()

    prefixed_from = f"whatsapp_{from_number.replace('whatsapp:', '')}"
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent, booking_intent FROM conversations WHERE username = %s AND channel = 'whatsapp'", (prefixed_from,))
        result = c.fetchone()

        if not result:
            c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations) VALUES (%s, %s, 'whatsapp', TRUE, FALSE) RETURNING id", (prefixed_from, from_number))
            convo_id = c.fetchone()['id']
            conn.commit()
            ai_enabled = True
            handoff_notified = False
            assigned_agent = None
            booking_intent = None

            language = detect_language(message_body, convo_id)
            welcome_message = "Gracias por contactarnos." if language == "es" else "Thank you for contacting us."
            log_message(convo_id, "AI", welcome_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "whatsapp"})
            socketio.emit("live_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "username": prefixed_from})

            if not send_whatsapp_message(from_number, welcome_message):
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send welcome message to WhatsApp", "channel": "whatsapp"})
        else:
            convo_id, ai_enabled, handoff_notified, assigned_agent, booking_intent = result['id'], result['ai_enabled'], result['handoff_notified'], result['assigned_agent'], result['booking_intent']

        # Check global AI setting
        c.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
        global_ai_result = c.fetchone()
        global_ai_enabled = int(global_ai_result['value']) if global_ai_result else 1

    log_message(convo_id, from_number, message_body, "user")
    socketio.emit("new_message", {"convo_id": convo_id, "message": message_body, "sender": "user", "channel": "whatsapp"})
    socketio.emit("live_message", {"convo_id": convo_id, "message": message_body, "sender": "user", "username": prefixed_from})

    if not global_ai_enabled or not ai_enabled:
        logger.info(f"AI response skipped for convo_id {convo_id}: global_ai_enabled={global_ai_enabled}, ai_enabled={ai_enabled}")
        return jsonify({}), 200

    # Handle booking intent if user confirms
    language = detect_language(message_body, convo_id)
    if booking_intent and ("yes" in message_body.lower() or "proceed" in message_body.lower() or "sí" in message_body.lower()):
        handoff_message = f"Great! An agent will assist you with booking for {booking_intent}. Please wait." if language == "en" else \
                         f"¡Excelente! Un agente te ayudará con la reserva para {booking_intent}. Por favor, espera."
        log_message(convo_id, "AI", handoff_message, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": "whatsapp"})
        socketio.emit("live_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "username": prefixed_from})

        if not send_whatsapp_message(from_number, handoff_message):
            socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to WhatsApp", "channel": "whatsapp"})

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
            conn.commit()
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": from_number, "channel": "whatsapp"})
        return jsonify({}), 200

    # Handle booking request (escalation to agent)
    if "book" in message_body.lower() or "booking" in message_body.lower() or "reservar" in message_body.lower():
        handoff_message = "I’ll connect you with a team member to assist with your booking." if language == "en" else \
                         "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
        log_message(convo_id, "AI", handoff_message, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": "whatsapp"})
        socketio.emit("live_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "username": prefixed_from})

        if not send_whatsapp_message(from_number, handoff_message):
            socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to WhatsApp", "channel": "whatsapp"})

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
            conn.commit()
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": from_number, "channel": "whatsapp"})
        return jsonify({}), 200

    # Process AI response
    response = ai_respond(message_body, convo_id)
    socketio.emit("ai_activity", {"convo_id": convo_id, "message": f"AI processing: {response}", "channel": "whatsapp"})
    socketio.emit("live_message", {"convo_id": convo_id, "message": response, "sender": "ai", "username": prefixed_from})
    log_message(convo_id, "AI", response, "ai")
    socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "whatsapp"})

    if not send_whatsapp_message(from_number, response):
        socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to WhatsApp", "channel": "whatsapp"})
    else:
        logger.info(f"✅ Sent WhatsApp message - To: {from_number}, Body: {response}")

    if "sorry" in response.lower() or "lo siento" in response.lower():
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
            handoff_notified = c.fetchone()['handoff_notified']
            if not handoff_notified:
                c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                conn.commit()
                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": from_number, "channel": "whatsapp"})
    
    return jsonify({}), 200
    
@app.route("/whatsapp", methods=["GET"])
def whatsapp_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    verify_token = os.getenv("VERIFY_TOKEN", "mysecretverifytoken")
    
    if mode == "subscribe" and token == verify_token:
        logger.info("✅ WhatsApp webhook verification successful")
        return challenge, 200
    logger.error("❌ WhatsApp webhook verification failed")
    return "Verification failed", 403

@app.route("/assign-agent", methods=["POST"])
@login_required
def assign_agent():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    agent = data.get("agent")

    if not convo_id or not agent:
        logger.error("❌ Missing conversation_id or agent in /assign-agent request")
        return jsonify({"error": "Missing required fields"}), 400

    if agent != current_user.username:
        logger.error(f"❌ Agent mismatch: {agent} != {current_user.username}")
        return jsonify({"error": "Agent mismatch"}), 403

    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET assigned_agent = %s, visible_in_conversations = TRUE WHERE id = %s", (agent, convo_id))
            conn.commit()
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "agent": agent})
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"❌ Error assigning agent to convo_id {convo_id}: {e}")
        return jsonify({"error": "Failed to assign agent"}), 500

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'GET':
        try:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
                result = c.fetchone()
                ai_enabled = result['value'] if result else '1'  # Default to enabled
                logger.info(f"✅ Fetched ai_enabled setting: {ai_enabled}")
                return jsonify({'ai_enabled': ai_enabled})
        except Exception as e:
            logger.error(f"❌ Error fetching settings: {e}")
            return jsonify({'error': f'Failed to fetch settings: {str(e)}'}), 500
    else:  # POST
        try:
            data = request.get_json()
            key = data.get('key')
            value = data.get('value')
            if not key or value is None:
                logger.error("❌ Missing key or value in /settings POST request")
                return jsonify({'error': 'Missing key or value'}), 400

            # Ensure value is a string
            if not isinstance(value, str):
                value = str(value)
                logger.info(f"Converted value to string: {value}")

            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s", (key, value, value))
                conn.commit()

            socketio.emit('settings_updated', {'ai_enabled': value})
            logger.info(f"✅ Updated setting {key} to {value} and emitted settings_updated event")
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"❌ Error updating settings: {e}")
            return jsonify({'error': f'Failed to update settings: {str(e)}'}), 500

@app.route("/test-ai", methods=["POST"])
def test_ai():
    data = request.get_json()
    message = data.get("message")
    if not message:
        logger.error("❌ Missing message in /test-ai request")
        return jsonify({"error": "Missing message"}), 400

    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO conversations (username, message, channel, ai_enabled, visible_in_conversations) VALUES (%s, %s, %s, TRUE, FALSE) RETURNING id", 
                      ("test_user", message, "test"))
            temp_convo_id = c.fetchone()['id']
            conn.commit()

        log_message(temp_convo_id, "test_user", message, "user")
        response = ai_respond(message, temp_convo_id)

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM conversations WHERE id = %s", (temp_convo_id,))
            c.execute("DELETE FROM messages WHERE conversation_id = %s", (temp_convo_id,))
            conn.commit()

        return jsonify({"response": response})
    except Exception as e:
        logger.error(f"❌ Error in /test-ai endpoint: {e}")
        return jsonify({"error": "Failed to test AI"}), 500

@app.route("/")
def index():
    return render_template("dashboard.html")

@socketio.on("connect")
def handle_connect():
    logger.info("✅ Client connected to SocketIO")
    emit("connection_status", {"status": "connected"})

@socketio.on("disconnect")
def handle_disconnect():
    logger.info("❌ Client disconnected from SocketIO")

@socketio.on("join_conversation")
def handle_join_conversation(data):
    convo_id = data.get("conversation_id")
    if convo_id:
        join_room(convo_id)
        logger.info(f"✅ Client joined conversation room: {convo_id}")
        emit("joined_conversation", {"conversation_id": convo_id})

@socketio.on("leave_conversation")
def handle_leave_conversation(data):
    convo_id = data.get("conversation_id")
    if convo_id:
        leave_room(convo_id)
        logger.info(f"✅ Client left conversation room: {convo_id}")
        emit("left_conversation", {"conversation_id": convo_id})

@socketio.on("agent_message")
def handle_agent_message(data):
    convo_id = data.get("conversation_id")
    message = data.get("message")
    channel = data.get("channel", "dashboard")

    if not convo_id or not message:
        logger.error("❌ Missing conversation_id or message in agent_message event")
        emit("error", {"message": "Missing required fields"})
        return

    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                return
            username, chat_id, convo_channel = result['username'], result['chat_id'], result['channel']

        log_message(convo_id, username, message, "agent")
        emit("new_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": "agent",
            "channel": convo_channel
        }, room=convo_id)
        emit("live_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": "agent",
            "username": username
        })

        if convo_channel == "whatsapp":
            if not chat_id:
                emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp: No chat_id found", "channel": convo_channel})
            else:
                if not send_whatsapp_message(chat_id, message):
                    emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp", "channel": convo_channel})
    except Exception as e:
        logger.error(f"❌ Error in agent_message event for convo_id {convo_id}: {e}")
        emit("error", {"convo_id": convo_id, "message": f"Failed to process agent message: {str(e)}", "channel": convo_channel})

@socketio.on("new_message")
def handle_new_message(data):
    convo_id = data.get("convo_id")
    message = data.get("message")
    sender = data.get("sender")
    channel = data.get("channel", "whatsapp")

    if not convo_id or not message or not sender:
        logger.error("❌ Missing required fields in new_message event")
        emit("error", {"message": "Missing required fields"})
        return

    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel, ai_enabled, booking_intent FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                return
            username, chat_id, channel, ai_enabled, booking_intent = result['username'], result['chat_id'], result['channel'], result['ai_enabled'], result['booking_intent']

            # Check global AI setting
            c.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
            global_ai_result = c.fetchone()
            global_ai_enabled = int(global_ai_result['value']) if global_ai_result else 1

            # Insert the new message into the messages table
            c.execute("INSERT INTO messages (conversation_id, sender, message) VALUES (%s, %s, %s)", (convo_id, sender, message))
            # Update the last_updated timestamp in the conversations table
            c.execute("UPDATE conversations SET last_updated = CURRENT_TIMESTAMP WHERE id = %s", (convo_id,))
            conn.commit()
            logger.info(f"✅ Logged message for convo_id {convo_id} from {sender}")

        if sender == "agent":
            return  # Agent messages are already handled by the agent_message event

        # Broadcast the message to the room
        emit("live_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": sender,
            "username": username
        }, room=convo_id)

        if not global_ai_enabled or not ai_enabled:
            logger.info(f"AI response skipped for convo_id {convo_id}: global_ai_enabled={global_ai_enabled}, ai_enabled={ai_enabled}")
            return

        language = detect_language(message, convo_id)

        # Handle booking intent if user confirms
        if booking_intent and ("yes" in message.lower() or "proceed" in message.lower() or "sí" in message.lower()):
            handoff_message = f"Great! An agent will assist you with booking for {booking_intent}. Please wait." if language == "en" else \
                             f"¡Excelente! Un agente te ayudará con la reserva para {booking_intent}. Por favor, espera."
            log_message(convo_id, "AI", handoff_message, "ai")
            emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": channel})
            emit("live_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "username": username}, room=convo_id)

            if channel == "whatsapp":
                if not chat_id:
                    emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to WhatsApp: No chat_id found", "channel": channel})                                    
                else:
                    if not send_whatsapp_message(chat_id, handoff_message):
                        emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to WhatsApp", "channel": channel})
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                conn.commit()
            emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
            return

        # Handle booking request (escalation to agent)
        if "book" in message.lower() or "booking" in message.lower() or "reservar" in message.lower():
            handoff_message = "I’ll connect you with a team member to assist with your booking." if language == "en" else \
                             "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
            log_message(convo_id, "AI", handoff_message, "ai")
            emit("new_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "channel": channel})
            emit("live_message", {"convo_id": convo_id, "message": handoff_message, "sender": "ai", "username": username}, room=convo_id)

            if channel == "whatsapp":
                if not chat_id:
                    emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to WhatsApp: No chat_id found", "channel": channel})
                else:
                    if not send_whatsapp_message(chat_id, handoff_message):
                        emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to WhatsApp", "channel": channel})
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                conn.commit()
            emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
            return

        # Process AI response
        response = ai_respond(message, convo_id)
        log_message(convo_id, "AI", response, "ai")
        emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": channel})
        emit("live_message", {"convo_id": convo_id, "message": response, "sender": "ai", "username": username}, room=convo_id)

        if channel == "whatsapp":
            if not chat_id:
                emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to WhatsApp: No chat_id found", "channel": channel})
            else:
                if not send_whatsapp_message(chat_id, response):
                    emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to WhatsApp", "channel": channel})

        if "sorry" in response.lower() or "lo siento" in response.lower():
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                handoff_notified = c.fetchone()['handoff_notified']
                if not handoff_notified:
                    c.execute("UPDATE conversations SET handoff_notified = TRUE, visible_in_conversations = TRUE WHERE id = %s", (convo_id,))
                    conn.commit()
                    emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
    except Exception as e:
        logger.error(f"❌ Error in new_message event for convo_id {convo_id}: {e}")
        emit("error", {"convo_id": convo_id, "message": f"Failed to process new message: {str(e)}", "channel": channel})

# Additional SocketIO event handlers for completeness
@socketio.on("typing")
def handle_typing(data):
    convo_id = data.get("conversation_id")
    sender = data.get("sender")
    if convo_id and sender:
        emit("typing", {"convo_id": convo_id, "sender": sender}, room=convo_id, include_self=False)
        logger.info(f"✅ Typing event emitted for convo_id {convo_id} by {sender}")

@socketio.on("stop_typing")
def handle_stop_typing(data):
    convo_id = data.get("conversation_id")
    sender = data.get("sender")
    if convo_id and sender:
        emit("stop_typing", {"convo_id": convo_id, "sender": sender}, room=convo_id, include_self=False)
        logger.info(f"✅ Stop typing event emitted for convo_id {convo_id} by {sender}")

# Route to toggle conversation visibility
@app.route("/toggle-visibility", methods=["POST"])
@login_required
def toggle_visibility():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    visible = data.get("visible", True)

    if not convo_id:
        logger.error("❌ Missing conversation_id in /toggle-visibility request")
        return jsonify({"error": "Missing conversation ID"}), 400

    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE conversations SET visible_in_conversations = %s WHERE id = %s", (visible, convo_id))
            conn.commit()
        logger.info(f"✅ Updated visibility for convo_id {convo_id} to {visible}")
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "visible": visible})
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"❌ Error toggling visibility for convo_id {convo_id}: {e}")
        return jsonify({"error": "Failed to toggle visibility"}), 500

# Route to get list of agents (for assignment dropdown)
@app.route("/agents", methods=["GET"])
@login_required
def get_agents():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username FROM agents")
            agents = [row['username'] for row in c.fetchall()]
        logger.info(f"✅ Fetched {len(agents)} agents")
        return jsonify({"agents": agents})
    except Exception as e:
        logger.error(f"❌ Error fetching agents: {e}")
        return jsonify({"error": "Failed to fetch agents"}), 500

# Error handling for 404
@app.errorhandler(404)
def not_found(error):
    logger.error(f"❌ 404 error: {request.url}")
    return jsonify({"error": "Not found"}), 404

# Error handling for 500
@app.errorhandler(500)
def internal_error(error):
    logger.error(f"❌ 500 error: {str(error)}")
    return jsonify({"error": "Internal server error"}), 500

# Main entry point
if __name__ == "__main__":
    try:
        init_db()  # Initialize the database on startup
        logger.info("✅ Starting Flask-SocketIO server")
        socketio.run(app, host="0.0.0.0", port=5000, debug=False)
    except Exception as e:
        logger.error(f"❌ Failed to start server: {str(e)}")
        raise
    
