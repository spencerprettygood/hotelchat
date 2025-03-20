import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify, session
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

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecretkey")
CORS(app)
# Configure Socket.IO with WebSocket support and ping/pong settings
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

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

# Import blueprints
from blueprints.dashboard import dashboard_bp
from blueprints.live_messages import live_messages_bp

# Register blueprints
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
app.register_blueprint(live_messages_bp, url_prefix='/live-messages')

# Database connection
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
        return conn
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        raise Exception(f"Failed to connect to database: {e}")

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        # Create conversations table with all columns
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            assigned_agent TEXT,
            ai_enabled INTEGER DEFAULT 1,
            booking_intent TEXT,
            handoff_notified INTEGER DEFAULT 0,
            visible_in_conversations INTEGER DEFAULT 1,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            convo_id INTEGER,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            sender TEXT NOT NULL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (convo_id) REFERENCES conversations (id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS agents (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )''')
        # Initialize settings
        c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                  ('ai_enabled', '1'))
        # Initialize a default agent (admin/password) if not exists
        c.execute("INSERT INTO agents (username, password) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
                  ('admin', 'password'))
        # Add test conversations
        c.execute("SELECT COUNT(*) FROM conversations WHERE channel = 'whatsapp'")
        if c.fetchone()['count'] == 0:
            logger.info("ℹ️ Inserting test conversations")
            c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations, last_updated) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                      ('TestUser1', '123456789', 'whatsapp', 1, 1, '2025-03-20 00:00:00'))
            convo_id1 = c.fetchone()['id']
            c.execute("INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                      (convo_id1, 'TestUser1', 'Hello, I need help!', 'user', '2025-03-20 00:00:00'))
            c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations, last_updated) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                      ('TestUser2', '987654321', 'whatsapp', 1, 1, '2025-03-20 00:00:01'))
            convo_id2 = c.fetchone()['id']
            c.execute("INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                      (convo_id2, 'TestUser2', 'Can I book a room?', 'user', '2025-03-20 00:00:01'))
        else:
            logger.info("ℹ️ Test conversations already exist, skipping insertion")
        conn.commit()
        logger.info("✅ Database initialized")

# Initialize database
init_db()

def log_message(conn, convo_id, user, message, sender):
    with conn:
        c = conn.cursor()
        try:
            c.execute("INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                      (convo_id, user, message, sender, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            c.execute("UPDATE conversations SET last_updated = %s WHERE id = %s",
                      (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), convo_id))
            if sender == "agent":
                c.execute("UPDATE conversations SET ai_enabled = 0 WHERE id = %s", (convo_id,))
                logger.info(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
            conn.commit()
            logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
        except Exception as e:
            logger.error(f"❌ Database error in log_message: {str(e)}")
            raise

class Agent(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(agent_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, username FROM agents WHERE id = %s", (agent_id,))
        agent = c.fetchone()
        if agent:
            return Agent(agent['id'], agent['username'])
    return None

# Authentication Endpoints
@app.route("/login", methods=["POST"])
def login():
    try:
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
    except Exception as e:
        logger.error(f"❌ Error in /login: {e}")
        return jsonify({"error": "Failed to login"}), 500

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    try:
        username = current_user.username
        logout_user()
        logger.info(f"✅ Logout successful for agent: {username}")
        return jsonify({"message": "Logged out successfully"})
    except Exception as e:
        logger.error(f"❌ Error in /logout: {e}")
        return jsonify({"error": "Failed to logout"}), 500

@app.route("/check-auth", methods=["GET"])
def check_auth():
    try:
        return jsonify({
            "is_authenticated": current_user.is_authenticated,
            "agent": current_user.username if current_user.is_authenticated else None
        })
    except Exception as e:
        logger.error(f"❌ Error in /check-auth: {e}")
        return jsonify({"error": "Failed to check authentication"}), 500

# Messaging Helper Functions
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
            c.execute("SELECT message, sender, timestamp FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 10", (convo_id,))
            messages = c.fetchall()
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
        c.execute("SELECT message FROM messages WHERE convo_id = %s ORDER BY timestamp DESC LIMIT 5", (convo_id,))
        messages = c.fetchall()
        for msg in messages:
            if any(keyword in msg['message'].lower() for keyword in spanish_keywords):
                return "es"
    
    return "en"

# Chat and Messaging Endpoints
@app.route("/chat", methods=["POST"])
@login_required
def chat():
    try:
        data = request.get_json()
        convo_id = data.get("convo_id")
        user_message = data.get("message")
        channel = data.get("channel", "whatsapp")
        if not convo_id or not user_message:
            logger.error("❌ Missing required fields in /chat request")
            return jsonify({"error": "Missing required fields"}), 400

        # Validate that the channel is WhatsApp
        if channel != "whatsapp":
            logger.error(f"❌ Invalid channel: {channel}. This app only supports WhatsApp.")
            return jsonify({"error": "This app only supports WhatsApp"}), 400

        # Fetch conversation details
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel, assigned_agent, ai_enabled, booking_intent FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found: {convo_id}")
                return jsonify({"error": "Conversation not found"}), 404
            username, chat_id, channel, assigned_agent, ai_enabled, booking_intent = result

        sender = "agent" if current_user.is_authenticated else "user"
        log_message(conn, convo_id, username, user_message, sender)

        language = detect_language(user_message, convo_id)

        if sender == "agent":
            # Emit the agent's message to the UI
            socketio.emit("new_message", {
                "convo_id": convo_id,
                "message": user_message,
                "sender": "agent",
                "channel": channel,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            socketio.emit("live_message", {
                "convo_id": convo_id,
                "message": user_message,
                "sender": "agent",
                "username": username,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            # Send the message to WhatsApp
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

        # Check global AI setting
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
            global_ai_result = c.fetchone()
            global_ai_enabled = int(global_ai_result['value']) if global_ai_result else 1

        if not global_ai_enabled or not ai_enabled:
            logger.info(f"AI response skipped for convo_id {convo_id}: global_ai_enabled={global_ai_enabled}, ai_enabled={ai_enabled}")
            return jsonify({"status": "Message received, AI disabled"})

        # Handle booking intent confirmation (e.g., user says "yes" or "proceed")
        if booking_intent and ("yes" in user_message.lower() or "proceed" in user_message.lower() or "sí" in user_message.lower()):
            response = f"Great! An agent will assist you with booking a room for {booking_intent}. Please wait." if language == "en" else \
                      f"¡Excelente! Un agente te ayudará con la reserva de una habitación para {booking_intent}. Por favor, espera."
            log_message(conn, convo_id, "AI", response, "ai")
            socketio.emit("new_message", {
                "convo_id": convo_id,
                "message": response,
                "sender": "ai",
                "channel": channel,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            socketio.emit("live_message", {
                "convo_id": convo_id,
                "message": response,
                "sender": "ai",
                "username": username,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            # Send the handoff message to WhatsApp
            if not chat_id:
                logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to WhatsApp: No chat_id found", "channel": channel})
            else:
                if not send_whatsapp_message(chat_id, response):
                    logger.error(f"❌ Failed to send handoff message to WhatsApp for chat_id {chat_id}")
                    socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send handoff message to WhatsApp", "channel": channel})
            # Update conversation for handoff
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1, last_updated = %s WHERE id = %s",
                          (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), convo_id))
                conn.commit()
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
            return jsonify({"reply": response})

        # Handle booking intent detection (e.g., user says "book" or "reservar")
        if "book" in user_message.lower() or "reservar" in user_message.lower():
            ai_reply = "I’ll connect you with a team member to assist with your booking." if language == "en" else \
                      "Te conectaré con un miembro del equipo para que te ayude con tu reserva."
            log_message(conn, convo_id, "AI", ai_reply, "ai")
            socketio.emit("new_message", {
                "convo_id": convo_id,
                "message": ai_reply,
                "sender": "ai",
                "channel": channel,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            socketio.emit("live_message", {
                "convo_id": convo_id,
                "message": ai_reply,
                "sender": "ai",
                "username": username,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            # Send the handoff message to WhatsApp
            if not chat_id:
                logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to WhatsApp: No chat_id found", "channel": channel})
            else:
                if not send_whatsapp_message(chat_id, ai_reply):
                    logger.error(f"❌ Failed to send booking handoff message to WhatsApp for chat_id {chat_id}")
                    socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send booking handoff message to WhatsApp", "channel": channel})
            # Update conversation for handoff
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1, last_updated = %s WHERE id = %s",
                          (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), convo_id))
                conn.commit()
            socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})
            return jsonify({"reply": ai_reply})

        # Handle help requests (e.g., user says "HELP" or "AYUDA")
        if "HELP" in user_message.upper() or "AYUDA" in user_message.upper():
            ai_reply = "I’m sorry, I couldn’t process that. I’ll connect you with a team member to assist you." if language == "en" else \
                      "Lo siento, no pude procesar eso. Te conectaré con un miembro del equipo para que te ayude."
        else:
            # Default AI response
            ai_reply = ai_respond(user_message, convo_id)

        # Log and emit the AI response
        log_message(conn, convo_id, "AI", ai_reply, "ai")
        socketio.emit("new_message", {
            "convo_id": convo_id,
            "message": ai_reply,
            "sender": "ai",
            "channel": channel,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        socketio.emit("live_message", {
            "convo_id": convo_id,
            "message": ai_reply,
            "sender": "ai",
            "username": username,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        # Send the AI response to WhatsApp
        if not chat_id:
            logger.error(f"❌ No chat_id found for convo_id {convo_id}")
            socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to WhatsApp: No chat_id found", "channel": channel})
        else:
            if not send_whatsapp_message(chat_id, ai_reply):
                logger.error(f"❌ Failed to send AI response to WhatsApp for chat_id {chat_id}")
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send AI response to WhatsApp", "channel": channel})
        
        # Check if the AI response indicates a handoff (e.g., "sorry" or "lo siento")
        if "sorry" in ai_reply.lower() or "lo siento" in ai_reply.lower():
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT handoff_notified FROM conversations WHERE id = %s", (convo_id,))
                handoff_notified = c.fetchone()['handoff_notified']
                if not handoff_notified:
                    c.execute("UPDATE conversations SET handoff_notified = 1, visible_in_conversations = 1, last_updated = %s WHERE id = %s",
                              (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), convo_id))
                    conn.commit()
                    socketio.emit("refresh_conversations", {"conversation_id": convo_id, "user": chat_id or username, "channel": channel})

        return jsonify({"reply": ai_reply})
    except Exception as e:
        logger.error(f"❌ Error in /chat endpoint: {str(e)}")
        return jsonify({"error": "Failed to process chat message"}), 500

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    conn = None
    try:
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
        if not from_number or not message_body:
            logger.info("ℹ️ WhatsApp message received but missing From or Body")
            return jsonify({}), 200

        prefixed_from = f"whatsapp_{from_number.replace('whatsapp:', '')}"
        conn = get_db_connection()
        c = conn.cursor()
        # Check if conversation exists
        c.execute("SELECT id, ai_enabled, handoff_notified, assigned_agent, booking_intent FROM conversations WHERE username = %s AND channel = 'whatsapp'", 
                  (prefixed_from,))
        result = c.fetchone()

        if not result:
            logger.info(f"ℹ️ Creating new conversation for {prefixed_from}")
            c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations, last_updated) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                      (prefixed_from, from_number, 'whatsapp', 1, 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            convo_id = c.fetchone()['id']
            ai_enabled = 1
            handoff_notified = 0
            assigned_agent = None
            booking_intent = None
            conn.commit()  # Commit the new conversation
            logger.info(f"✅ Created new conversation with id {convo_id} for {prefixed_from}")

            # Verify the conversation exists
            c.execute("SELECT 1 FROM conversations WHERE id = %s", (convo_id,))
            if not c.fetchone():
                logger.error(f"❌ Conversation with id {convo_id} not found after insert")
                raise ValueError(f"Failed to create conversation with id {convo_id}")

            # Send welcome message
            language = detect_language(message_body, convo_id)
            welcome_message = "Gracias por contactarnos." if language == "es" else "Thank you for contacting us."
            log_message(conn, convo_id, "AI", welcome_message, "ai")
            socketio.emit("new_message", {
                "convo_id": convo_id,
                "message": welcome_message,
                "sender": "ai",
                "channel": "whatsapp",
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            socketio.emit("live_message", {
                "convo_id": convo_id,
                "message": welcome_message,
                "sender": "ai",
                "username": prefixed_from,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

            if not send_whatsapp_message(from_number, welcome_message):
                socketio.emit("error", {"convo_id": convo_id, "message": "Failed to send welcome message to WhatsApp", "channel": "whatsapp"})
        else:
            convo_id, ai_enabled, handoff_notified, assigned_agent, booking_intent = result
            logger.info(f"ℹ️ Found existing conversation with id {convo_id} for {prefixed_from}")

        # Check global AI setting
        c.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
        global_ai_result = c.fetchone()
        global_ai_enabled = int(global_ai_result['value']) if global_ai_result else 1

        # Log the user's message
        logger.info(f"ℹ️ Logging user message for convo_id {convo_id}")
        log_message(conn, convo_id, from_number, message_body, "user")
        socketio.emit("new_message", {
            "convo_id": convo_id,
            "message": message_body,
            "sender": "user",
            "channel": "whatsapp",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        socketio.emit("live_message", {
            "convo_id": convo_id,
            "message": message_body,
            "sender": "user",
            "username": prefixed_from,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

        if not global_ai_enabled or not ai_enabled:
            logger.info(f"AI response skipped for convo_id {convo_id}: global_ai_enabled={global_ai_enabled}, ai_enabled={ai_enabled}")
            conn.commit()
            return jsonify({}), 200

        language = detect_language(message_body, convo_id)
        if booking_intent and ("yes" in message_body.lower() or "proceed" in message_body.lower() or "sí" in message_body.lower()):
            handoff_message = f"Great! An agent will assist you with booking for {booking_intent}. Please wait." if language == "en" else \
                            
