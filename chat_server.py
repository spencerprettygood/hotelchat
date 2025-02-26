from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import openai
import sqlite3
import os

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = "supersecretkey"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Setup Login Manager
login_manager = LoginManager()
login_manager.init_app(app)

openai.api_key = os.getenv("OPENAI_API_KEY")
DB_NAME = "chatbot.db"

def initialize_database():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Create the conversations table if it doesn't exist
    c.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            latest_message TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create the messages table if it doesn't exist
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            message TEXT NOT NULL,
            sender TEXT NOT NULL,
            channel TEXT NOT NULL,
            client_name TEXT,
            client_contact TEXT,
            stay_date TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

# Call the function to ensure tables are created
initialize_database()


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
    if "username" not in data or "password" not in data:
        return jsonify({"message": "Missing username or password"}), 400

    username = data["username"]
    password = data["password"]
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, username FROM agents WHERE username = ? AND password = ?", (username, password))
        agent = c.fetchone()
        conn.close()
        if agent:
            return jsonify({"message": "Login successful", "agent": agent[1]})
        else:
            return jsonify({"message": "Invalid credentials"}), 401
    except sqlite3.Error as e:
        return jsonify({"message": f"Database error: {str(e)}"}), 500

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"message": "Logged out successfully"})

@app.route("/conversations")
def get_conversations():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username, latest_message FROM conversations ORDER BY last_updated DESC")
    conversations = [
        {"id": row[0], "username": row[1], "latest_message": row[2], "initials": row[1][0].upper()}
        for row in c.fetchall()
    ]
    conn.close()
    return jsonify(conversations)

# ✅ Store message in database
def log_message(user, message, sender, channel, client_name=None, client_contact=None, stay_date=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""INSERT INTO messages (user, message, sender, channel, client_name, client_contact, stay_date)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (user, message, sender, channel, client_name, client_contact, stay_date))
    conn.commit()
    conn.close()

# ✅ Chat API - AI + Human Handoff Detection
@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    user_id = request.json.get("user_id", "Anonymous")
    channel = request.json.get("channel", "WhatsApp")  # Default to WhatsApp if not specified
    client_name = request.json.get("name")
    client_contact = request.json.get("contact")
    stay_date = request.json.get("stay_date")

    # Store user message
    log_message(user_id, user_message, "user", channel, client_name, client_contact, stay_date)

    # AI Response
    response = openai.ChatCompletion.create(
        model="gpt-4-turbo",
        messages=[
            {"role": "system", "content": "You are John, a helpful casino assistant. Keep responses short and clear."},
            {"role": "user", "content": user_message},
        ],
    )

    ai_reply = response.choices[0].message["content"]

    # Store AI response
    log_message(user_id, ai_reply, "ai", channel)

    # Check for handoff trigger words
    handoff_trigger = any(word in user_message.lower() for word in ["human", "help me", "real person", "talk to an agent"])

    if handoff_trigger:
        socketio.emit("handoff", {"user": user_id, "message": "AI suggests human takeover!"})

    # Emit message to dashboard
    socketio.emit("new_message", {"user": user_id, "message": ai_reply, "sender": "ai", "channel": channel})

    return jsonify({"reply": ai_reply})

# ✅ Fetch Chat History for a Specific User
@app.route("/messages", methods=["GET"])
@login_required
def get_messages():
    user_id = request.args.get("user")  # Get conversation ID from frontend
    if not user_id:
        return jsonify({"error": "Missing user ID"}), 400

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT message, sender, timestamp FROM messages 
        WHERE user = ? ORDER BY timestamp ASC
    """, (user_id,))
    
    messages = [{"message": row[0], "sender": row[1], "timestamp": row[2]} for row in c.fetchall()]
    conn.close()
    
    return jsonify(messages)


@app.route("/handoff", methods=["POST"])
@login_required
def handoff():
    data = request.json
    user_id = data["user_id"]
    agent_name = current_user.username

    # Store agent takeover message
    log_message(user_id, f"{agent_name} has taken over this conversation.", "agent", "manual")

    # Notify frontend of human handoff
    socketio.emit("handoff", {"user": user_id, "agent": agent_name})

    return jsonify({"message": "Handoff initiated"})

@app.route("/assign_chat", methods=["POST"])
@login_required
def assign_chat():
    data = request.get_json()
    convo_id = data.get("convo_id")

    if not convo_id:
        return jsonify({"message": "Missing conversation ID"}), 400

    agent_name = current_user.username

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE conversations SET assigned_agent = ? WHERE id = ?", (agent_name, convo_id))
    conn.commit()
    conn.close()

    # Emit real-time update to all connected agents
    socketio.emit("chat_assigned", {"convo_id": convo_id, "agent": agent_name})

    return jsonify({"message": f"Conversation {convo_id} assigned to {agent_name}."})


@app.route("/")
def index():
    return render_template("dashboard.html")

def add_test_conversations():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Ensure the table exists
    c.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            latest_message TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            assigned_agent TEXT DEFAULT NULL
        )
    ''')

    # Insert sample conversations if none exist
    c.execute("SELECT COUNT(*) FROM conversations")
    count = c.fetchone()[0]

    if count == 0:
        sample_data = [
            ("John Doe", "Hello, I need help with a booking."),
            ("Alice Smith", "Can I get a refund?"),
            ("Michael Johnson", "How do I deposit money?"),
        ]
        c.executemany("INSERT INTO conversations (username, latest_message) VALUES (?, ?)", sample_data)
        conn.commit()

    conn.close()

# Call function to add test data
add_test_conversations()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
