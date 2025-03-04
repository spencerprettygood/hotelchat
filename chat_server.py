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
            opted_in INTEGER DEFAULT 0,
            ai_enabled INTEGER DEFAULT 1  -- New column for AI control
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
                c.execute("INSERT INTO conversations (username, latest_message) VALUES (?, ?)", (username, message))
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
        return jsonify({"message": "Missing username or password"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username FROM agents WHERE username = ? AND password = ?", (username, password))
    agent = c.fetchone()
    conn.close()
    if agent:
        agent_obj = Agent(agent[0], agent[1])
        login_user(agent_obj)
        return jsonify({"message": "Login successful", "agent": agent[1]})
    return jsonify({"message": "Invalid credentials"}), 401

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"message": "Logged out successfully"})

@app.route("/conversations", methods=["GET"])
def get_conversations():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username, latest_message, assigned_agent, channel FROM conversations ORDER BY last_updated DESC")
    conversations = [{"id": row[0], "username": row[1], "latest_message": row[2], "assigned_agent": row[3], "channel": row[4]} for row in c.fetchall()]
    conn.close()
    return jsonify(conversations)

@app.route("/messages", methods=["GET"])
def get_messages():
    convo_id = request.args.get("conversation_id")
    if not convo_id:
        return jsonify({"error": "Missing conversation ID"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT message, sender, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC", (convo_id,))
    messages = [{"message": row[0], "sender": row[1], "timestamp": row[2]} for row in c.fetchall()]
    conn.close()
    return jsonify(messages)

def log_message(convo_id, user, message, sender):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO messages (conversation_id, user, message, sender) VALUES (?, ?, ?, ?)", 
              (convo_id, user, message, sender))
    c.execute("UPDATE conversations SET latest_message = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", 
              (message, convo_id))
    # Disable AI if sender is agent
    if sender == "agent":
        c.execute("UPDATE conversations SET ai_enabled = 0 WHERE id = ?", (convo_id,))
    conn.commit()
    conn.close()

@app.route("/check-auth", methods=["GET"])
def check_auth():
    return jsonify({"is_authenticated": current_user.is_authenticated})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    user_message = data.get("message")
    if not convo_id or not user_message:
        return jsonify({"error": "Missing required fields"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username, channel, assigned_agent, ai_enabled FROM conversations WHERE id = ?", (convo_id,))
    result = c.fetchone()
    username, channel, assigned_agent, ai_enabled = result if result else (None, None, None, None)
    conn.close()
    if not username:
        return jsonify({"error": "Conversation not found"}), 404
    
    # Determine sender: "user" for client, "agent" if sent by logged-in agent
    sender = "agent" if current_user.is_authenticated else "user"
    log_message(convo_id, username, user_message, sender)

    # If message is from an agent, emit it and send to WhatsApp if channel is whatsapp
    if sender == "agent":
        socketio.emit("new_message", {"convo_id": convo_id, "message": user_message, "sender": "agent", "channel": channel})
        if channel == "whatsapp":
            try:
                twilio_client.messages.create(
                    body=user_message,
                    from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
                    to=f"whatsapp:{username}"
                )
                print("Agent message sent to WhatsApp:", user_message)
            except Exception as e:
                print(f"Twilio error sending agent message: {e}")
        return jsonify({"status": "success"})

    # Otherwise, process as a client message and get AI response if AI is enabled
    if ai_enabled:
        try:
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a hotel chatbot. Answer guest questions and escalate to a human if the query is complex or requires personal assistance."},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=150
            )
            ai_reply = response.choices[0].message.content.strip()
        except Exception as e:
            ai_reply = "Sorry, I couldn’t process that. Let me get a human to assist you."
            print(f"OpenAI error: {e}")
        log_message(convo_id, "AI", ai_reply, "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": channel})
        if channel == "whatsapp":
            twilio_client.messages.create(
                body=ai_reply,
                from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
                to=f"whatsapp:{username}"
            )
        if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
            socketio.emit("handoff", {"conversation_id": convo_id, "agent": "unassigned", "user": username, "channel": channel})
        return jsonify({"reply": ai_reply})
    else:
        return jsonify({"status": "Message received, AI disabled"})

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = request.values.get("Body", "").strip().upper()
    from_number = request.values.get("From", "").replace("whatsapp:", "")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, opted_in, last_updated FROM conversations WHERE username = ?", (from_number,))
    convo = c.fetchone()
    if not convo:
        c.execute("INSERT INTO conversations (username, latest_message, channel) VALUES (?, ?, ?)", 
                  (from_number, incoming_msg, "whatsapp"))
        convo_id = c.lastrowid
        opted_in = 0
    else:
        convo_id, opted_in, last_updated = convo
        # Check if last message was more than 24 hours ago to reset ai_enabled
        last_updated_dt = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last_updated_dt) > timedelta(hours=24):
            c.execute("UPDATE conversations SET ai_enabled = 1 WHERE id = ?", (convo_id,))
            conn.commit()
            print("Reset ai_enabled for convo_id:", convo_id)
    if incoming_msg == "YES" and not opted_in:
        c.execute("UPDATE conversations SET opted_in = 1 WHERE id = ?", (convo_id,))
        conn.commit()
        twilio_client.messages.create(
            body="Thank you for opting in! You'll now receive updates from us.",
            from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
            to=f"whatsapp:{from_number}"
        )
        log_message(convo_id, "AI", "Thank you for opting in!", "ai")
        print("Emitting new_message for convo_id:", convo_id)
        socketio.emit("new_message", {"convo_id": convo_id, "message": "Thank you for opting in!", "sender": "ai", "channel": "whatsapp"})
        resp = MessagingResponse()
        conn.close()
        return str(resp)
    conn.close()
    log_message(convo_id, from_number, incoming_msg, "user")
    if not opted_in and incoming_msg != "YES":
        twilio_client.messages.create(
            body="Would you like to receive updates from us? Reply YES to opt in.",
            from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
            to=f"whatsapp:{from_number}"
        )
        log_message(convo_id, "AI", "Would you like to receive updates? Reply YES to opt in.", "ai")
        print("Emitting new_message for convo_id:", convo_id)
        socketio.emit("new_message", {"convo_id": convo_id, "message": "Would you like to receive updates? Reply YES to opt in.", "sender": "ai", "channel": "whatsapp"})
        resp = MessagingResponse()
        return str(resp)
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a hotel chatbot. Answer guest questions and escalate to a human if the query is complex or requires personal assistance."},
                {"role": "user", "content": incoming_msg}
            ],
            max_tokens=150
        )
        ai_reply = response.choices[0].message.content.strip()
    except Exception as e:
        ai_reply = "Sorry, I couldn’t process that. Let me get a human to assist you."
        print(f"OpenAI error: {e}")
    log_message(convo_id, "AI", ai_reply, "ai")
    print("Emitting new_message for convo_id:", convo_id)
    socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "whatsapp"})
    if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
        socketio.emit("handoff", {"conversation_id": convo_id, "agent": "unassigned", "user": from_number, "channel": "whatsapp"})
    resp = MessagingResponse()
    return str(resp)

