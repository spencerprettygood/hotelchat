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
from datetime import datetime, timedelta
import time
import logging

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecretkey")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.init_app(app)

# Initialize OpenAI with API key
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    logger.error("⚠️ OPENAI_API_KEY not set in environment variables")
    raise ValueError("OPENAI_API_KEY not set")

# Log environment variables to debug
logger.info(f"Environment variables: {dict(os.environ)}")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("⚠️ TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_API_URL = "https://graph.instagram.com/v20.0"

DB_NAME = "chatbot.db"

# Load the Q&A reference document
try:
    with open("qa_reference.txt", "r") as file:
        TRAINING_DOCUMENT = file.read()
    logger.info("✅ Loaded Q&A reference document")
except FileNotFoundError:
    TRAINING_DOCUMENT = ""
    logger.warning("⚠️ qa_reference.txt not found, using empty training document")

def initialize_database():
    try:
        conn = sqlite3.connect(DB_NAME)
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
            opted_in INTEGER DEFAULT 0,  -- Not used, but kept for schema compatibility
            ai_enabled INTEGER DEFAULT 1,
            handoff_notified INTEGER DEFAULT 0,
            visible_in_conversations INTEGER DEFAULT 0
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
    except sqlite3.Error as e:
        logger.error(f"⚠️ Database initialization error: {e}")
    finally:
        conn.close()

initialize_database()

def add_test_conversations():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM conversations")
        if c.fetchone()[0] == 0:
            test_conversations = [
                ("guest1", "Hi, can I book a room?"),
                ("guest2", "What’s the check-in time?"),
                ("guest3", "Do you have a pool?")]
            convo_ids = []
            for username, message in test_conversations:
                c.execute("INSERT INTO conversations (username, latest_message, visible_in_conversations) VALUES (?, ?, 1)", (username, message))
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
            conn.commit()
            logger.info("✅ Test conversations added.")
    except sqlite3.Error as e:
        logger.error(f"⚠️ Test conversations error: {e}")
    finally:
        conn.close()

add_test_conversations()

class Agent(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(agent_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username FROM agents WHERE id = ?", (agent_id,))
    agent = c.fetchone()
    conn.close()
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
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username FROM agents WHERE username = ? AND password = ?", (username, password))
    agent = c.fetchone()
    conn.close()
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
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, username, latest_message, assigned_agent, channel FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC")
        conversations = [{"id": row[0], "username": row[1], "latest_message": row[2], "assigned_agent": row[3], "channel": row[4]} for row in c.fetchall()]
        conn.close()
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
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
        result = c.fetchone()
        conn.close()
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
        c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC", (convo_id,))
        messages = [{"message": row[0], "sender": row[1], "timestamp": row[2]} for row in c.fetchall()]
        conn.close()
        logger.info(f"✅ Fetched {len(messages)} messages for convo ID {convo_id}")
        return jsonify(messages)
    except Exception as e:
        logger.error(f"❌ Error fetching messages for convo ID {convo_id}: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

def log_message(convo_id, user, message, sender):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (?, ?, ?, ?)", 
                  (convo_id, user, message, sender))
        c.execute("UPDATE conversations SET latest_message = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", 
                  (message, convo_id))
        # Disable AI if sender is agent
        if sender == "agent":
            c.execute("UPDATE conversations SET ai_enabled = 0 WHERE id = ?", (convo_id,))
            logger.info(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
        conn.commit()
        conn.close()
        logger.info(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
    except Exception as e:
        logger.error(f"❌ Error logging message for convo_id {convo_id}: {e}")
        raise  # Re-raise the exception to catch it in the caller

def send_telegram_message(chat_id, text):
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text
        }
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"✅ Sent Telegram message to {chat_id}: {text}")
        # Add a small delay to avoid rate limiting
        time.sleep(0.5)
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message to {chat_id}: {str(e)}")
        raise

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
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT username, channel, assigned_agent, ai_enabled FROM conversations WHERE id = ?", (convo_id,))
        result = c.fetchone()
        username, channel, assigned_agent, ai_enabled = result if result else (None, None, None, None)
        conn.close()
        if not username:
            logger.error(f"❌ Conversation not found: {convo_id}")
            return jsonify({"error": "Conversation not found"}), 404
        
        # Determine sender: "user" for client, "agent" if sent by logged-in agent
        sender = "agent" if current_user.is_authenticated else "user"
        logger.info(f"✅ Processing /chat message as sender: {sender}")
        log_message(convo_id, username, user_message, sender)

        # If message is from an agent, emit it and send to Telegram if channel is telegram
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

        # Otherwise, process as a client message and get AI response if AI is enabled
        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if ai_enabled:
            logger.info("✅ AI is enabled, proceeding with AI response")
            try:
                logger.info(f"Processing message with AI: {user_message}")
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Escalate to a human if the query is complex or requires personal assistance."},
                        {"role": "user", "content": user_message}
                    ],
                    max_tokens=150
                )
                ai_reply = response.choices[0].message.content.strip()
                logger.info("✅ AI reply: " + ai_reply)
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
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                    handoff_notified = c.fetchone()[0]
                    logger.info(f"✅ Handoff check for convo_id {convo_id}: handoff_notified={handoff_notified}")
                    if not handoff_notified:
                        # Automatically assign to "agent1"
                        default_agent = "agent1"
                        c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (default_agent, convo_id))
                        conn.commit()
                        # Increase delay to ensure the transaction is committed
                        time.sleep(1.0)
                        socketio.emit("handoff", {"conversation_id": convo_id, "agent": default_agent, "user": username, "channel": channel})
                        logger.info(f"✅ Handoff triggered for convo_id {convo_id}, assigned to {default_agent}, chat now visible in Conversations")
                    conn.close()
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
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        logger.info(f"✅ Querying conversations for username: {from_number}")
        c.execute("SELECT id, last_updated, handoff_notified, ai_enabled FROM conversations WHERE username = ?", (from_number,))
        convo = c.fetchone()

        # Check if this is a new conversation
        is_new_conversation = not convo
        logger.info(f"✅ Is new conversation: {is_new_conversation}")
        if is_new_conversation:
            logger.info("✅ Creating new conversation")
            c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled, visible_in_conversations) VALUES (?, ?, ?, 1, 1)", 
                      (from_number, incoming_msg, "telegram"))
            convo_id = c.lastrowid
            handoff_notified = 0
            ai_enabled = 1
            logger.info(f"✅ Created new conversation for {from_number}: convo_id {convo_id}")
        else:
            convo_id, last_updated, handoff_notified, ai_enabled = convo
            logger.info(f"✅ Existing conversation found: convo_id={convo_id}, last_updated={last_updated}, handoff_notified={handoff_notified}, ai_enabled={ai_enabled}")
            # Check if last message was more than 24 hours ago to reset ai_enabled and handoff_notified
            last_updated_dt = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - last_updated_dt) > timedelta(hours=24):
                logger.info("✅ Last message was >24 hours ago, resetting ai_enabled and handoff_notified")
                c.execute("UPDATE conversations SET ai_enabled = 1, handoff_notified = 0 WHERE id = ?", (convo_id,))
                conn.commit()
                logger.info("✅ Reset ai_enabled and handoff_notified for convo_id: " + str(convo_id))
                ai_enabled = 1
        conn.close()

        # Log and emit the client message
        logger.info("✅ Logging client message")
        log_message(convo_id, from_number, incoming_msg, "user")
        logger.info("✅ Emitting new_message event for client message")
        socketio.emit("new_message", {"convo_id": convo_id, "message": incoming_msg, "sender": "user", "channel": "telegram", "user": from_number})

        # Only proceed with AI response if ai_enabled is True
        logger.info(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if not ai_enabled:
            logger.info("❌ AI disabled for convo_id: " + str(convo_id) + ", Skipping AI response")
            return "OK", 200

        # Send welcome message for new conversations
        if is_new_conversation:
            logger.info("✅ Sending welcome message for new conversation")
            welcome_message = "Welcome to Sunshine Hotel! I'm here to assist with your bookings. How can I help you today?"
            try:
                logger.info(f"Sending welcome message to Telegram - To: {from_number}, Body: {welcome_message}")
                send_telegram_message(from_number, welcome_message)
            except Exception as e:
                logger.error(f"❌ Telegram error sending welcome message: {str(e)}")
                socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send welcome message to Telegram: {str(e)}", "channel": "telegram"})
            logger.info("✅ Logging welcome message")
            log_message(convo_id, "AI", welcome_message, "ai")
            logger.info("✅ Emitting new_message event for welcome message")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "telegram"})

        # Process the message with AI
        logger.info("✅ Processing message with AI")
        try:
            logger.info(f"Processing message with AI for convo_id {convo_id} with gpt-3.5-turbo: {incoming_msg}")
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Escalate to a human if the query is complex or requires personal assistance."},
                    {"role": "user", "content": incoming_msg}
                ],
                max_tokens=150
            )
            ai_reply = response.choices[0].message.content.strip()
            logger.info("✅ AI reply: " + ai_reply)
        except Exception as e:
            ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
            logger.error(f"❌ OpenAI error: {str(e)}")
            logger.error(f"❌ OpenAI error type: {type(e).__name__}")

        # Fallback: Force handoff for specific keywords like "HELP"
        logger.info("✅ Checking for HELP keyword")
        if "HELP" in incoming_msg.upper():
            ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
            logger.info("✅ Forcing handoff for keyword 'HELP', AI reply set to: " + ai_reply)

        # Log and emit the AI response
        logger.info("✅ Logging AI response")
        log_message(convo_id, "AI", ai_reply, "ai")
        logger.info("✅ Emitting new_message event for AI response")
        socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "telegram"})

        # Send AI response to Telegram
        logger.info("✅ Sending AI response to Telegram")
        try:
            logger.info(f"Sending AI message to Telegram - To: {from_number}, Body: {ai_reply}")
            send_telegram_message(from_number, ai_reply)
            logger.info("✅ AI message sent to Telegram: " + ai_reply)
        except Exception as e:
            logger.error(f"❌ Telegram error sending AI message: {str(e)}")
            socketio.emit("error", {"convo_id": convo_id, "message": f"Failed to send message to Telegram: {str(e)}", "channel": "telegram"})

        # Handle handoff if needed
        logger.info("✅ Checking for handoff condition")
        if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
            try:
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT handoff_notified, visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
                result = c.fetchone()
                handoff_notified, visible_in_conversations = result if result else (0, 0)
                logger.info(f"✅ Handoff check for convo_id {convo_id}: handoff_notified={handoff_notified}, visible_in_conversations={visible_in_conversations}")
                if not handoff_notified:
                    # Automatically assign to "agent1"
                    default_agent = "agent1"
                    c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (default_agent, convo_id))
                    conn.commit()
                    # Increase delay to ensure the transaction is committed
                    time.sleep(1.0)
                    socketio.emit("handoff", {"conversation_id": convo_id, "agent": default_agent, "user": from_number, "channel": "telegram"})
                    logger.info(f"✅ Handoff triggered for convo_id {convo_id}, assigned to {default_agent}, chat now visible in Conversations")
                conn.close()
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
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT id FROM conversations WHERE username = ?", (sender_id,))
                convo = c.fetchone()
                if not convo:
                    c.execute("INSERT INTO conversations (username, latest_message, channel) VALUES (?, ?, ?)", (sender_id, incoming_msg, "instagram"))
                    convo_id = c.lastrowid
                else:
                    convo_id = convo[0]
                conn.close()
                logger.info(f"✅ Conversation ID for Instagram: {convo_id}")
                log_message(convo_id, sender_id, incoming_msg, "user")
                try:
                    logger.info(f"Processing Instagram message with AI: {incoming_msg}")
                    response = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[
                            {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Escalate to a human if the query is complex or requires personal assistance."},
                            {"role": "user", "content": incoming_msg}
                        ],
                        max_tokens=150
                    )
                    ai_reply = response.choices[0].message.content.strip()
                    logger.info("✅ Instagram AI reply: " + ai_reply)
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
                        conn = sqlite3.connect(DB_NAME)
                        c = conn.cursor()
                        c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                        handoff_notified = c.fetchone()[0]
                        if not handoff_notified:
                            default_agent = "agent1"
                            c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (default_agent, convo_id))
                            conn.commit()
                            time.sleep(1.0)
                            socketio.emit("handoff", {"conversation_id": convo_id, "agent": default_agent, "user": sender_id, "channel": "instagram"})
                            logger.info(f"✅ Instagram handoff triggered for convo_id {convo_id}, assigned to {default_agent}")
                        conn.close()
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

