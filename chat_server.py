import gevent.monkey
gevent.monkey.patch_all()

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import openai
import sqlite3
import os
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import requests
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecretkey")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

login_manager = LoginManager()
login_manager.init_app(app)

openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("⚠️ OPENAI_API_KEY not set in environment variables")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_API_URL = "https://graph.instagram.com/v20.0"

DB_NAME = "chatbot.db"

# Load the Q&A reference document
try:
    with open("qa_reference.txt", "r") as file:
        TRAINING_DOCUMENT = file.read()
    print("✅ Loaded Q&A reference document")
except FileNotFoundError:
    TRAINING_DOCUMENT = ""
    print("⚠️ qa_reference.txt not found, using empty training document")

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
            print("✅ Added test agent: agent1/password123")
        conn.commit()
    except sqlite3.Error as e:
        print(f"⚠️ Database initialization error: {e}")
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
            print("✅ Test conversations added.")
    except sqlite3.Error as e:
        print(f"⚠️ Test conversations error: {e}")
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
        print("❌ Missing username or password in /login request")
        return jsonify({"message": "Missing username or password"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username FROM agents WHERE username = ? AND password = ?", (username, password))
    agent = c.fetchone()
    conn.close()
    if agent:
        agent_obj = Agent(agent[0], agent[1])
        login_user(agent_obj)
        print(f"✅ Login successful for agent: {agent[1]}")
        return jsonify({"message": "Login successful", "agent": agent[1]})
    print("❌ Invalid credentials in /login request")
    return jsonify({"message": "Invalid credentials"}), 401

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    print("✅ Logout successful")
    return jsonify({"message": "Logged out successfully"})

@app.route("/conversations", methods=["GET"])
def get_conversations():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, username, latest_message, assigned_agent, channel FROM conversations WHERE visible_in_conversations = 1 ORDER BY last_updated DESC")
        conversations = [{"id": row[0], "username": row[1], "latest_message": row[2], "assigned_agent": row[3], "channel": row[4]} for row in c.fetchall()]
        conn.close()
        print(f"✅ Fetched conversations: {len(conversations)} conversations")
        return jsonify(conversations)
    except Exception as e:
        print(f"❌ Error fetching conversations: {e}")
        return jsonify({"error": "Failed to fetch conversations"}), 500

@app.route("/check-visibility", methods=["GET"])
def check_visibility():
    convo_id = request.args.get("conversation_id")
    if not convo_id:
        print("❌ Missing conversation ID in check-visibility request")
        return jsonify({"error": "Missing conversation ID"}), 400
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
        result = c.fetchone()
        conn.close()
        if result:
            print(f"✅ Visibility check for convo ID {convo_id}: {bool(result[0])}")
            return jsonify({"visible": bool(result[0])})
        print(f"❌ Conversation not found: {convo_id}")
        return jsonify({"error": "Conversation not found"}), 404
    except Exception as e:
        print(f"❌ Error checking visibility for convo ID {convo_id}: {e}")
        return jsonify({"error": "Failed to check visibility"}), 500

@app.route("/messages", methods=["GET"])
def get_messages():
    convo_id = request.args.get("conversation_id")
    if not convo_id:
        print("❌ Missing conversation ID in get-messages request")
        return jsonify({"error": "Missing conversation ID"}), 400
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC", (convo_id,))
        messages = [{"message": row[0], "sender": row[1], "timestamp": row[2]} for row in c.fetchall()]
        conn.close()
        print(f"✅ Fetched {len(messages)} messages for convo ID {convo_id}")
        return jsonify(messages)
    except Exception as e:
        print(f"❌ Error fetching messages for convo ID {convo_id}: {e}")
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
            print(f"✅ Disabled AI for convo_id {convo_id} because agent responded")
        conn.commit()
        conn.close()
        print(f"✅ Logged message for convo_id {convo_id}: {message} (Sender: {sender})")
    except Exception as e:
        print(f"❌ Error logging message for convo_id {convo_id}: {e}")
        raise  # Re-raise the exception to catch it in the caller

@app.route("/check-auth", methods=["GET"])
def check_auth():
    return jsonify({"is_authenticated": current_user.is_authenticated})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    user_message = data.get("message")
    if not convo_id or not user_message:
        print("❌ Missing required fields in /chat request")
        return jsonify({"error": "Missing required fields"}), 400
    try:
        print("✅ Entering /chat endpoint")
        print(f"✅ Fetching conversation details for convo_id {convo_id}")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT username, channel, assigned_agent, ai_enabled FROM conversations WHERE id = ?", (convo_id,))
        result = c.fetchone()
        username, channel, assigned_agent, ai_enabled = result if result else (None, None, None, None)
        conn.close()
        if not username:
            print(f"❌ Conversation not found: {convo_id}")
            return jsonify({"error": "Conversation not found"}), 404
        
        # Determine sender: "user" for client, "agent" if sent by logged-in agent
        sender = "agent" if current_user.is_authenticated else "user"
        print(f"✅ Processing /chat message as sender: {sender}")
        log_message(convo_id, username, user_message, sender)

        # If message is from an agent, emit it and send to WhatsApp if channel is whatsapp
        if sender == "agent":
            print("✅ Sender is agent, emitting new_message event")
            socketio.emit("new_message", {"convo_id": convo_id, "message": user_message, "sender": "agent", "channel": channel})
            if channel == "whatsapp":
                try:
                    print("Sending agent message to WhatsApp - From:", f"whatsapp:{TWILIO_PHONE_NUMBER}", "To:", f"whatsapp:{username}", "Body:", user_message)
                    twilio_client.messages.create(
                        body=user_message,
                        from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
                        to=f"whatsapp:{username}"
                    )
                    print("✅ Agent message sent to WhatsApp:", user_message)
                except Exception as e:
                    print(f"❌ Twilio error sending agent message: {str(e)}")
                    print(f"Twilio error details: {e.__dict__}")
            print("✅ Agent message processed successfully")
            return jsonify({"status": "success"})

        # Otherwise, process as a client message and get AI response if AI is enabled
        print(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if ai_enabled:
            print("✅ AI is enabled, proceeding with AI response")
            try:
                print("AI processing message:", user_message)
                response = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Escalate to a human if the query is complex or requires personal assistance."},
                        {"role": "user", "content": user_message}
                    ],
                    max_tokens=150
                )
                ai_reply = response.choices[0].message.content.strip()
                print("✅ AI reply:", ai_reply)
            except Exception as e:
                ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
                print(f"❌ OpenAI error: {e}")
            print("✅ Logging and emitting AI response")
            log_message(convo_id, "AI", ai_reply, "ai")
            socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "whatsapp"})
            if channel == "whatsapp":
                try:
                    print("Sending AI message to WhatsApp - From:", f"whatsapp:{TWILIO_PHONE_NUMBER}", "To:", f"whatsapp:{username}", "Body:", ai_reply)
                    twilio_client.messages.create(
                        body=ai_reply,
                        from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
                        to=f"whatsapp:{username}"
                    )
                    print("✅ AI message sent to WhatsApp:", ai_reply)
                except Exception as e:
                    print(f"❌ Twilio error sending AI message: {str(e)}")
                    print(f"Twilio error details: {e.__dict__}")
            print("✅ Checking for handoff condition")
            if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
                try:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute("SELECT handoff_notified FROM conversations WHERE id = ?", (convo_id,))
                    handoff_notified = c.fetchone()[0]
                    print(f"✅ Handoff check for convo_id {convo_id}: handoff_notified={handoff_notified}")
                    if not handoff_notified:
                        # Automatically assign to "agent1"
                        default_agent = "agent1"
                        c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (default_agent, convo_id))
                        conn.commit()
                        socketio.emit("handoff", {"conversation_id": convo_id, "agent": default_agent, "user": username, "channel": channel})
                        print(f"✅ Handoff triggered for convo_id {convo_id}, assigned to {default_agent}, chat now visible in Conversations")
                    conn.close()
                except Exception as e:
                    print(f"❌ Error during handoff for convo_id {convo_id}: {e}")
            print("✅ AI response processed successfully")
            return jsonify({"reply": ai_reply})
        else:
            print("❌ AI disabled for convo_id:", convo_id)
            return jsonify({"status": "Message received, AI disabled"})
    except Exception as e:
        print(f"❌ Error in /chat endpoint: {e}")
        return jsonify({"error": "Failed to process chat message"}), 500

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    print("✅ Entering /whatsapp endpoint")
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").replace("whatsapp:", "")
    print("✅ Received WhatsApp message:", incoming_msg, "from:", from_number)
    try:
        print("✅ Connecting to database")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        print("✅ Querying conversations for username:", from_number)
        c.execute("SELECT id, last_updated, handoff_notified, ai_enabled FROM conversations WHERE username = ?", (from_number,))
        convo = c.fetchone()

        # Check if this is a new conversation
        is_new_conversation = not convo
        print(f"✅ Is new conversation: {is_new_conversation}")
        if is_new_conversation:
            print("✅ Creating new conversation")
            c.execute("INSERT INTO conversations (username, latest_message, channel, ai_enabled) VALUES (?, ?, ?, 1)", 
                      (from_number, incoming_msg, "whatsapp"))
            convo_id = c.lastrowid
            handoff_notified = 0
            ai_enabled = 1
            print(f"✅ Created new conversation for {from_number}: convo_id {convo_id}")
        else:
            convo_id, last_updated, handoff_notified, ai_enabled = convo
            print(f"✅ Existing conversation found: convo_id={convo_id}, last_updated={last_updated}, handoff_notified={handoff_notified}, ai_enabled={ai_enabled}")
            # Check if last message was more than 24 hours ago to reset ai_enabled and handoff_notified
            last_updated_dt = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - last_updated_dt) > timedelta(hours=24):
                print("✅ Last message was >24 hours ago, resetting ai_enabled and handoff_notified")
                c.execute("UPDATE conversations SET ai_enabled = 1, handoff_notified = 0 WHERE id = ?", (convo_id,))
                conn.commit()
                print("✅ Reset ai_enabled and handoff_notified for convo_id:", convo_id)
                ai_enabled = 1
        conn.close()

        # Log and emit the client message
        print("✅ Logging client message")
        log_message(convo_id, from_number, incoming_msg, "user")
        print("✅ Emitting new_message event for client message")
        socketio.emit("new_message", {"convo_id": convo_id, "message": incoming_msg, "sender": "user", "channel": "whatsapp", "user": from_number})

        # Only proceed with AI response if ai_enabled is True
        print(f"✅ Checking if AI is enabled: ai_enabled={ai_enabled}")
        if not ai_enabled:
            print("❌ AI disabled for convo_id:", convo_id, "Skipping AI response")
            resp = MessagingResponse()
            return str(resp)

        # Send welcome message for new conversations
        if is_new_conversation:
            print("✅ Sending welcome message for new conversation")
            welcome_message = "Welcome to Sunshine Hotel! I'm here to assist with your bookings. How can I help you today?"
            try:
                print("Sending welcome message to WhatsApp - From:", f"whatsapp:{TWILIO_PHONE_NUMBER}", "To:", f"whatsapp:{from_number}", "Body:", welcome_message)
                twilio_client.messages.create(
                    body=welcome_message,
                    from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
                    to=f"whatsapp:{from_number}"
                )
                print("✅ Sent welcome message to WhatsApp")
            except Exception as e:
                print(f"❌ Twilio error sending welcome message: {str(e)}")
                print(f"Twilio error details: {e.__dict__}")
            print("✅ Logging welcome message")
            log_message(convo_id, "AI", welcome_message, "ai")
            print("✅ Emitting new_message event for welcome message")
            socketio.emit("new_message", {"convo_id": convo_id, "message": welcome_message, "sender": "ai", "channel": "whatsapp"})

        # Process the message with AI
        print("✅ Processing message with AI")
        try:
            print(f"Processing message with AI for convo_id {convo_id}: {incoming_msg}")
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Escalate to a human if the query is complex or requires personal assistance."},
                    {"role": "user", "content": incoming_msg}
                ],
                max_tokens=150
            )
            ai_reply = response.choices[0].message.content.strip()
            print("✅ AI reply:", ai_reply)
        except Exception as e:
            ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
            print(f"❌ OpenAI error: {e}")

        # Fallback: Force handoff for specific keywords like "HELP"
        print("✅ Checking for HELP keyword")
        if "HELP" in incoming_msg.upper():
            ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
            print("✅ Forcing handoff for keyword 'HELP', AI reply set to:", ai_reply)

        # Log and emit the AI response
        print("✅ Logging AI response")
        log_message(convo_id, "AI", ai_reply, "ai")
        print("✅ Emitting new_message event for AI response")
        socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "whatsapp"})

        # Send AI response to WhatsApp
        print("✅ Sending AI response to WhatsApp")
        try:
            print("Sending AI message to WhatsApp - From:", f"whatsapp:{TWILIO_PHONE_NUMBER}", "To:", f"whatsapp:{from_number}", "Body:", ai_reply)
            twilio_client.messages.create(
                body=ai_reply,
                from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
                to=f"whatsapp:{from_number}"
            )
            print("✅ AI message sent to WhatsApp:", ai_reply)
        except Exception as e:
            print(f"❌ Twilio error sending AI message: {str(e)}")
            print(f"Twilio error details: {e.__dict__}")

        # Handle handoff if needed
        print("✅ Checking for handoff condition")
        if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
            try:
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT handoff_notified, visible_in_conversations FROM conversations WHERE id = ?", (convo_id,))
                result = c.fetchone()
                handoff_notified, visible_in_conversations = result if result else (0, 0)
                print(f"✅ Handoff check for convo_id {convo_id}: handoff_notified={handoff_notified}, visible_in_conversations={visible_in_conversations}")
                if not handoff_notified:
                    # Automatically assign to "agent1"
                    default_agent = "agent1"
                    c.execute("UPDATE conversations SET assigned_agent = ?, handoff_notified = 1, visible_in_conversations = 1 WHERE id = ?", (default_agent, convo_id))
                    conn.commit()
                    socketio.emit("handoff", {"conversation_id": convo_id, "agent": default_agent, "user": from_number, "channel": "whatsapp"})
                    print(f"✅ Handoff triggered for convo_id {convo_id}, assigned to {default_agent}, chat now visible in Conversations")
                conn.close()
            except Exception as e:
                print(f"❌ Error during handoff for convo_id {convo_id}: {e}")

        print("✅ Preparing response for WhatsApp")
        resp = MessagingResponse()
        print("✅ Returning response")
        return str(resp)
    except Exception as e:
        print(f"❌ Error in /whatsapp endpoint: {e}")
        resp = MessagingResponse()
        return str(resp)

