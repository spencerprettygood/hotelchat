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
    - Always greet new users with: "Thank you for contacting us." (Note: This is handled in the code for new conversations.)
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
            c.execute("SELECT id, username, latest_message, assigned_agent, channel FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC")
            conversations = [{"id": row[0], "username": row[1], "latest_message": row[2], "assigned_agent": row[3], "channel": row[4]} for row in c.fetchall()]
        logger.info(f"✅ Fetched conversations: {len(conversations)} conversations")
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
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC", (convo_id,))
            messages = [{"message": row[0], "sender": row[1], "timestamp": row[2]} for row in c.fetchall()]
        logger.info(f"✅ Fetched {len(messages)} messages for convo ID {convo_id}")
        return jsonify(messages)
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo ID {convo_id}: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

def log_message(convo_id, user, message, sender):
    try:
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

@app.route("/check-auth", methods=["GET"])
def check_auth():
    return jsonify({"is_authenticated": current_user.is_authenticated})

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
            try:
                logger.info(f"Processing message with AI for convo_id {convo_id}: {user_message}")
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
                conversation_history.append({"role": "user", "content": user_message})
                logger.info(f"✅ Sending conversation history to OpenAI: {conversation_history}")

                response = openai.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=conversation_history,
                    max_tokens=150
                )
                ai_reply = response.choices[0].message.content.strip()
                model_used = response.model
                logger.info(f"✅ AI reply: {ai_reply}, Model: {model_used}")
            except Exception as e:
                ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
                logger.error(f"❌ OpenAI error: {str(e)}")
                logger.error(f"❌ OpenAI error type: {type(e).__name__}")

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
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
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
    logger.info("✅ Entering /telegram endpoint")
    update = request.get_json()
    logger.info(f"Received update: {update}")
    if "message" not in update:
        logger.info("✅ Not a message update, returning OK")
        return "OK", 200
    
    try:
        chat_id = update["message"]["chat"]["id"]
        incoming_msg = update["message"]["text"]
        from_number = str(chat_id)
        logger.info(f"✅ Received Telegram message: {incoming_msg}, from: {from_number}")
    except KeyError as e:
        logger.error(f"❌ Invalid Telegram update format: {str(e)}")
        return "OK", 200

    try:
        logger.info("✅ Connecting to database")
        with get_db_connection() as conn:
            c = conn.cursor()
            logger.info(f"✅ Querying conversations for username: {from_number}")
            c.execute("SELECT id, last_updated, handoff_notified, ai_enabled, booking_state FROM conversations WHERE username = ?", (from_number,))
            convo = c.fetchone()

        is_new_conversation = not convo
        logger.info(f"✅ Is new conversation: {is_new_conversation}")
        if is_new_conversation:
            logger.info("✅ Creating new conversation")
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 0)", 
                          (from_number, incoming_msg, "telegram"))
                convo_id = c.lastrowid
                conn.commit()
                c.execute("SELECT visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
                insert_result = c.fetchone()
            logger.info(f"✅ After creating convo_id {convo_id}: visible_in_conversations={insert_result[0]}")
            handoff_notified = 0
            ai_enabled = 1
            booking_state = None
            logger.info(f"✅ Created new conversation for {from_number}: convo_id {convo_id}")
        else:
            convo_id, last_updated, handoff_notified, ai_enabled, booking_state = convo
            logger.info(f"✅ Existing conversation found: convo_id={convo_id}, last_updated={last_updated}, handoff_notified={handoff_notified}, ai_enabled={ai_enabled}, booking_state={booking_state}")
            if (datetime.now() - datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")) > timedelta(hours=24):
                logger.info("✅ Last message was >24 hours ago, resetting ai_enabled and handoff_notified")
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE conversations SET ai_enabled = 1, handoff_notified = 0 WHERE id = ?", (convo_id,))
                    conn.commit()
                ai_enabled = 1
        conn.close()

        logger.info("✅ Logging client message")
        log_message(convo_id, from_number, incoming_msg, "user")
        logger.info("✅ Emitting new_message event for client message")
        socketio.emit("new_message", {"convo_id": convo_id, "message": incoming_msg, "sender": "user", "channel": "telegram", "user": from_number})

        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if not ai_enabled:
            logger.info("❌ AI disabled for convo_id: " + str(convo_id) + ", Skipping AI response")
            return "OK", 200

        if incoming_msg.lower() == "/start":
            logger.info("✅ Handling /start command")
            welcome_message = "Thank you for contacting us. How can I assist you today?"
            try:
                send_telegram_message(from_number, welcome_message)
            except Exception as e:
                logger.error(f"❌ Telegram error sending welcome message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Telegram: {str(e)}", "channel": "telegram"})
            log_message(convo_id, "AI", welcome_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "telegram"})
            return "OK", 200

        if is_new_conversation:
            logger.info("✅ Sending welcome message for new conversation")
            welcome_message = "Thank you for contacting us."
            try:
                send_telegram_message(from_number, welcome_message)
            except Exception as e:
                logger.error(f"❌ Telegram error sending welcome message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Telegram: {str(e)}", "channel": "telegram"})
            log_message(convo_id, "AI", welcome_message, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "telegram"})

        logger.info("✅ Processing message with AI")
        ai_reply = None
        retry_attempts = 2
        for attempt in range(retry_attempts):
            try:
                logger.info(f"Processing message with AI for convo_id {convo_id} with gpt-4o-mini (Attempt {attempt + 1}): {incoming_msg}")
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("SELECT user, message, sender FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 10", (convo_id,))
                    messages = c.fetchall()
                conversation_history = [
                    {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Maintain conversation context and provide relevant follow-up responses. Extract dates (e.g., 'March 10 to March 15') and numbers (e.g., '4 guests') from user messages. Escalate to a human if the query is complex or requires personal assistance."}
                ]
                for msg in messages:
                    user, message_text, sender = msg
                    role = "user" if sender == "user" else "assistant"
                    conversation_history.append({"role": role, "content": message_text})
                conversation_history.append({"role": "user", "content": incoming_msg})
                logger.info(f"✅ Sending conversation history to OpenAI: {conversation_history}")

                # Parse dates and numbers using regex as a fallback
                date_match = re.search(r'(\w+\s+\d+\s+to\s+\w+\s+\d+)', incoming_msg, re.IGNORECASE)
                number_match = re.search(r'(\d+)\s*(guests)?', incoming_msg, re.IGNORECASE)
                dates = date_match.group(0) if date_match else None
                guests = int(number_match.group(1)) if number_match else None

                # Evaluate booking_state if it exists
                booking_state_dict = eval(booking_state) if booking_state else {}

                # Booking flow
                if "book" in incoming_msg.lower() and not booking_state_dict.get("status"):
                    if not dates:
                        ai_reply = "I’d love to help you book! Please provide your preferred dates (e.g., 'March 10 to March 15')."
                    else:
                        try:
                            date_parts = re.split(r'\s+to\s+', dates.lower())
                            months = {'march': 3, 'april': 4, 'may': 5}  # Add more months as needed
                            check_in_day = int(date_parts[1])
                            check_out_day = int(date_parts[3])
                            check_in_month = months[date_parts[0].split()[0]]
                            check_out_month = months[date_parts[2].split()[0]]
                            check_in = datetime(2025, check_in_month, check_in_day)
                            check_out = datetime(2025, check_out_month, check_out_day)
                            booking_state_dict = {"status": "awaiting_guests", "check_in": check_in.strftime("%Y-%m-%d"), "check_out": check_out.strftime("%Y-%m-%d")}
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (str(booking_state_dict), convo_id))
                                c.execute("INSERT INTO bookings (conversation_id, check_in, check_out) VALUES (?, ?, ?)", (convo_id, check_in.strftime("%Y-%m-%d"), check_out.strftime("%Y-%m-%d")))
                                conn.commit()
                            ai_reply = "Thanks for providing your dates! How many guests will be staying?"
                        except (ValueError, KeyError) as e:
                            ai_reply = "I couldn’t parse your dates. Please use the format 'March 10 to March 15'."
                elif booking_state_dict.get("status") == "awaiting_guests":
                    if not guests:
                        ai_reply = "Please tell me how many guests will be staying (e.g., '4 guests')."
                    else:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            booking_state_dict["guests"] = guests
                            booking_state_dict["status"] = "awaiting_room_type"
                            c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (str(booking_state_dict), convo_id))
                            c.execute("UPDATE bookings SET guests = ? WHERE conversation_id = ?", (guests, convo_id))
                            conn.commit()
                        ai_reply = "Got it! Now, please choose a room type: Standard ($150/night), Deluxe ($250/night), or Suite ($400/night)."
                elif booking_state_dict.get("status") == "awaiting_room_type":
                    room_type = incoming_msg.lower()
                    if "standard" in room_type:
                        rate_per_night = 150
                    elif "deluxe" in room_type:
                        rate_per_night = 250
                    elif "suite" in room_type:
                        rate_per_night = 400
                    else:
                        ai_reply = "Please choose a valid room type: Standard ($150/night), Deluxe ($250/night), or Suite ($400/night)."
                        log_message(convo_id, "AI", ai_reply, "ai")
                        send_telegram_message(from_number, ai_reply)
                        return "OK", 200

                    with get_db_connection() as conn:
                        c = conn.cursor()
                        check_in = datetime.strptime(booking_state_dict["check_in"], "%Y-%m-%d")
                        check_out = datetime.strptime(booking_state_dict["check_out"], "%Y-%m-%d")
                        nights = (check_out - check_in).days
                        total_cost = nights * rate_per_night * booking_state_dict["guests"]  # Adjust for per-room pricing if needed
                        is_fully_booked = check_availability(check_in, check_out)
                        if is_fully_booked:
                            ai_reply = f"Sorry, it looks like we’re fully booked for your dates ({booking_state_dict['check_in']} to {booking_state_dict['check_out']}). Please choose different dates."
                        else:
                            booking_state_dict["room_type"] = room_type
                            booking_state_dict["total_cost"] = total_cost
                            booking_state_dict["status"] = "confirming"
                            c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (str(booking_state_dict), convo_id))
                            c.execute("UPDATE bookings SET room_type = ?, total_cost = ? WHERE conversation_id = ?", (room_type, total_cost, convo_id))
                            conn.commit()
                        ai_reply = f"Great choice! Let me check availability for your dates. Assuming everything is available, your total will be ${total_cost}. Would you like to proceed with the booking?"
                elif booking_state_dict.get("status") == "confirming":
                    if "yes" in incoming_msg.lower():
                        ai_reply = "Perfect! To finalize your booking, I’ll need to get a human to assist you with the payment and confirmation details. Please hold on while I connect you."
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                            conn.commit()
                    else:
                        with get_db_connection() as conn:
                            c = conn.cursor()
                            c.execute("UPDATE conversations SET booking_state = ? WHERE id = ?", (None, convo_id))
                            c.execute("DELETE FROM bookings WHERE conversation_id = ?", (convo_id,))
                            conn.commit()
                        ai_reply = "Okay, let me know if you’d like to start the booking process again or if you have other questions!"

                # Prevent fallback to OpenAI if ai_reply is set
                if ai_reply:
                    log_message(convo_id, "AI", ai_reply, "ai")
                    send_telegram_message(from_number, ai_reply)
                    socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "telegram"})
                    if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
                        try:
                            with get_db_connection() as conn:
                                c = conn.cursor()
                                c.execute("SELECT handoff_notified, visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
                                result = c.fetchone()
                                handoff_notified, visible_in_conversations = result if result else (0, 0)
                            if not handoff_notified:
                                with get_db_connection() as conn:
                                    c = conn.cursor()
                                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                                    conn.commit()
                                time.sleep(3.0)
                                socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": from_number, "channel": "telegram"})
                        except Exception as e:
                            logger.error(f"❌ Error during handoff for convo_id {convo_id}: {e}")
                    return "OK", 200

                break
            except Exception as e:
                logger.error(f"❌ OpenAI error (Attempt {attempt + 1}): {str(e)}")
                logger.error(f"❌ OpenAI error type: {type(e).__name__}")
                if attempt == retry_attempts - 1:
                    ai_reply = "I’m sorry, I’m having trouble processing your request right now. Let me get a human to assist you."
                    logger.info("✅ Set default AI reply due to repeated errors: " + ai_reply)
                else:
                    time.sleep(1)
                    continue

        logger.info("✅ Checking for HELP keyword")
        if "HELP" in incoming_msg.upper():
            ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
            logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + ai_reply)

        logger.info("✅ Logging AI response")
        log_message(convo_id, "AI", ai_reply, "ai")
        logger.info("✅ Emitting new_message event for AI response")
        socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "telegram"})

        logger.info("✅ Sending AI response to Telegram")
        try:
            send_telegram_message(from_number, ai_reply)
            logger.info("✅ AI message sent to Telegram: " + ai_reply)
        except Exception as e:
            logger.error(f"❌ Telegram error sending AI message: {str(e)}")
            socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": "telegram"})

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
                        c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (convo_id,))
                        conn.commit()
                    time.sleep(3.0)
                    with get_db_connection() as conn:
                        c = conn.cursor()
                        c.execute("SELECT handoff_notified, visible_in_conversations, assigned_agent FROM conversations WHERE id = ?", (convo_id,))
                        updated_result = c.fetchone()
                    logger.info(f"✅ After handoff update for convo_id {convo_id}: handoff_notified={updated_result[0]}, visible_in_conversations={updated_result[1]}, assigned_agent={updated_result[2]}")
                    socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": from_number, "channel": "telegram"})
                    logger.info(f"✅ Refresh triggered for convo_id {convo_id}, chat now visible in Conversations (unassigned)")
            except Exception as e:
                logger.error(f"❌ Error during handoff for convo_id {convo_id}: {e}")
        
        logger.info("✅ Returning OK for Telegram")
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Error in /telegram endpoint: {str(e)}")
        logger.error(f"❌ Error type: {type(e).__name__}")
        return "OK", 200

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
                    conn.close()
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
            c.execute("SELECT username, channel FROM conversations WHERE id = ?", (convo_id,))
            username, channel = c.fetchone()
            conn.commit()
        socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": username, "channel": channel})
        logger.info(f"✅ Chat {convo_id} assigned to {current_user.username}")
        return jsonify({"message": f"Chat assigned to {current_user.username}"})
    except Exception as e:
        logger.error(f"❌ Error in /handoff endpoint: {e}")
        return jsonify({"error": "Failed to assign chat"}), 500

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