@app.route("/instagram", methods=["POST"])
def instagram():
    data = request.get_json()
    if "object" not in data or data["object"] != "instagram":
        return "OK", 200
    for entry in data.get("entry", []):
        for messaging in entry.get("messaging", []):
            sender_id = messaging["sender"]["id"]
            incoming_msg = messaging["message"].get("text", "")
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
            log_message(convo_id, sender_id, incoming_msg, "user")
            try:
                response = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a hotel chatbot. Answer guest questions and escalate to a human if the query is complex or requires personal assistance."},
                        {"role": "user", "content": incoming_msg}
                    ],
                    max_tokens=150
                )
                ai_reply = response.choices[0].message.content.strip()
            except Exception as e:
                ai_reply = "Sorry, I couldn’t process that. Let me get a human to assist you."
                print(f"OpenAI error: {e}")
            log_message(convo_id, "AI", ai_reply, "ai")
            requests.post(
                f"{INSTAGRAM_API_URL}/me/messages?access_token={INSTAGRAM_ACCESS_TOKEN}",
                json={"recipient": {"id": sender_id}, "message": {"text": ai_reply}}
            )
            socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai", "channel": "instagram"})
            if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
                socketio.emit("handoff", {"conversation_id": convo_id, "agent": "unassigned", "user": sender_id, "channel": "instagram"})
    return "EVENT_RECEIVED", 200

@app.route("/instagram", methods=["GET"])
def instagram_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.getenv("VERIFY_TOKEN", "mysecretverifytoken"):
        return challenge, 200
    return "Verification failed", 403

@app.route("/send-welcome", methods=["POST"])
def send_welcome():
    data = request.get_json()
    to_number = data.get("to_number")
    user_name = data.get("user_name", "Guest")
    if not to_number:
        return jsonify({"error": "Missing to_number"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, opted_in FROM conversations WHERE username = ? AND channel = ?", (to_number, "whatsapp"))
    convo = c.fetchone()
    if not convo or not convo[1]:
        conn.close()
        return jsonify({"error": "User not opted in"}), 403
    convo_id = convo[0]
    conn.close()
    try:
        twilio_client.messages.create(
            body=f"Welcome to our hotel, {user_name}! We're here to assist with your bookings. Reply 'BOOK' to start or 'HELP' for assistance.",
            from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
            to=to_number
        )
        log_message(convo_id, "AI", f"Welcome to our hotel, {user_name}!", "ai")
        socketio.emit("new_message", {"convo_id": convo_id, "message": f"Welcome to our hotel, {user_name}!", "sender": "ai", "channel": "whatsapp"})
        return jsonify({"message": "Welcome message sent"}), 200
    except Exception as e:
        print(f"Twilio error: {e}")
        return jsonify({"error": "Failed to send message"}), 500

@app.route("/handoff", methods=["POST"])
@login_required
def handoff():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    if not convo_id:
        return jsonify({"message": "Missing conversation ID"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE conversations SET assigned_agent = ? WHERE id = ?", (current_user.username, convo_id))
    c.execute("SELECT username, channel FROM conversations WHERE id = ?", (convo_id,))
    username, channel = c.fetchone()
    conn.commit()
    conn.close()
    socketio.emit("handoff", {"conversation_id": convo_id, "agent": current_user.username, "user": username, "channel": channel})
    return jsonify({"message": f"Chat assigned to {current_user.username}"})

@app.route("/")
def index():
    return render_template("dashboard.html")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