@app.route("/instagram", methods=["POST"])
def instagram():
    print("✅ Entering /instagram endpoint")
    data = request.get_json()
    if "object" not in data or data["object"] != "instagram":
        print("✅ Not an Instagram event, returning OK")
        return "OK", 200
    for entry in data.get("entry", []):
        for messaging in entry.get("messaging", []):
            sender_id = messaging["sender"]["id"]
            incoming_msg = messaging["message"].get("text", "")
            print("✅ Received Instagram message:", incoming_msg, "from:", sender_id)
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
                print(f"✅ Conversation ID for Instagram: {convo_id}")
                log_message(convo_id, sender_id, incoming_msg, "user")
                try:
                    print("Processing Instagram message with AI:", incoming_msg)
                    response = openai.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": TRAINING_DOCUMENT + "\nYou are a hotel chatbot acting as a friendly salesperson. Use the provided business information and Q&A to answer guest questions. Escalate to a human if the query is complex or requires personal assistance."},
                            {"role": "user", "content": incoming_msg}
                        ],
                        max_tokens=150
                    )
                    ai_reply = response.choices[0].message.content.strip()
                    print("✅ Instagram AI reply:", ai_reply)
                except Exception as e:
                    ai_reply = "I’m sorry, I couldn’t process that. Let me get a human to assist you."
                    print(f"❌ Instagram OpenAI error: {e}")
                print("✅ Logging Instagram AI response")
                log_message(convo_id, "AI", ai_reply, "ai")
                print("✅ Sending Instagram AI response")
                requests.post(
                    f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                    json={"recipient": {"id": sender_id}, "message": {"text": ai_reply}}
                )
                print("✅ Emitting new_message event for Instagram")
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
                            socketio.emit("handoff", {"conversation_id": convo_id, "agent": default_agent, "user": sender_id, "channel": "instagram"})
                            print(f"✅ Instagram handoff triggered for convo_id {convo_id}, assigned to {default_agent}")
                        conn.close()
                    except Exception as e:
                        print(f"❌ Error during Instagram handoff for convo_id {convo_id}: {e}")
            except Exception as e:
                print(f"❌ Error in /instagram endpoint: {e}")
    print("✅ Returning EVENT_RECEIVED for Instagram")
    return "EVENT_RECEIVED", 200

