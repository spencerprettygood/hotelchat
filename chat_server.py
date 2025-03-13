import gevent.monkey
gevent.monkey.patch_all()

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import openai
import psycopg2
from psycopg2 import pool
import os
import urllib.parse as urlparse
import requests
import json
from datetime import datetime
import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from contextlib import contextmanager
import asyncio
from aiohttp import ClientSession

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

try:
    service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_KEY)
except json.JSONDecodeError as e:
    logger.error(f"⚠️ Invalid GOOGLE_SERVICE_ACCOUNT_KEY format: {e}")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY must be a valid JSON string")

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
service = build('calendar', 'v3', credentials=credentials)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("⚠️ TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_API_URL = "https://graph.instagram.com/v20.0"

# Placeholder for WhatsApp API
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", None)
WHATSAPP_API_URL = "https://api.whatsapp.com"  # Update with actual URL

# PostgreSQL configuration using DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set. Please set it in your Render environment.")

url = urlparse.urlparse(DATABASE_URL)
DB_PARAMS = {
    "dbname": url.path[1:],  # Remove the leading "/"
    "user": url.username,
    "password": url.password,
    "host": url.hostname,
    "port": url.port
}

# Initialize PostgreSQL connection pool
db_pool = None

def initialize_db_pool():
    global db_pool
    if db_pool is None:
        db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, **DB_PARAMS)  # Min 1, Max 20 connections
        logger.info("✅ Database connection pool initialized")

@contextmanager
def get_db_connection():
    """Context manager for PostgreSQL database connection with connection pool."""
    if db_pool is None:
        initialize_db_pool()
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)