@app.route("/send-welcome", methods=["POST"])
def send_welcome():
    data = request.get_json()
    to_number = data.get("to_number")
    user_name = data.get("user_name", "Guest")
    if not to_number:
        logger.error("❌ Missing to_number in /send-welcome request")
        return jsonify({"error": "Missing to_number"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM conversations WHERE username = ? AND channel = ?", (to_number, "telegram"))
    convo = c.fetchone()
    if not convo:
        conn.close()
        logger.error("❌ Conversation not found in /send-welcome")
        return jsonify({"error": "Conversation not found"}), 404
    convo_id = convo[0]
    conn.close()
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
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 0 WHERE id = ?", (current_user.username, convo_id))
        c.execute("SELECT username, channel FROM conversations WHERE id = ?", (convo_id,))
        username, channel = c.fetchone()
        conn.commit()
        conn.close()
        socketio.emit("handoff", {"conversation_id": convo_id, "agent": current_user.username, "user": username, "channel": channel})
        logger.info(f"✅ Chat {convo_id} assigned to {current_user.username}")
        return jsonify({"message": f"Chat assigned to {current_user.username}"})
    except Exception as e:
        logger.error(f"❌ Error in /handoff endpoint: {e}")
        return jsonify({"error": "Failed to assign chat"}), 500

@app.route("/clear-database", methods=["POST"])
def clear_database():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM conversations")
        c.execute("DELETE FROM messages")
        conn.commit()
        conn.close()
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
