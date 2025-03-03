from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import openai
import sqlite3
import os

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecretkey")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')  # Explicitly use eventlet

# Setup Login Manager
login_manager = LoginManager()
login_manager.init_app(app)

openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("⚠️ OPENAI_API_KEY not set in environment variables")

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
            assigned_agent TEXT DEFAULT NULL)''')
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
    c.execute("SELECT id, username, latest_message, assigned_agent FROM conversations ORDER BY last_updated DESC")
    conversations = [{"id": row[0], "username": row[1], "latest_message": row[2], "assigned_agent": row[3]} for row in c.fetchall()]
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
    conn.commit()
    conn.close()

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    convo_id = data.get("conversation_id")
    user_message = data.get("message")
    if not convo_id or not user_message:
        return jsonify({"error": "Missing required fields"}), 400
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username FROM conversations WHERE id = ?", (convo_id,))
    username = c.fetchone()[0]
    conn.close()
    log_message(convo_id, username, user_message, "user")
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a hotel chatbot. Answer guest questions and escalate to a human if the query is complex or requires personal assistance."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=150
        )
        ai_reply = response.choices[0].message["content"].strip()
    except Exception as e:
        ai_reply = "Sorry, I couldn’t process that. Let me get a human to assist you."
        print(f"OpenAI error: {e}")
    log_message(convo_id, "AI", ai_reply, "ai")
    socketio.emit("new_message", {"convo_id": convo_id, "message": ai_reply, "sender": "ai"})
    if "human" in ai_reply.lower() or "sorry" in ai_reply.lower():
        socketio.emit("handoff", {"conversation_id": convo_id, "agent": "unassigned", "user": username})
    return jsonify({"reply": ai_reply})

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
    c.execute("SELECT username FROM conversations WHERE id = ?", (convo_id,))
    username = c.fetchone()[0]
    conn.commit()
    conn.close()
    socketio.emit("handoff", {"conversation_id": convo_id, "agent": current_user.username, "user": username})
    return jsonify({"message": f"Chat assigned to {current_user.username}"})

@app.route("/")
def index():
    return render_template("dashboard.html")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