def initialize_database():
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                latest_message TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigned_agent TEXT DEFAULT NULL,
                channel TEXT DEFAULT 'dashboard',
                opted_in INTEGER DEFAULT 0,
                ai_enabled INTEGER DEFAULT 1,
                handoff_notified INTEGER DEFAULT 0,
                visible_in_conversations INTEGER DEFAULT 0,
                booking_state TEXT DEFAULT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER NOT NULL,
                check_in DATE,
                check_out DATE,
                guests INTEGER,
                room_type TEXT,
                total_cost REAL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER NOT NULL,
                message_user TEXT NOT NULL,
                message TEXT NOT NULL,
                sender TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id))''')
            c.execute("SELECT COUNT(*) FROM agents")
            if c.fetchone()[0] == 0:
                c.execute("INSERT INTO agents (username, password) VALUES (%s, %s)", ("agent1", "password123"))
                logger.info("✅ Added test agent: agent1/password123")
            conn.commit()
    logger.info("✅ Database initialized")

# Initialize database and connection pool
initialize_db_pool()
initialize_database()

def add_test_conversations():
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM conversations")
            if c.fetchone()[0] == 0:
                test_conversations = [
                    ("guest1", "Hi, can I book a room?"),
                    ("guest2", "What’s the check-in time?"),
                    ("guest3", "Do you have a pool?")]
                convo_ids = []
                for username, message in test_conversations:
                    c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (%s, %s, %s, 1, 0) RETURNING id",
                            (username, message, "dashboard"))
                    convo_id = c.fetchone()[0]  # Fetch the returned ID
                    convo_ids.append(convo_id)
                test_messages = [
                    (convo_ids[0], "guest1", "Hi, can I book a room?", "user"),
                    (convo_ids[0], "AI", "Yes, I can help with that! What dates are you looking for?", "ai"),
                    (convo_ids[1], "guest2", "What’s the check-in time?", "user"),
                    (convo_ids[1], "AI", "Check-in is at 3 PM.", "ai"),
                    (convo_ids[2], "guest3", "Do you have a pool?", "user"),
                    (convo_ids[2], "AI", "Yes, we have an outdoor pool!", "ai")]
                for convo_id, message_user, message, sender in test_messages:
                    c.execute("INSERT INTO messages (conversation_id, message_user, message, sender) VALUES (%s, %s, %s, %s)", 
                            (convo_id, message_user, message, sender))
                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = %s", (convo_ids[0],))
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
        with conn.cursor() as c:
            c.execute("SELECT id, username FROM agents WHERE id = %s", (agent_id,))
            agent = c.fetchone()
            if agent:
                return Agent(agent[0], agent[1])
    return None

# Load or define the Q&A reference document
try:
    with open("qa_reference.txt", "r") as file:
        TRAINING_DOCUMENT = file.read()
    logger.info("✅ Loaded Q&A reference document")
except FileNotFoundError:
    TRAINING_DOCUMENT = """
    **Amapola Resort Chatbot Training Document**

    You are a friendly and professional hotel representative for Amapola Resort, a luxury beachfront hotel. Your role is to assist guests with inquiries, help with bookings, and provide information about the resort’s services and amenities. Below is a set of common questions and answers to guide your responses. Always maintain conversation context, ask follow-up questions to clarify user intent, and provide helpful, concise answers. If a query is too complex or requires personal assistance (e.g., specific booking modifications, complaints, or detailed itinerary planning), escalate to an agent by saying: 'I’m sorry, that’s a bit complex for me to handle. Let me connect you with an agent to assist you.'

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
    For example, you can say: "I’d like a Deluxe Room for 2 guests from March 10 to March 15." Once I have this information, I’ll check availability and guide you through the next steps. If you’d prefer assistance to finalize your booking, let me know!

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
    A: You can cancel your reservation for free up to 48 hours before your arrival. After that, you may be charged for the first night. If you need to modify or cancel a booking, I can connect you with an agent to assist with the details.

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
    - Escalate to an agent for complex requests, such as modifying an existing booking, handling complaints, or providing detailed recommendations.
    """
    logger.warning("⚠️ qa_reference.txt not found, using default training document")

# Semaphore for rate-limiting OpenAI calls
openai_semaphore = asyncio.Semaphore(10)  # Allow 10 concurrent calls

async def ai_respond(message, convo_id):
    logger.info(f"Generating AI response for convo_id {convo_id}: {message}")
    async with ClientSession() as session:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT message_user, message, sender FROM messages WHERE conversation_id = %s ORDER BY timestamp DESC LIMIT 10", (convo_id,))
                    messages = c.fetchall()
            conversation_history = [
                {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a friendly hotel representative. Use the provided business information and Q&A to answer guest questions naturally and professionally. If the query involves collecting payment details, credit card information, or any sensitive personal information, immediately escalate by saying: 'To proceed with your booking, I’ll need to collect payment details. Let me connect you with an agent to assist you securely.' Do not suggest providing the information directly or offer alternative contact methods like phone numbers or email addresses. For other complex queries or those requiring personal assistance, escalate with a similar message using 'agent'."}
            ]
            for msg in messages:
                message_user, message_text, sender = msg
                role = "user" if sender == "user" else "assistant"
                conversation_history.append({"role": role, "content": message_text})
            conversation_history.append({"role": "user", "content": message})

            async with openai_semaphore:
                retry_attempts = 2
                for attempt in range(retry_attempts):
                    try:
                        async with session.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai.api_key}"},
                            json={"model": "gpt-4o-mini", "messages": conversation_history, "max_tokens": 150}
                        ) as response:
                            result = await response.json()
                            ai_reply = result["choices"][0]["message"]["content"].strip()
                            logger.info(f"✅ AI reply: {ai_reply}")
                            return ai_reply
                    except Exception as e:
                        logger.error(f"❌ OpenAI error (Attempt {attempt + 1}): {str(e)}")
                        if attempt == retry_attempts - 1:
                            logger.info("✅ Set default AI reply due to repeated errors")
                            return "I’m sorry, I’m having trouble processing your request right now. Let me connect you with an agent to assist you."
                        await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ Error in ai_respond for convo_id {convo_id}: {str(e)}")
            return "I’m sorry, I’m having trouble processing your request right now. Let me connect you with an agent to assist you."

def send_telegram_message(chat_id, text):
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"✅ Sent Telegram message to {chat_id}: {text}")
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message to {chat_id}: {str(e)}")
        raise

def send_whatsapp_message(phone_number, text):
    raise NotImplementedError("WhatsApp messaging not yet implemented")

def send_instagram_message(user_id, text):
    raise NotImplementedError("Instagram messaging not yet implemented")

def check_availability(check_in, check_out):
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

def log_message(convo_id, message_user, message, sender):
    try:
        if message is None:
            logger.error(f"❌ Attempted to log a None message for convo_id {convo_id}, message_user {message_user}, sender {sender}")
            message = "Error: Message content unavailable"
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("INSERT INTO messages (conversation_id, message_user, message, sender) VALUES (%s, %s, %s, %s)", 
                        (convo_id, message_user, message, sender))
                c.execute("UPDATE conversations SET latest_message = %s, last_updated = CURRENT_TIMESTAMP WHERE id = %s", 
                        (message, convo_id))
                if sender == "agent":
                    c.execute("UPDATE conversations SET ai_enabled = 0 WHERE id = %s", (convo_id,))
                    logger.info(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
                conn.commit()
        logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
    except Exception as e:
        logger.error(f"❌ Error logging message for convo_id {convo_id}: {e}")
        raise

def handle_booking_flow(message, convo_id, chat_id, channel):
    logger.info(f"Handling booking flow for convo_id {convo_id}: {message}")
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute("SELECT booking_state FROM conversations WHERE id = %s", (convo_id,))
            booking_state = c.fetchone()[0]

    booking_state_dict = eval(booking_state) if booking_state else {}

    # Parse dates and numbers using regex as a fallback
    date_match = re.search(r'(\w+\s+\d+\s+to\s+\w+\s+\d+)', message, re.IGNORECASE)
    number_match = re.search(r'(\d+)\s*(guests)?', message, re.IGNORECASE)
    dates = date_match.group(0) if date_match else None
    guests = int(number_match.group(1)) if number_match else None

    if "book" in message.lower() and not booking_state_dict.get("status"):
        if not dates:
            ai_reply = "I’d love to help you book! Please provide your preferred dates (e.g., 'March 10 to March 15')."
            return (False, ai_reply)
        else:
            try:
                date_parts = re.split(r'\s+to\s+', dates.lower())
                months = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12}
                check_in_day = int(date_parts[1])
                check_out_day = int(date_parts[3])
                check_in_month = months[date_parts[0].split()[0]]
                check_out_month = months[date_parts[2].split()[0]]
                check_in = datetime(2025, check_in_month, check_in_day)
                check_out = datetime(2025, check_out_month, check_out_day)
                booking_state_dict = {"status": "awaiting_guests", "check_in": check_in.strftime("%Y-%m-%d"), "check_out": check_out.strftime("%Y-%m-%d")}
                with get_db_connection() as conn:
                    with conn.cursor() as c:
                        c.execute("UPDATE conversations SET booking_state = %s WHERE id = %s", (str(booking_state_dict), convo_id))
                        c.execute("INSERT INTO bookings (conversation_id, check_in, check_out) VALUES (%s, %s, %s)", 
                                (convo_id, check_in.strftime("%Y-%m-%d"), check_out.strftime("%Y-%m-%d")))
                        conn.commit()
                ai_reply = "Thanks for providing your dates! How many guests will be staying?"
                return (False, ai_reply)
            except (ValueError, KeyError) as e:
                ai_reply = "I couldn’t parse your dates. Please use the format 'March 10 to March 15'."
                return (False, ai_reply)
    elif booking_state_dict.get("status") == "awaiting_guests":
        if not guests:
            ai_reply = "Please tell me how many guests will be staying (e.g., '4 guests')."
            return (False, ai_reply)
        else:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    booking_state_dict["guests"] = guests
                    booking_state_dict["status"] = "awaiting_room_type"
                    c.execute("UPDATE conversations SET booking_state = %s WHERE id = %s", (str(booking_state_dict), convo_id))
                    c.execute("UPDATE bookings SET guests = %s WHERE conversation_id = %s", (guests, convo_id))
                    conn.commit()
            ai_reply = "Got it! Now, please choose a room type: Standard ($150/night), Deluxe ($250/night), or Suite ($400/night)."
            return (False, ai_reply)
    elif booking_state_dict.get("status") == "awaiting_room_type":
        room_type = message.lower()
        if "standard" in room_type:
            rate_per_night = 150
            room_type = "standard"
        elif "deluxe" in room_type:
            rate_per_night = 250
            room_type = "deluxe"
        elif "suite" in room_type:
            rate_per_night = 400
            room_type = "suite"
        else:
            ai_reply = "Please choose a valid room type: Standard ($150/night), Deluxe ($250/night), or Suite ($400/night)."
            return (False, ai_reply)

        with get_db_connection() as conn:
            with conn.cursor() as c:
                check_in = datetime.strptime(booking_state_dict["check_in"], "%Y-%m-%d")
                check_out = datetime.strptime(booking_state_dict["check_out"], "%Y-%m-%d")
                nights = (check_out - check_in).days
                total_cost = nights * rate_per_night * booking_state_dict["guests"]
                is_fully_booked = check_availability(check_in, check_out)
                if is_fully_booked:
                    ai_reply = f"Sorry, it looks like we’re fully booked for your dates ({booking_state_dict['check_in']} to {booking_state_dict['check_out']}). Please choose different dates."
                    c.execute("UPDATE conversations SET booking_state = %s WHERE id = %s", (None, convo_id))
                    c.execute("DELETE FROM bookings WHERE conversation_id = %s", (convo_id,))
                    conn.commit()
                    return (False, ai_reply)
                else:
                    booking_state_dict["room_type"] = room_type
                    booking_state_dict["total_cost"] = total_cost
                    booking_state_dict["status"] = "confirming"
                    c.execute("UPDATE conversations SET booking_state = %s WHERE id = %s", (str(booking_state_dict), convo_id))
                    c.execute("UPDATE bookings SET room_type = %s, total_cost = %s WHERE conversation_id = %s", 
                            (room_type, total_cost, convo_id))
                    conn.commit()
                    ai_reply = f"Great choice! Let me check availability for your dates. Assuming everything is available, your total will be ${total_cost}. Would you like to proceed with the booking?"
                    return (False, ai_reply)
    elif booking_state_dict.get("status") == "confirming":
        if "yes" in message.lower():
            ai_reply = "Perfect! To finalize your booking, I’ll need to collect payment details. Let me connect you with an agent to assist you securely."
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("UPDATE conversations SET handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = %s", (convo_id,))
                    conn.commit()
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id, "channel": channel})
            return (False, ai_reply)
        else:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("UPDATE conversations SET booking_state = %s WHERE id = %s", (None, convo_id))
                    c.execute("DELETE FROM bookings WHERE conversation_id = %s", (convo_id,))
                    conn.commit()
            ai_reply = "Okay, let me know if you’d like to start the booking process again or if you have other questions!"
            return (False, ai_reply)
    return (True, None)

@app.route("/check-auth", methods=["GET"])
def check_auth():
    if current_user.is_authenticated:
        return jsonify({"authenticated": True, "username": current_user.username}), 200
    return jsonify({"authenticated": False}), 200

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        logger.error("❌ Missing username or password in /login request")
        return jsonify({"message": "Missing username or password"}), 400
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id, username FROM agents WHERE username = %s AND password = %s", (username, password))
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
        filter_type = request.args.get("filter", "all")  # Options: "all", "unassigned", "mine"
        with get_db_connection() as conn:
            with conn.cursor() as c:
                if filter_type == "unassigned":
                    c.execute("SELECT id, channel, assigned_agent FROM conversations WHERE visible_in_conversations = 1 AND assigned_agent IS NULL ORDER BY last_updated DESC")
                elif filter_type == "mine" and current_user.is_authenticated:
                    c.execute("SELECT id, channel, assigned_agent FROM conversations WHERE visible_in_conversations = 1 AND assigned_agent = %s ORDER BY last_updated DESC", (current_user.username,))
                else:
                    c.execute("SELECT id, channel, assigned_agent FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC")
                conversations = [{"id": row[0], "channel": row[1], "assigned_agent": row[2]} for row in c.fetchall()]
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
            with conn.cursor() as c:
                c.execute("SELECT visible_in_conversations FROM conversations WHERE id = %s", (convo_id,))
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
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = %s ORDER BY timestamp ASC", (convo_id,))
                messages = [{"message": row[0], "sender": row[1], "timestamp": row[2].isoformat()} for row in c.fetchall()]
                c.execute("SELECT username FROM conversations WHERE id = %s", (convo_id,))
                username_result = c.fetchone()
                username = username_result[0] if username_result else "Unknown"
        logger.info(f"✅ Fetched {len(messages)} messages for convo ID {convo_id}")
        return jsonify({"messages": messages, "username": username})
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo ID {convo_id}: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

@app.route("/chat", methods=["POST"])
async def chat():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    user_message = data.get("message")
    if not convo_id or not user_message:
        logger.error("❌ Missing required fields in /chat request")
        return jsonify({"error": "Missing required fields"}), 400
    try:
        logger.info("✅ Entering /chat endpoint")
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT username, channel, assigned_agent, ai_enabled FROM conversations WHERE id = %s", (convo_id,))
                result = c.fetchone()
                if result:
                    username, channel, assigned_agent, ai_enabled = result
                else:
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
                    send_telegram_message(username, user_message)
                    logger.info("✅ Agent message sent to Telegram: " + user_message)
                except Exception as e:
                    logger.error(f"❌ Telegram error sending agent message: {str(e)}")
                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": channel})
            return jsonify({"status": "success"})

        if ai_enabled:
            logger.info("✅ AI is enabled, proceeding with AI response")
            if "HELP" in user_message.upper():
                ai_reply = "I’m sorry, I couldn’t process that. Let me connect you with an agent to assist you."
                logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + ai_reply)
            else:
                continue_with_ai, ai_reply = handle_booking_flow(user_message, convo_id, username, channel)
                if not continue_with_ai:
                    logger.info("✅ Booking flow handled, using booking flow reply")
                else:
                    ai_reply = await ai_respond(user_message, convo_id)

            log_message(convo_id, "AI", ai_reply, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": channel})
            if channel == "telegram":
                try:
                    send_telegram_message(username, ai_reply)
                    logger.info("✅ AI message sent to Telegram: " + ai_reply)
                except Exception as e:
                    logger.error(f"❌ Telegram error sending AI message: {str(e)}")
                    socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": channel})
            if "agent" in ai_reply.lower() or "sorry" in ai_reply.lower():
                with get_db_connection() as conn:
                    with conn.cursor() as c:
                        c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                        handoff_notified = c.fetchone()[0]
                    if not handoff_notified:
                        c.execute("UPDATE conversations SET handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = %s", (convo_id,))
                        conn.commit()
                        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
                        logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
            return jsonify({"reply": ai_reply})
        else:
            logger.info(f"❌ AI disabled for convo_id: {convo_id}")
            return jsonify({"status": "Message received, AI disabled"})
    except Exception as e:
        logger.error(f"❌ Error in /chat endpoint: {str(e)}")
        return jsonify({"error": "Failed to process chat message"}), 500

@app.route("/telegram", methods=["POST"])
async def telegram():
    update = request.get_json()
    logger.info(f"Received Telegram update: {update}")
    chat_id = str(update["message"]["chat"]["id"])
    message = update["message"]["text"]

    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent FROM conversations WHERE username = %s AND channel = 'telegram'", (chat_id,))
            result = c.fetchone()

            if not result:
                c.execute("INSERT INTO conversations (username, channel, ai_enabled, visible_in_conversations) VALUES (%s, 'telegram', 1, 0)", (chat_id,))
                conn.commit()
                convo_id = c.lastrowid
                ai_enabled = 1
                handoff_notified = 0
                assigned_agent = None
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

        log_message(convo_id, chat_id, message, "user")
        socketio.emit("new_message", {"convo_id": convo_id, "message": message, "sender": "user", "channel": "telegram"})

        if not ai_enabled:
            logger.info(f"❌ AI disabled for convo_id: {convo_id}, Skipping AI response")
            return jsonify({}), 200

        if "HELP" in message.upper():
            response = "I’m sorry, I couldn’t process that. Let me connect you with an agent to assist you."
            logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + response)
        else:
            continue_with_ai, response = handle_booking_flow(message, convo_id, chat_id, "telegram")
            if not continue_with_ai:
                logger.info("✅ Booking flow handled, using booking flow reply")
            else:
                response = await ai_respond(message, convo_id)

        log_message(convo_id, "AI", response, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "telegram"})

        if "agent" in response.lower() or "sorry" in response.lower() or "HELP" in message.upper():
            if not handoff_notified:
                with get_db_connection() as conn:
                    with conn.cursor() as c:
                        c.execute("UPDATE conversations SET handoff_notified = 1, ai_enabled = 0, visible_in_conversations = 1 WHERE id = %s", (convo_id,))
                        conn.commit()
                        c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = %s", (convo_id,))
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
async def instagram():
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
                    with conn.cursor() as c:
                        c.execute("SELECT id FROM conversations WHERE username = %s AND channel = 'instagram'", (sender_id,))
                        convo = c.fetchone()
                    if not convo:
                        with get_db_connection() as conn:
                            with conn.cursor() as c:
                                c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (%s, %s, %s, 1, 0)", 
                                        (sender_id, incoming_msg, "instagram"))
                                conn.commit()
                                convo_id = c.lastrowid
                    else:
                        convo_id = convo[0]
                log_message(convo_id, sender_id, incoming_msg, "user")
                response = await ai_respond(incoming_msg, convo_id)
                log_message(convo_id, "AI", response, "ai")
                requests.post(
                    f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                    json={"recipient": {"id": sender_id}, "message": {"text": response}}
                )
                socketio.emit("new_message", {"convo_id": convo_id, "message": response, "sender": "ai", "channel": "instagram"})
                if "agent" in response.lower() or "sorry" in response.lower():
                    with get_db_connection() as conn:
                        with conn.cursor() as c:
                            c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                            handoff_notified = c.fetchone()[0]
                        if not handoff_notified:
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = %s", (convo_id,))
                            conn.commit()
                            c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = %s", (convo_id,))
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
    logger.info("✅ Entering /whatsapp endpoint (placeholder)")
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
        with conn.cursor() as c:
            c.execute("SELECT id FROM conversations WHERE username = %s AND channel = 'telegram'", (to_number,))
            convo = c.fetchone()
    if not convo:
        logger.error("❌ Conversation not found in /send-welcome")
        return jsonify({"error": "Conversation not found"}), 404
    convo_id = convo[0]
    try:
        welcome_message = f"Welcome to our hotel, {user_name}! We're here to assist with your bookings. Reply 'BOOK' to start or 'HELP' for assistance."
        send_telegram_message(to_number, welcome_message)
        log_message(convo_id, "AI", f"Welcome to our hotel, {user_name}!", "ai")
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
            with conn.cursor() as c:
                c.execute("SELECT assigned_agent FROM conversations WHERE id = %s", (convo_id,))
                result = c.fetchone()
                if result and result[0] is not None:
                    logger.warning(f"❌ Conversation {convo_id} already assigned to {result[0]}")
                    return jsonify({"error": f"Conversation already assigned to {result[0]}"}), 409
                c.execute("UPDATE conversations SET assigned_agent = %s, handoff_notified = 0 WHERE id = %s", (current_user.username, convo_id))
                c.execute("SELECT username, channel FROM conversations WHERE id = %s", (convo_id,))
                username, channel = c.fetchone()
                conn.commit()
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
        socketio.emit("notify_agent", {"agent": current_user.username, "conversation_id": convo_id, "user": username, "channel": channel})
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
            with conn.cursor() as c:
                c.execute("SELECT username, channel, assigned_agent FROM conversations WHERE id = %s", (convo_id,))
                result = c.fetchone()
                if not result:
                    logger.error(f"❌ Conversation not found: {convo_id}")
                    return jsonify({"error": "Conversation not found"}), 404
                username, channel, assigned_agent = result

                if assigned_agent != current_user.username:
                    logger.error(f"❌ Agent {current_user.username} is not assigned to convo_id {convo_id}")
                    return jsonify({"error": "You are not assigned to this conversation"}), 403

                c.execute("UPDATE conversations SET assigned_agent = NULL, ai_enabled = 1, handoff_notified = 0, visible_in_conversations = 0 WHERE id = %s", (convo_id,))
                conn.commit()

                c.execute("SELECT ai_enabled FROM conversations WHERE id = %s", (convo_id,))
                updated_result = c.fetchone()
                if updated_result:
                    ai_enabled = updated_result[0]
                    logger.info(f"✅ After handback, ai_enabled for convo_id {convo_id} is {ai_enabled}")

        handback_message = "The agent has handed the conversation back to me. I’m here to assist you now! How can I help?"
        log_message(convo_id, "AI", handback_message, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": handback_message, "sender": "ai", "channel": channel})
        if channel == "telegram":
            try:
                send_telegram_message(username, handback_message)
                logger.info("✅ Handback message sent to Telegram: " + handback_message)
            except Exception as e:
                logger.error(f"❌ Telegram error sending handback message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send handback message to Telegram: {str(e)}", "channel": channel})

        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
        logger.info(f"✅ Chat {convo_id} handed back to AI by {current_user.username}")
        return jsonify({"message": "Chat handed back to AI successfully"})
    except Exception as e:
        logger.error(f"❌ Error in /handback-to-ai endpoint: {e}")
        return jsonify({"error": "Failed to hand back to AI"}), 500

@app.route("/test-openai", methods=["GET"])
async def test_openai():
    try:
        async with ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai.api_key}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 10}
            ) as response:
                result = await response.json()
                ai_reply = result["choices"][0]["message"]["content"].strip()
                logger.info(f"✅ OpenAI test successful: {ai_reply}")
                return jsonify({"status": "success", "reply": ai_reply}), 200
    except Exception as e:
        logger.error(f"❌ OpenAI test failed: {str(e)}")
        return jsonify({"status": "failed", "error": str(e)}), 500

@app.route("/clear-database", methods=["POST"])
def clear_database():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
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
else:
    # For gunicorn deployment on Render
    application = app
