import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify, session, render_template, redirect, url_for  # Add redirect, url_for
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

# Database connection
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
        return conn
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        raise Exception(f"Failed to connect to database: {e}")

# ... (previous imports and app setup)

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        # Create agents table
        c.execute('''CREATE TABLE IF NOT EXISTS agents (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )''')
        c.execute("INSERT INTO agents (username, password) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
                  ('admin', 'password'))

        # Create conversations table (for conversation metadata)
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            conversation_id TEXT,           -- To group messages by conversation
            username TEXT NOT NULL,        -- Display name for the user
            phone_number TEXT,             -- Phone number of the user
            chat_id TEXT,                  -- Optional chat ID (e.g., for WhatsApp)
            channel TEXT NOT NULL,         -- 'whatsapp' or 'sms'
            status TEXT DEFAULT 'pending',
            agent_id INTEGER REFERENCES agents(id),
            last_message_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ai_enabled INTEGER DEFAULT 1,  -- Whether AI is enabled for this conversation
            handoff_notified INTEGER DEFAULT 0,  -- Whether a handoff to an agent has been notified
            assigned_agent INTEGER,        -- References agents(id), can be NULL
            booking_intent TEXT,           -- Stores booking intent (e.g., dates)
            visible_in_conversations INTEGER DEFAULT 0  -- Whether the conversation is visible in the UI
        )''')

        # Migrate the conversations table schema
        # Add missing columns
        columns_to_add = {
            'conversation_id': 'TEXT',
            'phone_number': 'TEXT',
            'status': 'TEXT DEFAULT \'pending\'',
            'agent_id': 'INTEGER REFERENCES agents(id)',
            'last_message_timestamp': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }
        for column, column_type in columns_to_add.items():
            c.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'conversations' AND column_name = %s
            """, (column,))
            if not c.fetchone():
                logger.info(f"ℹ️ Adding missing column {column} to conversations table")
                c.execute(f"ALTER TABLE conversations ADD COLUMN {column} {column_type}")

        # Update last_updated to last_message_timestamp if last_updated exists
        c.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'conversations' AND column_name = 'last_updated'
        """)
        if c.fetchone():
            logger.info("ℹ️ Migrating last_updated to last_message_timestamp")
            c.execute("""
                UPDATE conversations 
                SET last_message_timestamp = TO_TIMESTAMP(last_updated, 'YYYY-MM-DD HH24:MI:SS') 
                WHERE last_updated IS NOT NULL AND last_message_timestamp IS NULL
            """)
            c.execute("ALTER TABLE conversations DROP COLUMN last_updated")

        # Update assigned_agent type from TEXT to INTEGER
        c.execute("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name = 'conversations' AND column_name = 'assigned_agent'
        """)
        result = c.fetchone()
        if result and result['data_type'] == 'text':
            logger.info("ℹ️ Converting assigned_agent from TEXT to INTEGER")
            c.execute("ALTER TABLE conversations ADD COLUMN assigned_agent_temp INTEGER")
            c.execute("UPDATE conversations SET assigned_agent_temp = CAST(assigned_agent AS INTEGER) WHERE assigned_agent IS NOT NULL")
            c.execute("ALTER TABLE conversations DROP COLUMN assigned_agent")
            c.execute("ALTER TABLE conversations RENAME COLUMN assigned_agent_temp TO assigned_agent")

        # Populate conversation_id and phone_number for existing rows
        c.execute("UPDATE conversations SET conversation_id = chat_id WHERE conversation_id IS NULL")
        c.execute("UPDATE conversations SET phone_number = chat_id WHERE phone_number IS NULL")

        # Add NOT NULL constraints
        c.execute("ALTER TABLE conversations ALTER COLUMN conversation_id SET NOT NULL")
        c.execute("ALTER TABLE conversations ALTER COLUMN phone_number SET NOT NULL")

        # Update visible_in_conversations default to 0 for existing rows
        c.execute("UPDATE conversations SET visible_in_conversations = 0 WHERE visible_in_conversations IS NULL")

        # Create messages table (for individual messages)
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            convo_id TEXT NOT NULL,        -- References conversation_id in conversations table
            message TEXT NOT NULL,
            sender TEXT NOT NULL,          -- 'user', 'agent', or 'ai'
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            channel TEXT NOT NULL          -- 'whatsapp' or 'sms'
        )''')

        # Drop the existing foreign key constraint on messages.convo_id
        c.execute("""
            SELECT constraint_name 
            FROM information_schema.table_constraints 
            WHERE table_name = 'messages' AND constraint_type = 'FOREIGN KEY' AND constraint_name = 'messages_convo_id_fkey'
        """)
        if c.fetchone():
            c.execute("ALTER TABLE messages DROP CONSTRAINT messages_convo_id_fkey")

        # Change the type of convo_id from INTEGER to TEXT if necessary
        c.execute("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name = 'messages' AND column_name = 'convo_id'
        """)
        result = c.fetchone()
        if result and result['data_type'] == 'integer':
            logger.info("ℹ️ Converting convo_id from INTEGER to TEXT in messages table")
            c.execute("ALTER TABLE messages ADD COLUMN convo_id_temp TEXT")
            c.execute("UPDATE messages SET convo_id_temp = CAST(convo_id AS TEXT) WHERE convo_id IS NOT NULL")
            c.execute("ALTER TABLE messages DROP COLUMN convo_id")
            c.execute("ALTER TABLE messages RENAME COLUMN convo_id_temp TO convo_id")
            c.execute("ALTER TABLE messages ALTER COLUMN convo_id SET NOT NULL")

        # Update convo_id in messages to match conversation_id
        c.execute("""
            UPDATE messages m
            SET convo_id = (
                SELECT c.conversation_id
                FROM conversations c
                WHERE CAST(c.id AS TEXT) = m.convo_id
            )
            WHERE EXISTS (
                SELECT 1
                FROM conversations c
                WHERE CAST(c.id AS TEXT) = m.convo_id
            )
        """)

        # Create settings table
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')
        c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                  ('ai_enabled', '1'))

        conn.commit()

# Initialize database
init_db()

def log_message(conn, convo_id, message, sender):
    with conn:
        c = conn.cursor()
        try:
            # Insert the message into the messages table
            c.execute("INSERT INTO messages (convo_id, message, sender, timestamp, channel) VALUES (%s, %s, %s, %s, %s)",
                      (convo_id, message, sender, datetime.now(), 'whatsapp'))
            # Update the last_message_timestamp in the conversations table
            c.execute("UPDATE conversations SET last_message_timestamp = %s WHERE conversation_id = %s",
                      (datetime.now(), convo_id))
            if sender == "agent":
                c.execute("UPDATE conversations SET ai_enabled = 0 WHERE conversation_id = %s", (convo_id,))
                logger.info(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
            conn.commit()
            logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
        except Exception as e:
            logger.error(f"❌ Database error in log_message: {str(e)}")
            raise

class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as conn:
        c = conn.cursor(cursor_factory=DictCursor)
        c.execute("SELECT id, username FROM agents WHERE id = %s", (user_id,))
        user = c.fetchone()
        if user:
            return User(user['id'], user['username'])
        return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=DictCursor)
            c.execute("SELECT id, username, password FROM agents WHERE username = %s", (username,))
            user = c.fetchone()
            if user and check_password_hash(user['password'], password):
                user_obj = User(user['id'], user['username'])
                login_user(user_obj)
                return redirect(url_for('dashboard.dashboard'))
            return jsonify({"error": "Invalid credentials"}), 401
    return render_template('login.html')
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
        logger.info("ℹ️ Received WhatsApp message")
        validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))
        if not validator.validate(
            request.url,
            request.form,
            request.headers.get("X-Twilio-Signature", "")
        ):
            logger.error("❌ Invalid Twilio signature")
            return jsonify({"error": "Invalid signature"}), 403

        message = request.values.get("Body", "").strip()
        phone_number = request.values.get("From")
        username = request.values.get("ProfileName", phone_number)
        timestamp = datetime.now()

        if not message or not phone_number:
            logger.error("❌ Missing message or phone number in WhatsApp request")
            return jsonify({"error": "Missing message or phone number"}), 400

        conversation_id = phone_number

        conn = get_db_connection()
        c = conn.cursor(cursor_factory=DictCursor)

        c.execute("""
            INSERT INTO conversations (conversation_id, username, phone_number, channel, last_message_timestamp)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id) DO UPDATE
            SET last_message_timestamp = EXCLUDED.last_message_timestamp,
                username = EXCLUDED.username
            RETURNING ai_enabled, handoff_notified
        """, (conversation_id, username, phone_number, "whatsapp", timestamp))
        convo = c.fetchone()

        c.execute("""
            INSERT INTO messages (convo_id, message, sender, timestamp, channel)
            VALUES (%s, %s, %s, %s, %s)
        """, (conversation_id, message, "user", timestamp, "whatsapp"))

        ai_enabled = convo["ai_enabled"] if convo else 1
        handoff_notified = convo["handoff_notified"] if convo else 0

        response = MessagingResponse()
        if ai_enabled:
            ai_response = get_ai_response(message, conversation_id)
            if ai_response:
                c.execute("""
                    INSERT INTO messages (convo_id, message, sender, timestamp, channel)
                    VALUES (%s, %s, %s, %s, %s)
                """, (conversation_id, ai_response, "ai", datetime.now(), "whatsapp"))
                response.message(ai_response)
                socketio.emit("live_message", {
                    "convo_id": conversation_id,
                    "message": ai_response,
                    "sender": "ai",
                    "username": username
                })
            else:
                if not handoff_notified:
                    c.execute("""
                        UPDATE conversations
                        SET handoff_notified = 1
                        WHERE conversation_id = %s
                    """, (conversation_id,))
                    socketio.emit("refresh_conversations", {})
        else:
            socketio.emit("refresh_conversations", {})

        conn.commit()
        logger.info("✅ Processed WhatsApp message successfully")
        return str(response)
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"❌ Error processing WhatsApp message: {e}", exc_info=True)
        return jsonify({"error": "Failed to process message"}), 500
    finally:
        if conn:
            conn.close()
            
@app.route("/whatsapp", methods=["GET"])
def whatsapp_verify():
    try:
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode and token:
            if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN"):
                logger.info("✅ WhatsApp webhook verified")
                return challenge, 200
            else:
                logger.error("❌ WhatsApp webhook verification failed")
                return "Forbidden", 403
        else:
            logger.error("❌ Missing parameters in WhatsApp webhook verification")
            return "Bad Request", 400
    except Exception as e:
        logger.error(f"❌ Error in /whatsapp GET endpoint: {str(e)}")
        return jsonify({"error": "Failed to verify WhatsApp webhook"}), 500

# Testing Endpoint
@app.route("/test-ai", methods=["POST"])
def test_ai():
    try:
        data = request.get_json()
        message = data.get("message")
        if not message:
            logger.error("❌ Missing message in /test-ai request")
            return jsonify({"error": "Missing message"}), 400

        # Create a temporary conversation for testing
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO conversations (username, chat_id, channel, ai_enabled, visible_in_conversations, last_updated) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                      ("test_user", "test_123", "whatsapp", 1, 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            convo_id = c.fetchone()['id']
            conn.commit()

        log_message(conn, convo_id, "test_user", message, "user")
        response = ai_respond(message, convo_id)
        log_message(conn, convo_id, "AI", response, "ai")

        logger.info(f"✅ Test AI response: {response}")
        return jsonify({"response": response})
    except Exception as e:
        logger.error(f"❌ Error in /test-ai: {e}")
        return jsonify({"error": "Failed to test AI"}), 500

# Socket.IO Events
@socketio.on("connect")
def handle_connect():
    logger.info("✅ Client connected via Socket.IO")
    emit("connection_status", {"status": "connected"})

@socketio.on('disconnect')
def handle_disconnect():
    try:
        logger.info(f"ℹ️ Client disconnected: {request.sid}")
        if 'conversation_id' in session:
            convo_id = session.get('conversation_id')
            if convo_id:
                logger.info(f"ℹ️ Client {request.sid} leaving conversation {convo_id}")
                emit('leave_conversation', {'conversation_id': convo_id}, room=convo_id, skip_sid=request.sid)
                session.pop('conversation_id', None)
    except Exception as e:
        logger.error(f"❌ Error during disconnect for client {request.sid}: {str(e)}")

@socketio.on('join_conversation')
def join_conversation(data):
    try:
        convo_id = data.get('conversation_id')
        if not convo_id:
            logger.error("❌ Missing conversation_id in join_conversation")
            emit('error', {'message': 'Missing conversation ID'})
            return
        join_room(convo_id)
        session['conversation_id'] = convo_id
        logger.info(f"✅ Client {request.sid} joined conversation {convo_id}")
    except Exception as e:
        logger.error(f"❌ Error in join_conversation for client {request.sid}: {str(e)}")
        emit('error', {'message': 'Failed to join conversation'})

@socketio.on('leave_conversation')
def leave_conversation(data):
    try:
        convo_id = data.get('conversation_id')
        if not convo_id:
            logger.error("❌ Missing conversation_id in leave_conversation")
            emit('error', {'message': 'Missing conversation ID'})
            return
        leave_room(convo_id)
        if 'conversation_id' in session and session['conversation_id'] == convo_id:
            session.pop('conversation_id', None)
        logger.info(f"✅ Client {request.sid} left conversation {convo_id}")
    except Exception as e:
        logger.error(f"❌ Error in leave_conversation for client {request.sid}: {str(e)}")
        emit('error', {'message': 'Failed to leave conversation'})

@socketio.on("agent_message")
def handle_agent_message(data):
    try:
        convo_id = data.get("convo_id")
        message = data.get("message")
        channel = data.get("channel", "whatsapp")

        if not convo_id or not message:
            logger.error("❌ Missing required fields in agent_message event")
            emit("error", {"message": "Missing required fields"})
            return

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found in agent_message: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                return
            username, chat_id, channel = result

        log_message(conn, convo_id, username, message, "agent")
        emit("new_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": "agent",
            "channel": channel,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }, room=convo_id)
        emit("live_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": "agent",
            "username": username,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

        if channel == "whatsapp":
            if not chat_id:
                logger.error(f"❌ No chat_id found for convo_id {convo_id}")
                emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp: No chat_id found", "channel": channel})
            else:
                if not send_whatsapp_message(chat_id, message):
                    logger.error(f"❌ Failed to send message to WhatsApp for chat_id {chat_id}")
                    emit("error", {"convo_id": convo_id, "message": "Failed to send message to WhatsApp", "channel": channel})

        logger.info(f"✅ Agent message sent for convo_id {convo_id}: {message}")
    except Exception as e:
        logger.error(f"❌ Error in agent_message event: {e}")
        emit("error", {"message": "Failed to send agent message"})

@socketio.on("new_message")
def handle_new_message(data):
    try:
        convo_id = data.get("convo_id")
        message = data.get("message")
        sender = data.get("sender")
        channel = data.get("channel", "whatsapp")

        if not convo_id or not message or not sender:
            logger.error("❌ Missing required fields in new_message event")
            emit("error", {"message": "Missing required fields"})
            return

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found in new_message: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                return
            username = result['username']

        emit("live_message", {
            "convo_id": convo_id,
            "message": message,
            "sender": sender,
            "username": username,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        logger.info(f"✅ Broadcasted new message for convo_id {convo_id}: {message}")
    except Exception as e:
        logger.error(f"❌ Error in new_message event: {e}")
        emit("error", {"message": "Failed to broadcast new message"})

@socketio.on("hand_back_to_ai")
def handle_hand_back_to_ai(data):
    try:
        convo_id = data.get("convo_id")
        if not convo_id:
            logger.error("❌ Missing convo_id in hand_back_to_ai event")
            emit("error", {"message": "Missing conversation ID"})
            return

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT username, chat_id, channel FROM conversations WHERE id = %s", (convo_id,))
            result = c.fetchone()
            if not result:
                logger.error(f"❌ Conversation not found in hand_back_to_ai: {convo_id}")
                emit("error", {"message": "Conversation not found"})
                return
            username, chat_id, channel = result

            c.execute("UPDATE conversations SET assigned_agent = NULL, ai_enabled = 1, handoff_notified = 0, last_updated = %s WHERE id = %s",
                      (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), convo_id))
            conn.commit()

        socketio.emit("agent_assigned", {
            "convo_id": convo_id,
            "agent": None,
            "user": chat_id or username,
            "channel": channel
        })
        socketio.emit("refresh_conversations", {
            "conversation_id": convo_id,
            "user": chat_id or username,
            "channel": channel
        })
        logger.info(f"✅ Handed back convo_id {convo_id} to AI")
        emit("handed_back_to_ai", {"convo_id": convo_id, "message": "Conversation handed back to AI"})
    except Exception as e:
        logger.error(f"❌ Error in hand_back_to_ai event: {e}")
        emit("error", {"message": "Failed to hand back to AI"})

# Import blueprints
from blueprints.dashboard import dashboard_bp
from blueprints.live_messages import live_messages_bp

# Register blueprints
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
app.register_blueprint(live_messages_bp, url_prefix='/live-messages')

if __name__ == "__main__":
    logger.info("🚀 Starting Flask-SocketIO server")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
                            