@app.route("/instagram", methods=["GET"])
def instagram_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.getenv("VERIFY_TOKEN", "mysecretverifytoken"):
        print("✅ Instagram verification successful")
        return challenge, 200
    print("❌ Instagram verification failed")
    return "Verification failed", 403

@app.route("/send-welcome", methods=["POST"])
def send_welcome():
    data = request.get_json()
    to_number = data.get("to_number")
    user_name = data.get("user_name", "Guest")
    if not to_number:
        print("❌ Missing to_number in /send-welcome request")
        return jsonify({"error": "Missing to_number"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM conversations WHERE username = ? AND channel = ?", (to_number, "whatsapp"))
    convo = c.fetchone()
    if not convo:
        conn.close()
        print("❌ Conversation not found in /send-welcome")
        return jsonify({"error": "Conversation not found"}), 404
    convo_id = convo[0]
    conn.close()
    try:
        print(f"✅ Sending welcome message to {to_number}")
        twilio_client.messages.create(
            body=f"Welcome to our hotel, {user_name}! We're here to assist with your bookings. Reply 'BOOK' to start or 'HELP' for assistance.",
            from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
            to=to_number
        )
        print("✅ Logging welcome message in /send-welcome")
        log_message(convo_id, "AI", f"Welcome to our hotel, {user_name}!", "ai")
        print("✅ Emitting new_message event in /send-welcome")
        socketio.emit("new_message", {"convo_id": convo_id, "message": f"Welcome to our hotel, {user_name}!", "sender": "ai", "channel": "whatsapp"})
        print("✅ Welcome message sent successfully")
        return jsonify({"message": "Welcome message sent"}), 200
    except Exception as e:
        print(f"❌ Twilio error in send-welcome: {e}")
        return jsonify({"error": "Failed to send message"}), 500

@app.route("/handoff", methods=["POST"])
@login_required
def handoff():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    if not convo_id:
        print("❌ Missing conversation ID in /handoff request")
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
        print(f"✅ Chat {convo_id} assigned to {current_user.username}")
        return jsonify({"message": f"Chat assigned to {current_user.username}"})
    except Exception as e:
        print(f"❌ Error in /handoff endpoint: {e}")
        return jsonify({"error": "Failed to assign chat"}), 500

@app.route("/")
def index():
    return render_template("dashboard.html")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
