# Gevent monkey-patching at the very top
import gevent
from gevent import monkey
monkey.patch_all()

# Now proceed with other imports
from flask import Flask, render_template, request, jsonify, session, redirect, Response, g
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import psycopg2
from psycopg2.extras import DictCursor
from psycopg2.pool import SimpleConnectionPool
import os
import requests
import json
from datetime import datetime, timezone, timedelta
import time
import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from cachetools import TTLCache
import openai
from openai import OpenAI
# Update error imports to match OpenAI v1.x package structure
from openai.types.error import RateLimitError, APIError, AuthenticationError
from openai.types.timeout_error import APITimeoutError
import redis as sync_redis  # Add synchronous Redis client
from concurrent_log_handler import ConcurrentRotatingFileHandler
from langdetect import detect, DetectorFactoryotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
DetectorFactory.seed = 0
DetectorFactory.seed = 0
# Enhanced Logging Configuration
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "chat_server.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()erver.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Create a logger instance. Using "chat_server" to potentially share with tasks.py logger.
logger = logging.getLogger("chat_server")erver" to potentially share with tasks.py logger.
logger.setLevel(LOG_LEVEL)("chat_server")
logger.propagate = False # Prevent double logging if root logger is also configured
logger.propagate = False # Prevent double logging if root logger is also configured
# Clear existing handlers to avoid duplicate logs if this script is reloaded
if logger.hasHandlers():s to avoid duplicate logs if this script is reloaded
    logger.handlers.clear()
    logger.handlers.clear()
# Formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(funcName)s - %(message)s'
)   '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(funcName)s - %(message)s'
)
# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)()
logger.addHandler(console_handler)tter)
logger.addHandler(console_handler)
# File Handler (Rotating)
file_handler = ConcurrentRotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8') # 10MB per file, 5 backups
file_handler.setFormatter(formatter)eHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8') # 10MB per file, 5 backups
logger.addHandler(file_handler)tter)
logger.addHandler(file_handler)
logger.info(f"Logging initialized. Level: {LOG_LEVEL}, File: {LOG_FILE_PATH}")
logger.info(f"Logging initialized. Level: {LOG_LEVEL}, File: {LOG_FILE_PATH}")
# Validate critical environment variables
database_url = os.getenv("DATABASE_URL")s
if not database_url:tenv("DATABASE_URL")
    logger.error("❌ DATABASE_URL environment variable is not set")
    raise ValueError("DATABASE_URL environment variable is not set")
    raise ValueError("DATABASE_URL environment variable is not set")
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:tenv("SECRET_KEY")
    logger.error("⚠️ SECRET_KEY not set in environment variables")
    raise ValueError("SECRET_KEY not set") environment variables")
    raise ValueError("SECRET_KEY not set")
SERVER_URL = os.getenv("SERVER_URL", "https://hotel-chatbot-1qj5.onrender.com")
SERVER_URL = os.getenv("SERVER_URL", "https://hotel-chatbot-1qj5.onrender.com")
# Redis clients
# Synchronous Redis client for Flask routes
redis_client = sync_redis.Redis.from_url(es
    os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    decode_responses=True, 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    max_connections=20rue,
)   max_connections=20
)
# Async Redis client for ai_respond
async_redis_client = redis.Redis.from_url(
    os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    decode_responses=True, 'redis://red-cvfhn5nnoe9s73bhmct0:6379'),
    max_connections=20rue,
)   max_connections=20
)
# Simplified Redis sync functions
def redis_get_sync(key):functions
    request_start_time = time.time()
    logger.debug(f"[Redis GET] Key: {key}")
    try:er.debug(f"[Redis GET] Key: {key}")
        value = redis_client.get(key)
        processing_time = (time.time() - request_start_time) * 1000
        if value is not None:me.time() - request_start_time) * 1000
            logger.debug(f"[Redis HIT] Key: {key} - Time: {processing_time:.2f}ms")
        else:ogger.debug(f"[Redis HIT] Key: {key} - Time: {processing_time:.2f}ms")
            logger.debug(f"[Redis MISS] Key: {key} - Time: {processing_time:.2f}ms")
        return valueebug(f"[Redis MISS] Key: {key} - Time: {processing_time:.2f}ms")
    except redis.exceptions.RedisError as e: # Catch specific Redis errors
        processing_time = (time.time() - request_start_time) * 1000 errors
        logger.error(f"[Redis ERROR] GET Key: {key} - Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
        return Noner(f"[Redis ERROR] GET Key: {key} - Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
    except Exception as e:
        processing_time = (time.time() - request_start_time) * 1000
        logger.error(f"[Redis ERROR] GET Key: {key} - Unexpected Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
        return Noner(f"[Redis ERROR] GET Key: {key} - Unexpected Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
        return None
def redis_setex_sync(key, ttl, value):
    request_start_time = time.time()):
    value_snippet = str(value)[:100] + ("..." if len(str(value)) > 100 else "")
    logger.debug(f"[Redis SETEX] Key: {key}, TTL: {ttl}, Value Snippet: '{value_snippet}'")
    try:er.debug(f"[Redis SETEX] Key: {key}, TTL: {ttl}, Value Snippet: '{value_snippet}'")
        redis_client.setex(key, ttl, value)
        processing_time = (time.time() - request_start_time) * 1000
        logger.debug(f"[Redis SETEX Success] Key: {key} - Time: {processing_time:.2f}ms")
    except redis.exceptions.RedisError as e: Key: {key} - Time: {processing_time:.2f}ms")
        processing_time = (time.time() - request_start_time) * 1000
        logger.error(f"[Redis ERROR] SETEX Key: {key} - Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
    except Exception as e:dis ERROR] SETEX Key: {key} - Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
        processing_time = (time.time() - request_start_time) * 1000
        logger.error(f"[Redis ERROR] SETEX Key: {key} - Unexpected Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
        logger.error(f"[Redis ERROR] SETEX Key: {key} - Unexpected Error: {e} - Time: {processing_time:.2f}ms", exc_info=True)
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config["SECRET_KEY"] = SECRET_KEYstatic', template_folder='templates')
CORS(app)g["SECRET_KEY"] = SECRET_KEY
CORS(app)
socketio = SocketIO(
    app, = SocketIO(
    cors_allowed_origins=["http://localhost:5000", "https://hotel-chatbot-1qj5.onrender.com"],
    async_mode="gevent",=["http://localhost:5000", "https://hotel-chatbot-1qj5.onrender.com"],
    message_queue=os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'), # Added message queue for Celery
    ping_timeout=60,.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379'), # Added message queue for Celery
    ping_interval=15,
    logger=True,l=15,
    engineio_logger=True
)   engineio_logger=True
)
login_manager = LoginManager()
login_manager.init_app(app)r()
login_manager.login_view = 'login'
login_manager.login_view = 'login'
# Initialize connection pool
try:itialize connection pool
    database_url = database_url.replace("postgres://", "postgresql://", 1)
    logger.info(f"Using DATABASE_URL: {database_url}") "postgresql://", 1)
except NameError:"Using DATABASE_URL: {database_url}")
    logger.error("❌ DATABASE_URL environment variable not set")
    raise ValueError("DATABASE_URL environment variable not set")
    raise ValueError("DATABASE_URL environment variable not set")
# Use sslmode=require
if "sslmode" not in database_url:
    database_url += "?sslmode=require"
    logger.info(f"Added sslmode=require to DATABASE_URL: {database_url}")
    logger.info(f"Added sslmode=require to DATABASE_URL: {database_url}")
db_pool = SimpleConnectionPool(
    minconn=1,  # Start with 1 connection
    maxconn=5,  # Limit to 5 connections to avoid overloading
    dsn=database_url,it to 5 connections to avoid overloading
    sslmode="require",  # Enforce SSL
    sslrootcert=None,  # Let psycopg2 handle SSL certificates
    connect_timeout=10,  # 10-second timeout for connectionss
    options="-c statement_timeout=10000"  # Set a 10-second statement timeout
)   options="-c statement_timeout=10000"  # Set a 10-second statement timeout
logger.info("✅ Database connection pool initialized with minconn=1, maxconn=5, connect_timeout=10")
logger.info("✅ Database connection pool initialized with minconn=1, maxconn=5, connect_timeout=10")
# Cache for ai_enabled setting with 5-second TTL
settings_cache = TTLCache(maxsize=1, ttl=5) # type: ignore
settings_cache = TTLCache(maxsize=1, ttl=5) # type: ignore
# Import tasks after app and logger are initialized to avoid circular imports
# logger.debug("Importing tasks module...")tialized to avoid circular imports
# from tasks import process_whatsapp_message, send_whatsapp_message_task # Commented out global import
# logger.debug("Tasks module imported.")sage, send_whatsapp_message_task # Commented out global import
# logger.debug("Tasks module imported.")
# Validate OpenAI API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:tenv("OPENAI_API_KEY")
    logger.error("⚠️ OPENAI_API_KEY not set in environment variables")
    raise ValueError("OPENAI_API_KEY not set") environment variables")
    raise ValueError("OPENAI_API_KEY not set")
# Initialize OpenAI client with a timeout
logger.info("Initializing OpenAI client...")
openai_client = OpenAI(
    api_key=OPENAI_API_KEY,
    timeout=30.0
)
    api_key=OPENAI_API_KEY,(
    timeout=30.0AI_API_KEY,
)   timeout=30.0
logger.info("OpenAI client initialized.")
logger.info("OpenAI client initialized.")
# Semaphore for controlling concurrent OpenAI API calls
OPENAI_CONCURRENCY = int(os.getenv("OPENAI_CONCURRENCY", "5"))
logger.info(f"OpenAI concurrency limit: {OPENAI_CONCURRENCY}") "5"))
logger.info(f"OpenAI concurrency semaphore initialized with limit: {OPENAI_CONCURRENCY}")
logger.info(f"OpenAI concurrency semaphore initialized with limit: {OPENAI_CONCURRENCY}")
# Define the AI response function
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError, APIError))
)
def get_ai_response(convo_id, username, conversation_history, user_message, chat_id, channel, language="en"):
    """
    Generates an AI response using the synchronous OpenAI client.
    This version is compatible with gevent and Celery.
    """
    start_time_ai = time.time()
    logger.info(f"AI_RESPOND initiated for convo_id: {convo_id}, user: {username}. Message: '{user_message[:50]}...'")

    # Ensure history is a list of dicts, and add the latest user message
    if not isinstance(conversation_history, list):
        conversation_history = []
    if not conversation_history or conversation_history[-1].get("content") != user_message:
        conversation_history.append({"role": "user", "content": user_message})
    
    # Add the current user message to the history if it's not already the last message
    if not conversation_history or conversation_history[-1].get("content") != user_message or conversation_history[-1].get("role") != "user":
        conversation_history.append({"role": "user", "content": user_message})
    
    # Limit history length to avoid excessive token usage (e.g., last 10 messages)
    MAX_HISTORY_LEN = 10
    if len(conversation_history) > MAX_HISTORY_LEN:
        conversation_history = conversation_history[-MAX_HISTORY_LEN:]
        logger.debug(f"Trimmed conversation history to last {MAX_HISTORY_LEN} messages for convo_id {convo_id}")
    
    system_prompt = f"You are a helpful assistant for Amapola Resort. Current language for response: {language}. Training document: {TRAINING_DOCUMENT[:200]}..." # Truncate for logging
    messages_for_openai = [
        {"role": "system", "content": system_prompt}
    ] + conversation_history
    
    ai_reply = None
    detected_intent = None # Placeholder for intent detection
    handoff_triggered = False # Placeholder for handoff logic
    
    request_start_time = time.time()
    logger.info(f"[OpenAI Request] Convo ID: {convo_id} - Model: gpt-4o-mini - User Message: '{user_message[:100]}...'") # Log request details
    logger.debug(f"[OpenAI Request Details] Convo ID: {convo_id} - Full History: {conversation_history}")
    
    try:
        logger.info(f"Calling OpenAI API for convo_id {convo_id}. Model: gpt-4o-mini. History length: {len(messages_for_openai)}")
        
        # Call OpenAI API synchronously
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", # Using the specified model
            messages=messages_for_openai,
            max_tokens=300, # Max tokens for the response
            temperature=0.7
        )
        
        ai_reply = response.choices[0].message.content.strip()
        usage = response.usage
        processing_time = (time.time() - request_start_time) * 1000
        logger.info(
            f"[OpenAI Response] Convo ID: {convo_id} - Tokens: P{usage.prompt_tokens}/C{usage.completion_tokens} - Reply: '{ai_reply[:50]}...'"
        )
        logger.debug(f"[OpenAI Response Details] Convo ID: {convo_id} - Full Reply: {ai_reply} - Full Response Object: {response.model_dump_json(indent=2)}")
        
        # Basic intent detection (example - can be expanded)
        if "book a room" in user_message.lower() or "reservation" in user_message.lower():
            detected_intent = "booking_inquiry"
        if "human" in user_message.lower() or "agent" in user_message.lower() or "speak to someone" in user_message.lower():
            handoff_triggered = True
            logger.info(f"Handoff to human agent triggered by user message for convo_id {convo_id}")
            
    except (RateLimitError, APITimeoutError, APIError, AuthenticationError) as e:
        logger.error(f"❌ OpenAI API Error for convo_id {convo_id}: {e}", exc_info=True)
        # Return a user-friendly error and allow unpacking
        return "I'm having some trouble connecting to my systems right now. Please give me a moment and try again.", None, False
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_ai_response for convo_id {convo_id}: {e}", exc_info=True)
        # Return a user-friendly error and allow unpacking
        return "An unexpected error occurred. I've logged the issue for review.", None, False
    
    # No need for this section as we're returning directly in the try/except blocks
    return ai_reply, detected_intent, handoff_triggered

# Google Calendar setup with Service Account
GOOGLE_SERVICE_ACCOUNT_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
if not GOOGLE_SERVICE_ACCOUNT_KEY:tenv("GOOGLE_SERVICE_ACCOUNT_KEY")
    logger.error("⚠️ GOOGLE_SERVICE_ACCOUNT_KEY not set in environment variables")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set") environment variables")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")
try:
    service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_KEY)
except json.JSONDecodeError as e:oads(GOOGLE_SERVICE_ACCOUNT_KEY)
    logger.error(f"⚠️ Invalid GOOGLE_SERVICE_ACCOUNT_KEY format: {e}")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY must be a valid JSON string")
    raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY must be a valid JSON string")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
credentials = service_account.Credentials.from_service_account_info(
    service_account_info, scopes=SCOPESls.from_service_account_info(
)   service_account_info, scopes=SCOPES
service = build('calendar', 'v3', credentials=credentials)
service = build('calendar', 'v3', credentials=credentials)
# Messaging API tokens
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
if not TWILIO_ACCOUNT_SID:s.getenv("TWILIO_WHATSAPP_NUMBER")
    logger.error("⚠️ TWILIO_ACCOUNT_SID not set in environment variables")
    raise ValueError("TWILIO_ACCOUNT_SID not set") environment variables")
if not TWILIO_AUTH_TOKEN:LIO_ACCOUNT_SID not set")
    logger.error("⚠️ TWILIO_AUTH_TOKEN not set in environment variables")
    raise ValueError("TWILIO_AUTH_TOKEN not set") environment variables")
if not TWILIO_WHATSAPP_NUMBER:UTH_TOKEN not set")
    logger.error("⚠️ TWILIO_WHATSAPP_NUMBER not set in environment variables")
    raise ValueError("TWILIO_WHATSAPP_NUMBER not set") environment variables")
    raise ValueError("TWILIO_WHATSAPP_NUMBER not set")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN")
if not WHATSAPP_API_TOKEN:tenv("WHATSAPP_API_TOKEN")
    logger.warning("⚠️ WHATSAPP_API_TOKEN not set, some WhatsApp features may not work")
WHATSAPP_API_URL = "https://api.whatsapp.com" set, some WhatsApp features may not work")
WHATSAPP_API_URL = "https://api.whatsapp.com"
# Load or define the Q&A reference document
try:ad or define the Q&A reference document
    logger.debug("Attempting to load qa_reference.txt")
    with open("qa_reference.txt", "r", encoding='utf-8') as file: # Added encoding
        TRAINING_DOCUMENT = file.read()encoding='utf-8') as file: # Added encoding
    logger.info("✅ Loaded Q&A reference document")
except FileNotFoundError: Q&A reference document")
    logger.warning("⚠️ qa_reference.txt not found, using default training document")
    TRAINING_DOCUMENT = """eference.txt not found, using default training document")
    **Amapola Resort Chatbot Training Document**
    **Amapola Resort Chatbot Training Document**
    You are a friendly and professional chatbot for Amapola Resort, a luxury beachfront hotel. Your role is to assist guests with inquiries, help with bookings, and provide information about the resort’s services and amenities. Below is a set of common questions and answers to guide your responses. Always maintain conversation context, ask follow-up questions to clarify user intent, and provide helpful, concise answers. If a query is too complex or requires human assistance (e.g., specific booking modifications, complaints, or detailed itinerary planning), escalate to a human by saying: "I’m sorry, that’s a bit complex for me to handle. Let me get a human to assist you."
    You are a friendly and professional chatbot for Amapola Resort, a luxury beachfront hotel. Your role is to assist guests with inquiries, help with bookings, and provide information about the resort’s services and amenities. Below is a set of common questions and answers to guide your responses. Always maintain conversation context, ask follow-up questions to clarify user intent, and provide helpful, concise answers. If a query is too complex or requires human assistance (e.g., specific booking modifications, complaints, or detailed itinerary planning), escalate to a human by saying: "I’m sorry, that’s a bit complex for me to handle. Let me get a human to assist you."
    **Business Information**
    - **Location**: Amapola Resort, 123 Ocean Drive, Sunny Beach, FL 33160
    - **Check-In/Check-Out**: Check-in at 3:00 PM, Check-out at 11:00 AM60
    - **Room Types**:k-Out**: Check-in at 3:00 PM, Check-out at 11:00 AM
      - Standard Room: $150/night, 2 guests, 1 queen bed
      - Deluxe Room: $250/night, 4 guests, 2 queen beds, ocean view
      - Suite: $400/night, 4 guests, 1 king bed, living area, oceanfront balcony
    - **Amenities**:night, 4 guests, 1 king bed, living area, oceanfront balcony
      - Beachfront access, outdoor pool, spa, gym, on-site restaurant (Amapola Bistro), free Wi-Fi, parking ($20/day)
    - **Activities**:cess, outdoor pool, spa, gym, on-site restaurant (Amapola Bistro), free Wi-Fi, parking ($20/day)
      - Snorkeling ($50/person), kayak rentals ($30/hour), sunset cruises ($100/person)
    - **Policies**:($50/person), kayak rentals ($30/hour), sunset cruises ($100/person)
      - Cancellation: Free cancellation up to 48 hours before arrival
      - Pets: Not allowede cancellation up to 48 hours before arrival
      - Children: Kids under 12 stay free with an adult
      - Children: Kids under 12 stay free with an adult
    **Common Q&A**
    **Common Q&A**
    Q: What are your room rates?
    A: We offer several room types:
    - Standard Room: $150/night for 2 guests
    - Deluxe Room: $250/night for 4 guests, with an ocean view
    - Suite: $400/night for 4 guests, with an oceanfront balcony
    Would you like to book a room, or do you have questions about a specific room type?
    Would you like to book a room, or do you have questions about a specific room type?
    Q: How do I book a room?
    A: I can help you start the booking process! Please let me know:
    1. Your preferred dates (e.g., check-in and check-out dates)now:
    2. The number of guests (e.g., check-in and check-out dates)
    3. Your preferred room type (Standard, Deluxe, or Suite)
    For example, you can say: "I’d like a Deluxe Room for 2 guests from March 10 to March 15." Once I have this information, I’ll check availability and guide you through the next steps. If you’d prefer to speak with a human to finalize your booking, let me know!
    For example, you can say: "I’d like a Deluxe Room for 2 guests from March 10 to March 15." Once I have this information, I’ll check availability and guide you through the next steps. If you’d prefer to speak with a human to finalize your booking, let me know!
    Q: What is the check-in time?
    A: Check-in at Amapola Resort is at 3:00 PM, and check-out is at 11:00 AM. If you need an early check-in or late check-out, I can check availability for you—just let me know your dates!
    A: Check-in at Amapola Resort is at 3:00 PM, and check-out is at 11:00 AM. If you need an early check-in or late check-out, I can check availability for you—just let me know your dates!
    Q: Do you have a pool?
    A: Yes, we have a beautiful outdoor pool with beachfront views! It’s open from 8:00 AM to 8:00 PM daily. We also have a spa and gym if you’re interested in other amenities. Would you like to know more?
    A: Yes, we have a beautiful outdoor pool with beachfront views! It’s open from 8:00 AM to 8:00 PM daily. We also have a spa and gym if you’re interested in other amenities. Would you like to know more?
    Q: Can I bring my pet?
    A: I’m sorry, but pets are not allowed at Amapola Resort. If you need recommendations for pet-friendly accommodations nearby, I can help you find some options!
    A: I’m sorry, but pets are not allowed at Amapola Resort. If you need recommendations for pet-friendly accommodations nearby, I can help you find some options!
    Q: What activities do you offer?
    A: We have a variety of activities for our guests:
    - Snorkeling: $50 per personvities for our guests:
    - Kayak rentals: $30 per hour
    - Sunset cruises: $100 per person
    Would you like to book an activity, or do you have questions about any of these?
    Would you like to book an activity, or do you have questions about any of these?
    Q: What are the cancellation policies?
    A: You can cancel your reservation for free up to 48 hours before your arrival. After that, you may be charged for the first night. If you need to modify or cancel a booking, I can get a human to assist you with the details.
    A: You can cancel your reservation for free up to 48 hours before your arrival. After that, you may be charged for the first night. If you need to modify or cancel a booking, I can get a human to assist you with the details.
    Q: Do you have a restaurant?
    A: Yes, Amapola Bistro is our on-site restaurant, serving breakfast, lunch, and dinner with a focus on fresh seafood and local flavors. It’s open from 7:00 AM to 10:00 PM. Would you like to make a reservation or see the menu?
    A: Yes, Amapola Bistro is our on-site restaurant, serving breakfast, lunch, and dinner with a focus on fresh seafood and local flavors. It’s open from 7:00 AM to 10:00 PM. Would you like to make a reservation or see the menu?
    **Conversational Guidelines**
    - Always greet new users with: "Thank you for contacting us."
    - For follow-up messages, do not repeat the greeting. Instead, respond based on the context of the conversation.
    - Ask clarifying questions if the user’s intent is unclear (e.g., "Could you tell me your preferred dates for booking?").
    - Use a friendly and professional tone, and keep responses concise (under 150 tokens, as set by max_tokens).r booking?").
    - If the user asks multiple questions in one message, address each question systematically.t by max_tokens).
    - If the user provides partial information (e.g., "I want to book a room"), ask for missing details (e.g., dates, number of guests, room type).
    - If a query is ambiguous, ask for clarification (e.g., "Did you mean you’d like to book a room, or are you asking about our rates?").om type).
    - Escalate to a human for complex requests, such as modifying an existing booking, handling complaints, or providing detailed recommendations.
    """scalate to a human for complex requests, such as modifying an existing booking, handling complaints, or providing detailed recommendations.
    logger.warning("⚠️ qa_reference.txt not found, using default training document")
    logger.warning("⚠️ qa_reference.txt not found, using default training document")
def get_db_connection():
    global db_poolion():
    try:al db_pool
        conn = db_pool.getconn()
        if conn.closed:getconn()
            logger.warning("Connection retrieved from pool is closed, reinitializing pool")
            db_pool.closeall()nnection retrieved from pool is closed, reinitializing pool")
            db_pool = SimpleConnectionPool(
                minconn=1,leConnectionPool(
                maxconn=5,
                dsn=database_url,
                sslmode="require",
                sslrootcert=None,,
                connect_timeout=10,
                options="-c statement_timeout=10000"
            )   options="-c statement_timeout=10000"
            conn = db_pool.getconn()
        # Test the connection with a simple query
        with conn.cursor() as c:th a simple query
            c.execute("SELECT 1")
        conn.cursor_factory = DictCursor
        logger.info("✅ Retrieved database connection from pool")
        return conn("✅ Retrieved database connection from pool")
    except Exception as e:
        logger.error(f"❌ Failed to get database connection: {str(e)}", exc_info=True)
        # If the error is SSL-related, reinitialize the pool{str(e)}", exc_info=True)
        error_str = str(e).lower()ted, reinitialize the pool
        if any(err in error_str for err in ["ssl syscall error", "eof detected", "decryption failed", "bad record mac"]):
            logger.warning("SSL error detected, reinitializing connection pool") "decryption failed", "bad record mac"]):
            try:er.warning("SSL error detected, reinitializing connection pool")
                db_pool.closeall()
                db_pool = SimpleConnectionPool(
                    minconn=1,leConnectionPool(
                    maxconn=5,
                    dsn=database_url,
                    sslmode="require",
                    sslrootcert=None,,
                    connect_timeout=10,
                    options="-c statement_timeout=10000"
                )   options="-c statement_timeout=10000"
                conn = db_pool.getconn()
                with conn.cursor() as c:
                    c.execute("SELECT 1")
                conn.cursor_factory = DictCursor
                logger.info("✅ Reinitialized database connection pool and retrieved new connection")
                return conn("✅ Reinitialized database connection pool and retrieved new connection")
            except Exception as e2:
                logger.error(f"❌ Failed to reinitialize database connection pool: {str(e2)}", exc_info=True)
                raiser.error(f"❌ Failed to reinitialize database connection pool: {str(e2)}", exc_info=True)
        raise e raise
        raise e
def release_db_connection(conn):
    global db_poolnection(conn):
    if conn:b_pool
        try:
            if conn.closed:
                logger.warning("Attempted to release a closed connection")
            else:ogger.warning("Attempted to release a closed connection")
                db_pool.putconn(conn)
                logger.info("✅ Database connection returned to pool")
        except Exception as e: Database connection returned to pool")
            logger.error(f"❌ Failed to return database connection to pool: {str(e)}")
            logger.error(f"❌ Failed to return database connection to pool: {str(e)}")
def with_db_retry(func):
    """Decorator to retry database operations on failure."""
    def wrapper(*args, **kwargs):e operations on failure."""
        retries = 5gs, **kwargs):
        for attempt in range(retries):
            try:mpt in range(retries):
                return func(*args, **kwargs)
            except Exception as e: **kwargs)
                logger.error(f"❌ Database operation failed (Attempt {attempt + 1}/{retries}): {str(e)}")
                error_str = str(e).lower()operation failed (Attempt {attempt + 1}/{retries}): {str(e)}")
                if any(err in error_str for err in ["ssl syscall error", "eof detected", "decryption failed", "bad record mac", "connection already closed"]):
                    global db_poolr_str for err in ["ssl syscall error", "eof detected", "decryption failed", "bad record mac", "connection already closed"]):
                    try:al db_pool
                        db_pool.closeall()
                        db_pool = SimpleConnectionPool(
                            minconn=5,leConnectionPool(
                            maxconn=30,
                            dsn=database_url,
                            sslmode="require",
                            sslrootcert=None",
                        )   sslrootcert=None
                        logger.info("✅ Reinitialized database connection pool due to SSL or connection error")
                    except Exception as e2:itialized database connection pool due to SSL or connection error")
                        logger.error(f"❌ Failed to reinitialize database connection pool: {str(e2)}")
                if attempt < retries - 1:Failed to reinitialize database connection pool: {str(e2)}")
                    time.sleep(2)ies - 1:
                    continueep(2)
                raise etinue
    return wrapperise e
    return wrapper
# Cache the ai_enabled setting
@with_db_retry_enabled setting
def get_ai_enabled():
    if "ai_enabled" in settings_cache:
        cached_value, cached_timestamp = settings_cache["ai_enabled"]
        logger.debug(f"CACHE HIT: ai_enabled='{cached_value}', timestamp='{cached_timestamp}'")
        return cached_value, cached_timestamp'{cached_value}', timestamp='{cached_timestamp}'")
    try:return cached_value, cached_timestamp
        logger.debug("CACHE MISS: Fetching ai_enabled from database.")
        with get_db_connection() as conn:g ai_enabled from database.")
            c = conn.cursor()n() as conn:
            c.execute("SELECT value, last_updated FROM settings WHERE key = %s", ("ai_enabled",))
            result = c.fetchone()ue, last_updated FROM settings WHERE key = %s", ("ai_enabled",))
            global_ai_enabled = result['value'] if result else "1"
            ai_toggle_timestamp = result['last_updated'] if result else "1970-01-01T00:00:00Z"
            settings_cache["ai_enabled"] = (global_ai_enabled, ai_toggle_timestamp)T00:00:00Z"
            logger.info(f"✅ Cached ai_enabled: {global_ai_enabled}, last_updated: {ai_toggle_timestamp}")
            # release_db_connection(conn) # Removed as 'with' statement handles it{ai_toggle_timestamp}")
        return settings_cache["ai_enabled"] Removed as 'with' statement handles it
    except Exception as e:che["ai_enabled"]
        logger.error(f"❌ Failed to fetch ai_enabled from database: {str(e)}", exc_info=True)
        return ("1", "1970-01-01T00:00:00Z")enabled from database: {str(e)}", exc_info=True)
        return ("1", "1970-01-01T00:00:00Z")
@with_db_retry
def init_db():
    logger.info("Initializing database")
    with get_db_connection() as conn:e")
        c = conn.cursor()n() as conn:
        c = conn.cursor()
        # Check if tables exist before creating them
        c.execute("""bles exist before creating them
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'conversations'les 
            )   WHERE table_name = 'conversations'
        """))
        conversations_table_exists = c.fetchone()[0]
        conversations_table_exists = c.fetchone()[0]
        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'messages'a.tables 
            )   WHERE table_name = 'messages'
        """))
        messages_table_exists = c.fetchone()[0]
        messages_table_exists = c.fetchone()[0]
        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'agents'ema.tables 
            )   WHERE table_name = 'agents'
        """))
        agents_table_exists = c.fetchone()[0]
        agents_table_exists = c.fetchone()[0]
        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'settings'a.tables 
            )   WHERE table_name = 'settings'
        """))
        settings_table_exists = c.fetchone()[0]
        settings_table_exists = c.fetchone()[0]
        # Create tables only if they don't exist
        if not conversations_table_exists: exist
            c.execute('''CREATE TABLE conversations (
                id SERIAL PRIMARY KEY,conversations (
                username TEXT NOT NULL,
                chat_id TEXT NOT NULL,,
                channel TEXT NOT NULL,
S               channel TEXT NOT NULL,
                assigned_agent TEXT,
                ai_enabled INTEGER DEFAULT 1,
                needs_agent INTEGER DEFAULT 0,
                booking_intent TEXT,DEFAULT 0,
                handoff_notified INTEGER DEFAULT 0,
                visible_in_conversations INTEGER DEFAULT 1,
                language TEXT DEFAULT 'en',TEGER DEFAULT 1,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )''')ast_updated TEXT DEFAULT CURRENT_TIMESTAMP
            logger.info("Created conversations table")
        else:ogger.info("Created conversations table")
            # Migration: Add needs_agent, language, and other columns if they don't exist
            c.execute("""Add needs_agent, language, and other columns if they don't exist
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'needs_agent'
                )   WHERE table_name = 'conversations' AND column_name = 'needs_agent'
            """))
            needs_agent_exists = c.fetchone()[0]
            if not needs_agent_exists:chone()[0]
                c.execute("ALTER TABLE conversations ADD COLUMN needs_agent INTEGER DEFAULT 0")
                logger.info("Added needs_agent column to conversations table")TEGER DEFAULT 0")
                logger.info("Added needs_agent column to conversations table")
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'language'
                )   WHERE table_name = 'conversations' AND column_name = 'language'
            """))
            language_exists = c.fetchone()[0]
            if not language_exists:chone()[0]
                c.execute("ALTER TABLE conversations ADD COLUMN language TEXT DEFAULT 'en'")
                logger.info("Added language column to conversations table")XT DEFAULT 'en'")
                logger.info("Added language column to conversations table")
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'booking_intent'
                )   WHERE table_name = 'conversations' AND column_name = 'booking_intent'
            """))
            booking_intent_exists = c.fetchone()[0]
            if not booking_intent_exists:chone()[0]
                c.execute("ALTER TABLE conversations ADD COLUMN booking_intent TEXT")
                logger.info("Added booking_intent column to conversations table")XT"
                logger.info("Added booking_intent column to conversations table")
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'conversations' AND column_name = 'handoff_notified'
                )   WHERE table_name = 'conversations' AND column_name = 'handoff_notified'
            """))
            handoff_notified_exists = c.fetchone()[0]
            if not handoff_notified_exists:chone()[0]
                c.execute("ALTER TABLE conversations ADD COLUMN handoff_notified INTEGER DEFAULT 0")
                logger.info("Added handoff_notified column to conversations table")TEGER DEFAULT 0")
                logger.info("Added handoff_notified column to conversations table")
        if not messages_table_exists:
            c.execute('''CREATE TABLE messages (
                id SERIAL PRIMARY KEY,messages (
                convo_id INTEGER NOT NULL,
                username TEXT NOT NULL,LL,
                message TEXT NOT NULL,,
                sender TEXT NOT NULL,,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (convo_id) REFERENCES conversations (id)
            )''')OREIGN KEY (convo_id) REFERENCES conversations (id)
            logger.info("Created messages table")
            logger.info("Created messages table")
        if not agents_table_exists:
            c.execute('''CREATE TABLE agents (
                id SERIAL PRIMARY KEY,agents (
                username TEXT NOT NULL UNIQUE
            )''')sername TEXT NOT NULL UNIQUE
            logger.info("Created agents table")
        else:ogger.info("Created agents table")
            # Migration: Drop password_hash and password columns if they exist
            c.execute("""Drop password_hash and password columns if they exist
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'agents' AND column_name = 'password_hash'
                )   WHERE table_name = 'agents' AND column_name = 'password_hash'
            """))
            password_hash_exists = c.fetchone()[0]
            if password_hash_exists:.fetchone()[0]
                c.execute("ALTER TABLE agents DROP COLUMN password_hash")
                logger.info("Dropped password_hash column from agents table")
                logger.info("Dropped password_hash column from agents table")
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'agents' AND column_name = 'password'
                )   WHERE table_name = 'agents' AND column_name = 'password'
            """))
            password_exists = c.fetchone()[0]
            if password_exists:.fetchone()[0]
                c.execute("ALTER TABLE agents DROP COLUMN password")
                logger.info("Dropped password column from agents table")
                logger.info("Dropped password column from agents table")
        if not settings_table_exists:
            c.execute('''CREATE TABLE settings (
                key TEXT PRIMARY KEY, settings (
                value TEXT NOT NULL,,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )''')ast_updated TEXT DEFAULT CURRENT_TIMESTAMP
            logger.info("Created settings table")
        else:ogger.info("Created settings table")
            # Migration: Add last_updated column if it doesn't exist
            c.execute("""Add last_updated column if it doesn't exist
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'settings' AND column_name = 'last_updated'
                )   WHERE table_name = 'settings' AND column_name = 'last_updated'
            """))
            last_updated_exists = c.fetchone()[0]
            if not last_updated_exists:chone()[0]
                c.execute("ALTER TABLE settings ADD COLUMN last_updated TEXT DEFAULT CURRENT_TIMESTAMP")
                logger.info("Added last_updated column to settings table")XT DEFAULT CURRENT_TIMESTAMP")
                logger.info("Added last_updated column to settings table")
        # Create indexes for frequently queried columns
        c.execute("CREATE INDEX IF NOT EXISTS idx_conversations_chat_id ON conversations (chat_id);")
        logger.info("Created index idx_conversations_chat_id")s_chat_id ON conversations (chat_id);")
        logger.info("Created index idx_conversations_chat_id")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_convo_id ON messages (convo_id);")
        logger.info("Created index idx_messages_convo_id")_convo_id ON messages (convo_id);")
        logger.info("Created index idx_messages_convo_id")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp);")
        logger.info("Created index idx_messages_timestamp")timestamp ON messages (timestamp);")
        logger.info("Created index idx_messages_timestamp")
        c.execute("CREATE INDEX IF NOT EXISTS idx_settings_key ON settings (key);")
        logger.info("Created index idx_settings_key")tings_key ON settings (key);")
        logger.info("Created index idx_settings_key")
        # Seed initial data (only in development or if explicitly enabled)
        if os.getenv("SEED_INITIAL_DATA", "false").lower() == "true":bled)
            c.execute("SELECT COUNT(*) FROM settings")er() == "true":
            if c.fetchone()[0] == 0:*) FROM settings")
                c.execute()[0] == 0:
                    "INSERT INTO settings (key, value, last_updated) VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING",
                    ('ai_enabled', '1', datetime.now(timezone.utc).isoformat()), %s, %s) ON CONFLICT (key) DO NOTHING",
                )   ('ai_enabled', '1', datetime.now(timezone.utc).isoformat())
                logger.info("Inserted default settings")
                logger.info("Inserted default settings")
            c.execute("SELECT COUNT(*) FROM agents")
            if c.fetchone()[0] == 0:*) FROM agents")
                c.execute()[0] == 0:
                    "INSERT INTO agents (username) VALUES (%s) ON CONFLICT (username) DO NOTHING",
                    ('admin',)TO agents (username) VALUES (%s) ON CONFLICT (username) DO NOTHING",
                )   ('admin',)
                logger.info("Inserted default admin user")
                logger.info("Inserted default admin user")
            c.execute("SELECT COUNT(*) FROM conversations WHERE channel = %s", ('whatsapp',))
            if c.fetchone()[0] == 0:*) FROM conversations WHERE channel = %s", ('whatsapp',))
                logger.info("ℹ️ Inserting test conversations")
                test_timestamp1 = "2025-03-22T00:00:00Z"ions")
                c.execute(tamp1 = "2025-03-22T00:00:00Z"
                    "INSERT INTO conversations (username, chat_id, channel, assigned_agent, ai_enabled, needs_agent, booking_intent, last_updated, language) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",igned_agent, ai_enabled, needs_agent, booking_intent, last_updated, language) "
                    ('TestUser1', '123456789', 'whatsapp', None, 1, 0, None, test_timestamp1, 'en')
                )   ('TestUser1', '123456789', 'whatsapp', None, 1, 0, None, test_timestamp1, 'en')
                convo_id1 = c.fetchone()['id']
                c.execute(= c.fetchone()['id']
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                    (convo_id1, 'TestUser1', 'Hello, I need help!', 'user', test_timestamp1)ALUES (%s, %s, %s, %s, %s)",
                )   (convo_id1, 'TestUser1', 'Hello, I need help!', 'user', test_timestamp1)
                test_timestamp2 = "2025-03-22T00:00:01Z"
                c.execute(tamp2 = "2025-03-22T00:00:01Z"
                    "INSERT INTO conversations (username, chat_id, channel, assigned_agent, ai_enabled, needs_agent, booking_intent, last_updated, language) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",igned_agent, ai_enabled, needs_agent, booking_intent, last_updated, language) "
                    ('TestUser2', '987654321', 'whatsapp', None, 1, 0, None, test_timestamp2, 'es')
                )   ('TestUser2', '987654321', 'whatsapp', None, 1, 0, None, test_timestamp2, 'es')
                convo_id2 = c.fetchone()['id']
                c.execute(= c.fetchone()['id']
                    "INSERT INTO messages (convo_id, username, message, sender, timestamp) VALUES (%s, %s, %s, %s, %s)",
                    (convo_id2, 'TestUser2', 'Hola, ¿puedo reservar una habitación?', 'user', test_timestamp2) %s, %s)",
                )   (convo_id2, 'TestUser2', 'Hola, ¿puedo reservar una habitación?', 'user', test_timestamp2)
                logger.info("Inserted test conversations")
                logger.info("Inserted test conversations")
        conn.commit()
        logger.info("✅ Database initialized")
        release_db_connection(conn)tialized")
        release_db_connection(conn)
@with_db_retry
def add_test_conversations():
    if os.getenv("SEED_INITIAL_DATA", "false").lower() != "true":
        logger.info("Skipping test conversations (SEED_INITIAL_DATA not enabled)")
        return.info("Skipping test conversations (SEED_INITIAL_DATA not enabled)")
    try:return
        with get_db_connection() as conn:
            c = conn.cursor()n() as conn:
            c.execute("SELECT COUNT(*) FROM conversations WHERE channel = %s", ('test',))
            count = c.fetchone()['count']OM conversations WHERE channel = %s", ('test',))
            if count == 0:hone()['count']
                for i in range(1, 6):
                    c.execute((1, 6):
                        "INSERT INTO conversations (username, chat_id, channel, ai_enabled, needs_agent, visible_in_conversations, last_updated) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",nel, ai_enabled, needs_agent, visible_in_conversations, last_updated) "
                        (f"test_user_{i}", f"test_chat_{i}", "test", 1, 0, 0, datetime.now(timezone.utc).isoformat())
                    )   (f"test_user_{i}", f"test_chat_{i}", "test", 1, 0, 0, datetime.now(timezone.utc).isoformat())
                    convo_id = c.fetchone()['id']
                    c.execute( c.fetchone()['id']
                        "INSERT INTO messages (convo_id, username, sender, message, timestamp) VALUES (%s, %s, %s, %s, %s)",
                        (convo_id, f"test_user_{i}", "user", f"Test message {i}", datetime.now(timezone.utc).isoformat()))",
                    )   (convo_id, f"test_user_{i}", "user", f"Test message {i}", datetime.now(timezone.utc).isoformat())
                conn.commit()
                logger.info("✅ Added test conversations")
            else:ogger.info("✅ Added test conversations")
                logger.info("✅ Test conversations already exist, skipping insertion")
            release_db_connection(conn)versations already exist, skipping insertion")
    except Exception as e:nection(conn)
        logger.error(f"❌ Error adding test conversations: {e}")
        raiser.error(f"❌ Error adding test conversations: {e}")
        raise
# Initialize database and add test conversations
init_db()ize database and add test conversations
add_test_conversations()
add_test_conversations()
@with_db_retry
def log_message(convo_id, username, message, sender):
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(f"Attempting to log message for convo_id {convo_id}: '{message[:50]}...' (Sender: {sender}, Timestamp: {timestamp})")
    conn = None(f"Attempting to log message for convo_id {convo_id}: '{message[:50]}...' (Sender: {sender}, Timestamp: {timestamp})")
    try: = None
        conn = get_db_connection()
        c = conn.cursor()nection()
        c.execute("BEGIN")
        c.execute("BEGIN")
            "INSERT INTO messages (convo_id, username, message, sender, timestamp) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",message, sender, timestamp) "
            (convo_id, username, message, sender, timestamp)
        )   (convo_id, username, message, sender, timestamp)
        message_id_tuple = c.fetchone()
        message_id = message_id_tuple['id'] if message_id_tuple else None
        message_id = message_id_tuple['id'] if message_id_tuple else None
        # Update conversation's last_updated timestamp
        c.execute(onversation's last_updated timestamp
            "UPDATE conversations SET last_updated = %s WHERE id = %s",
            (timestamp, convo_id) SET last_updated = %s WHERE id = %s",
        )   (timestamp, convo_id)
        )
        c.execute("COMMIT")
        logger.info(f"✅ Logged message for convo_id {convo_id}, message_id {message_id}. Sender: {sender}. Updated conversation last_updated.")
        return timestamp, message_idge for convo_id {convo_id}, message_id {message_id}. Sender: {sender}. Updated conversation last_updated.")
    except Exception as e:message_id
        if conn:tion as e:
            try:
                # Obtain a new cursor for rollback if the original one is problematic
                rollback_cursor = conn.cursor()ack if the original one is problematic
                rollback_cursor.execute("ROLLBACK")
                rollback_cursor.close()("ROLLBACK")
                logger.info(f"DB transaction rolled back for convo_id {convo_id} due to error: {str(e)}")
            except Exception as rb_exc:ction rolled back for convo_id {convo_id} due to error: {str(e)}")
                logger.error(f"❌ Error during ROLLBACK for convo_id {convo_id}: {rb_exc}", exc_info=True)
        logger.error(f"❌ Failed to log message for convo_id {convo_id}: {str(e)}", exc_info=True)fo=True)
        raise  # Re-raise the exception to be handled by caller or @with_db_retry, exc_info=True)
    finally:e  # Re-raise the exception to be handled by caller or @with_db_retry
        if conn:
            release_db_connection(conn)
            release_db_connection(conn)
class User(UserMixin):
    def __init__(self, id, username):
        self.id = idf, id, username):
        self.username = username
        logger.debug(f"User object created: id={id}, username='{username}'")
        logger.debug(f"User object created: id={id}, username='{username}'")
@login_manager.user_loader
def load_user(user_id):der
    start_time = time.time()
    logger.info(f"Starting load_user for user_id {user_id}")
    conn = None(f"Starting load_user for user_id {user_id}")
    try: = None
        logger.debug(f"Attempting to load user with id: {user_id}")
        conn = get_db_connection()to load user with id: {user_id}")
        c = conn.cursor()nection()
        c.execute("SELECT id, username FROM agents WHERE id = %s", (user_id,))
        user_data = c.fetchone()ername FROM agents WHERE id = %s", (user_id,))
        if user_data:.fetchone()
            user = User(id=user_data['id'], username=user_data['username']) # Corrected: Removed unnecessary escapes
            logger.info(f"✅ User loaded: id={user.id}, username='{user.username}'")cted: Removed unnecessary escapes
            return user(f"✅ User loaded: id={user.id}, username='{user.username}'")
        logger.warning(f"⚠️ No user found with id: {user_id}")
        return Noneing(f"⚠️ No user found with id: {user_id}")
    except Exception as e:
        logger.error(f"❌ Error loading user {user_id}: {str(e)}", exc_info=True)
        return Noner(f"❌ Error loading user {user_id}: {str(e)}", exc_info=True)
    finally:rn None
        if conn:
            release_db_connection(conn)
            release_db_connection(conn)
@app.route('/')
def index():/')
    logger.info(f"Route / accessed by {request.remote_addr}")
    if current_user.is_authenticated: {request.remote_addr}")
        logger.debug(f"User {current_user.username} is authenticated, redirecting to /dashboard")
        return redirect('/dashboard')user.username} is authenticated, redirecting to /dashboard")
    logger.debug("User not authenticated, redirecting to /login")
    return redirect('/login')thenticated, redirecting to /login")
    return redirect('/login')
@app.route('/login', methods=['GET', 'POST'])
def login():/login', methods=['GET', 'POST'])
    logger.info("✅ /login endpoint registered and called")
    start_time = time.time()dpoint registered and called")
    logger.info("Starting /login endpoint")
    # Removed try block that was causing "Try statement must have at least one except or finally clause"
    if request.method == "GET":s causing "Try statement must have at least one except or finally clause"
        if current_user.is_authenticated:
            logger.info(f"User already authenticated, redirecting in {time.time() - start_time:.2f} seconds")
            return redirect(request.args.get("next", "/conversations"))ime.time() - start_time:.2f} seconds")
        logger.info(f"Rendering login page in {time.time() - start_time:.2f} seconds")
        return render_template("login.html")n {time.time() - start_time:.2f} seconds")
        return render_template("login.html")
    # Log the request headers and content type
    logger.info(f"Request headers: {request.headers}")
    logger.info(f"Request content type: {request.content_type}")
    logger.info(f"Request content type: {request.content_type}")
    # Try to get JSON data
    data = request.get_json(silent=True)
    if data is None:et_json(silent=True)
        # Fallback to form data
        logger.info("No JSON data found, trying form data")
        data = request.formN data found, trying form data")
        logger.info(f"Form data: {dict(data)}")
        logger.info(f"Form data: {dict(data)}")
    if not data:
        logger.error("❌ No valid JSON or form data in /login request")
        return jsonify({"message": "Invalid request format, expected JSON or form data"}), 400
        return jsonify({"message": "Invalid request format, expected JSON or form data"}), 400
    username = data.get("username")
    if not username:get("username")
        logger.error("❌ Missing username in /login request")
        return jsonify({"message": "Missing username"}), 400
        return jsonify({"message": "Missing username"}), 400
    logger.info(f"Attempting to log in user: {username}")
    conn = None # Initialize connog in user: {username}")
    try: = None # Initialize conn
        with get_db_connection() as conn:
            c = conn.cursor()n() as conn:
            c.execute(ursor()
                "SELECT id, username FROM agents WHERE username = %s",
                (username,) username FROM agents WHERE username = %s",
            )   (username,)
            user_data = c.fetchone()
            logger.info(f"Agent query result: {user_data}")
            if user_data:"Agent query result: {user_data}")
                # No password check, directly log in if user exists
                user = User(id=user_data['id'], username=user_data['username'])
                login_user(user)ser_data['id'], username=user_data['username'])
                session['username'] = user.username # Store username in session
                logger.info(f"✅ User '{username}' logged in successfully.")sion
                return redirect('/dashboard')me}' logged in successfully.")
            else:eturn redirect('/dashboard')
                logger.warning(f"⚠️ Login failed for username: '{username}'. User not found.")
                return render_template('login.html', error="Invalid credentials") not found.")
    except Exception as e:der_template('login.html', error="Invalid credentials")
        logger.error(f"❌ Error during login for {username}: {str(e)}", exc_info=True)
        return render_template('login.html', error="An error occurred. Please try again.")
    # finally: # Removed finally block as 'with' statement handles connection releasein.")
    #     if conn:emoved finally block as 'with' statement handles connection release
    # The original return render_template('login.html') was inside the try block,
    # it should be outside if the POST request fails before the try, or if it's a GET.
    # For a POST that fails validation before DB, it returns JSON.y, or if it's a GET.
    # If it's a GET, it's already returned.re DB, it returns JSON.
    # Adding a fallback for safety, though logically it might be dead code for POST.
    return render_template('login.html', error="An unexpected error occurred.")POST.
    return render_template('login.html', error="An unexpected error occurred.")

@app.route('/logout')
@login_requiredgout')
def logout():ed
    logger.info(f"User '{current_user.username}' initiated logout from {request.remote_addr}.")
    logout_user()"User '{current_user.username}' initiated logout from {request.remote_addr}.")
    logger.info("✅ User logged out successfully.")
    return redirect('/login')d out successfully.")
    return redirect('/login')
@app.route('/dashboard')
@login_requiredshboard')
def dashboard():
    logger.info(f"Route /dashboard accessed by user '{current_user.username}' from {request.remote_addr}")
    return render_template('dashboard.html', username=current_user.username)' from {request.remote_addr}")
    return render_template('dashboard.html', username=current_user.username)
@app.route('/get_conversations')
@login_requiredt_conversations')
def get_conversations():
    start_time = time.time()
    logger.info("Starting /get_conversations endpoint")
    conn = None("Starting /get_conversations endpoint")
    try: = None
        logger.debug(f"User '{current_user.username}' fetching conversations.")
        conn = get_db_connection()ent_user.username}' fetching conversations.")
        c = conn.cursor()nection()
        c.execute(ursor()
            "SELECT id, username, channel, assigned_agent, needs_agent, ai_enabled "
            "FROM conversations " channel, assigned_agent, needs_agent, ai_enabled "
            "WHERE needs_agent = 1 "
            "ORDER BY last_updated DESC"
        )   "ORDER BY last_updated DESC"
        conversations = [
            {rsations = [
                "id": row["id"],
                "username": row["username"],
                "channel": row["channel"],],
                "assigned_agent": row["assigned_agent"],
                "needs_agent": row["needs_agent"],ent"],
                "ai_enabled": row["ai_enabled"]"],
            }   "ai_enabled": row["ai_enabled"]
            for row in c.fetchall()
        ]   for row in c.fetchall()
        logger.info(f"✅ Retrieved {len(conversations)} conversations for user '{current_user.username}'.")
        return jsonify(conversations)n(conversations)} conversations for user '{current_user.username}'.")
    except Exception as e:versations)
        logger.error(f"❌ Error fetching conversations for {current_user.username}: {str(e)}", exc_info=True)
        return jsonify([]), 500fetching conversations for {current_user.username}: {str(e)}", exc_info=True)
    finally:rn jsonify([]), 500
        if conn:
            release_db_connection(conn)
            release_db_connection(conn)
@app.route('/get_messages/<int:convo_id>')
@login_requiredt_messages/<int:convo_id>')
def get_messages(convo_id):
    start_time = time.time()
    logger.info(f"Starting /get_messages/{convo_id} endpoint")
    conn = None(f"Starting /get_messages/{convo_id} endpoint")
    try: = None
        logger.debug(f"User '{current_user.username}' fetching messages for convo_id: {convo_id}.")
        conn = get_db_connection()ent_user.username}' fetching messages for convo_id: {convo_id}.")
        c = conn.cursor()nection()
        # Check if the conversation exists and is visible
        c.execute( the conversation exists and is visible
            "SELECT username, visible_in_conversations FROM conversations WHERE id = %s",
            (convo_id,)rname, visible_in_conversations FROM conversations WHERE id = %s",
        )   (convo_id,)
        convo = c.fetchone()
        if not convo:chone()
            logger.error(f"❌ Conversation not found: {convo_id}")
            release_db_connection(conn)on not found: {convo_id}")
            return jsonify({"error": "Conversation not found"}), 404
            return jsonify({"error": "Conversation not found"}), 404
        if not convo["visible_in_conversations"]:
            logger.info(f"Conversation {convo_id} is not visible")
            release_db_connection(conn){convo_id} is not visible")
            return jsonify({"username": convo["username"], "messages": []})
            return jsonify({"username": convo["username"], "messages": []})
        username = convo["username"]
        username = convo["username"]
        # Check Redis cache first
        cache_key = f"messages:{convo_id}"
        cached_messages = redis_get_sync(cache_key)
        if cached_messages:edis_get_sync(cache_key)
            logger.info(f"Returning cached messages for convo_id {convo_id}")
            cached_data = json.loads(cached_messages)or convo_id {convo_id}")
            return jsonify({on.loads(cached_messages)
                "username": cached_data["username"],
                "messages": cached_data["messages"],
            })  "messages": cached_data["messages"]
            })
        # Fetch messages from database
        c.execute(ssages from database
            "SELECT message, sender, timestamp "
            "FROM messages " sender, timestamp "
            "WHERE convo_id = %s "
            "ORDER BY timestamp ASC",
            (convo_id,)imestamp ASC",
        )   (convo_id,)
        messages = [
            {ges = [
                "message": msg["message"],
                "sender": msg["sender"],],
                "timestamp": msg["timestamp"] if isinstance(msg["timestamp"], str) else msg["timestamp"].isoformat()
            }   "timestamp": msg["timestamp"] if isinstance(msg["timestamp"], str) else msg["timestamp"].isoformat()
            for msg in c.fetchall()
        ]   for msg in c.fetchall()
        logger.info(f"✅ Retrieved {len(messages)} messages for convo_id {convo_id} for user '{current_user.username}'.")
        logger.info(f"✅ Retrieved {len(messages)} messages for convo_id {convo_id} for user '{current_user.username}'.")
        # Cache the result for 300 seconds (5 minutes)
        cache_data = {"username": username, "messages": messages}
        redis_setex_sync(cache_key, 300, json.dumps(cache_data))}
        release_db_connection(conn) 300, json.dumps(cache_data))
        release_db_connection(conn)
        logger.info(f"Finished /get_messages/{convo_id} in {time.time() - start_time:.2f} seconds")
        return jsonify({nished /get_messages/{convo_id} in {time.time() - start_time:.2f} seconds")
            "username": username,
            "messages": messages,
        })  "messages": messages
    except Exception as e:
        logger.error(f"❌ Error in /get_messages/{convo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to fetch messages"}), 500)}", exc_info=True)
        return jsonify({"error": "Failed to fetch messages"}), 500
@app.route('/send_message', methods=['POST'])
@login_requirednd_message', methods=['POST'])
def send_message_route():
    import tasks # Import tasks module locally
    start_time = time.time()sks module locally
    logger.info("Starting /send_message endpoint")
    data = request.get_json()nd_message endpoint"
    if not data:st.get_json()
        logger.error("❌ No JSON data in /send_message request")
        return jsonify({"error": "Invalid request, JSON data expected"}), 400
        return jsonify({"error": "Invalid request, JSON data expected"}), 400
    convo_id = data.get('convo_id')
    message_body = data.get('message')
    chat_id = data.get('chat_id') # Assuming chat_id is sent from frontend
    channel = data.get('channel', 'dashboard') # Default to dashboard if not specified
    username = current_user.username # Agent's usernamet to dashboard if not specified
    username = current_user.username # Agent's username
    if not convo_id or not message_body or not chat_id:
        logger.error(f"❌ Missing convo_id, message, or chat_id in /send_message. Convo: {convo_id}, ChatID: {chat_id}, Msg: '{message_body}'")
        return jsonify({"error": "Missing convo_id, message, or chat_id"}), 400. Convo: {convo_id}, ChatID: {chat_id}, Msg: '{message_body}'")
        return jsonify({"error": "Missing convo_id, message, or chat_id"}), 400
    logger.info(f"Agent '{username}' sending message to convo_id {convo_id} (chat_id: {chat_id}, channel: {channel}): '{message_body[:50]}...'")
    logger.info(f"Agent '{username}' sending message to convo_id {convo_id} (chat_id: {chat_id}, channel: {channel}): '{message_body[:50]}...'")
    try:
        # Log the agent's message
        timestamp, message_id = log_message(convo_id, username, message_body, "agent")
        logger.info(f"✅ Agent message logged to DB for convo_id {convo_id}. Message ID: {message_id}")
        logger.info(f"✅ Agent message logged to DB for convo_id {convo_id}. Message ID: {message_id}")
        # Emit agent message to SocketIO for real-time update
        room = f"conversation_{convo_id}"for real-time update
        socketio.emit('new_message', {d}"
            'convo_id': convo_id,e', {
            'message': message_body,
            'sender': 'agent',_body,
            'username': username, # Agent's username
            'timestamp': timestamp, Agent's username
            'chat_id': chat_id,amp,
            'channel': channel,
        }, to=room)l': channel
        logger.info(f"✅ Emitted 'new_message' (agent) to SocketIO room: {room}")
        logger.info(f"✅ Emitted 'new_message' (agent) to SocketIO room: {room}")
        # If the channel is WhatsApp, send the message via Twilio using Celery task
        if channel == 'whatsapp':App, send the message via Twilio using Celery task
            logger.info(f"Channel is WhatsApp for convo_id {convo_id}. Dispatching send_whatsapp_message_task.")
            # Use tasks.send_whatsapp_message_taskconvo_id {convo_id}. Dispatching send_whatsapp_message_task.
            tasks.send_whatsapp_message_task.delay(
                to_number=chat_id, # chat_id is the user's phone number for WhatsApp
                message=message_body,chat_id is the user's phone number for WhatsApp
                convo_id=convo_id, # Pass convo_id for context if needed by the task
                username=username, # Agent usernamefor context if needed by the task
                chat_id=chat_id # Pass chat_id for context
            )   chat_id=chat_id # Pass chat_id for context
            logger.info(f"✅ Dispatched send_whatsapp_message_task for convo_id {convo_id} to chat_id {chat_id}")
            logger.info(f"✅ Dispatched send_whatsapp_message_task for convo_id {convo_id} to chat_id {chat_id}")
        processing_time = time.time() - start_time
        logger.info(f"/send_message for convo_id {convo_id} completed in {processing_time:.2f} seconds")
        return jsonify({"status": "Message sent and logged", "timestamp": timestamp, "message_id": message_id})
        return jsonify({"status": "Message sent and logged", "timestamp": timestamp, "message_id": message_id})
    except Exception as e:
        logger.error(f"❌ Error in /send_message for convo_id {convo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to send message"}), 500nvo_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to send message"}), 500
@app.route('/twilio_webhook', methods=['POST'])
def twilio_webhook():ebhook', methods=['POST'])
    import tasks # Import tasks module locally
    start_time = time.time()sks module locally
    logger.info("Starting /twilio_webhook endpoint")
    try:er.info("Starting /twilio_webhook endpoint")
        data = request.form
        from_number_raw = data.get('From')
        message_body = data.get('Body')m')
        profile_name = data.get('ProfileName') # User's WhatsApp profile name
        profile_name = data.get('ProfileName') # User's WhatsApp profile name
        if not from_number_raw or not message_body:
            logger.warning(f"⚠️ Missing 'From' or 'Body' in Twilio webhook data: {data}")
            return jsonify({"status": "error", "message": "Missing From or Body"}), 400")
            return jsonify({"status": "error", "message": "Missing From or Body"}), 400
        # Normalize from_number: remove "whatsapp:" prefix if present, then ensure it has it for chat_id consistency
        chat_id = from_number_raw.replace("whatsapp:", "") if present, then ensure it has it for chat_id consistency
        from_number_normalized = f"whatsapp:{chat_id}" # This is the ID used with Twilio API
        from_number_normalized = f"whatsapp:{chat_id}" # This is the ID used with Twilio API
        user_timestamp = datetime.now(timezone.utc).isoformat()
        logger.info(f"Received WhatsApp message. From: {from_number_normalized} (Profile: {profile_name}), Body: '{message_body[:50]}...'")
        logger.info(f"Received WhatsApp message. From: {from_number_normalized} (Profile: {profile_name}), Body: '{message_body[:50]}...'")
        # Dispatch to Celery task for processing
        # Use tasks.process_whatsapp_messagesing
        tasks.process_whatsapp_message.delay(
            from_number=from_number_normalized, # Send the full "whatsapp:+123..." number
            chat_id=chat_id,                   # Send the plain number as chat_id" number
            message_body=message_body,         # Send the plain number as chat_id
            user_timestamp=user_timestamp
        )   user_timestamp=user_timestamp
        logger.info(f"✅ Dispatched process_whatsapp_message task for chat_id {chat_id}")
        logger.info(f"✅ Dispatched process_whatsapp_message task for chat_id {chat_id}")
        processing_time = time.time() - start_time
        logger.info(f"/twilio_webhook for chat_id {chat_id} completed in {processing_time:.2f} seconds")
        return Response(status=204) # Twilio expects a 204 No Content or an empty <Response/> TwiMLnds")
        return Response(status=204) # Twilio expects a 204 No Content or an empty <Response/> TwiML
    except Exception as e:
        logger.error(f"❌ Error processing Twilio webhook: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500
        return jsonify({"status": "error", "message": "Internal server error"}), 500
# SocketIO Events
@socketio.on("connect")
def handle_connect():
    """Handle new Socket.IO connections with proper authentication."""
    try:
        if not current_user.is_authenticated:
            logger.warning(f"Unauthenticated Socket.IO connection attempt from {request.remote_addr}")
            return False  # Reject the connection
            
        sid = request.sid
        logger.info(f"SOCKETIO Client connected: session_id={sid}, user='{current_user.username}', ip={request.remote_addr}")
        emit('status', {
            'msg': f'Connected as {current_user.username}',
            'username': current_user.username
        }, to=sid)
    except Exception as e:
        logger.error(f"❌ Error in Socket.IO connect handler: {str(e)}", exc_info=True)
        return False
    return True

@socketio.on("disconnect")
def handle_disconnect():")
    sid = request.sid():
    logger.info(f"SOCKETIO Client disconnected: session_id={sid}")
    logger.info(f"SOCKETIO Client disconnected: session_id={sid}")
@socketio.on("join_conversation")
@login_required # Ensure only logged-in users can join
def handle_join_conversation(data):d-in users can join
    sid = request.sidrsation(data):
    convo_id = data.get("conversation_id")
    agent_username = current_user.username
    logger.info(f"SOCKETIO 'join_conversation' received. Agent: {agent_username}, Convo ID: {convo_id}, SID: {sid}")
    if not convo_id:CKETIO 'join_conversation' received. Agent: {agent_username}, Convo ID: {convo_id}, SID: {sid}")
        logger.error("❌ Attempted to join conversation with no convo_id")
        emit("error", {"message": "conversation_id is required"}, to=sid)
        returnerror", {"message": "conversation_id is required"}, to=sid)
    room = f"conversation_{convo_id}"
    join_room(room) # SID is implicitly used by join_room for the current client
    logger.info(f"Agent '{agent_username}' (SID: {sid}) joined room: {room}")ent
    emit("room_joined", {"room": room, "convo_id": convo_id}, to=room) # Notify others in the room (or just the client: to=sid)
    emit("room_joined", {"room": room, "convo_id": convo_id}, to=room) # Notify others in the room (or just the client: to=sid)
@socketio.on("leave_conversation")
@login_requiredeave_conversation"
def handle_leave_conversation(data):
    sid = request.sidersation(data):
    convo_id = data.get("conversation_id")
    agent_username = current_user.username
    logger.info(f"SOCKETIO 'leave_conversation' received. Agent: {agent_username}, Convo ID: {convo_id}, SID: {sid}")
    if not convo_id:CKETIO 'leave_conversation' received. Agent: {agent_username}, Convo ID: {convo_id}, SID: {sid}")
        logger.error("❌ Attempted to leave conversation with no convo_id")
        emit("error", {"message": "conversation_id is required"}, to=sid))
        returnerror", {"message": "conversation_id is required"}, to=sid)
    room = f"conversation_{convo_id}"
    leave_room(room) # SID is implicitly used
    logger.info(f"Agent '{agent_username}' (SID: {sid}) left room: {room}")
    emit("room_left", {"room": room, "convo_id": convo_id}, to=room) # Notify others in the room
    emit("room_left", {"room": room, "convo_id": convo_id}, to=room) # Notify others in the room
@socketio.on("agent_message")
@login_required
def handle_agent_message(data):
    """Handle incoming agent messages via Socket.IO with improved error handling."""
    import tasks # Import tasks module locally
    start_time = time.time()
    
    sid = request.sid
    logger.info(f"SocketIO 'agent_message' received from {current_user.username} (SID: {sid})")
    
    try:
        # Validate required fields
        required_fields = ['convo_id', 'message', 'chat_id', 'channel']
        missing_fields = [field for field in required_fields if not data.get(field)]
        
        if missing_fields:
            error_msg = f"Missing required fields: {', '.join(missing_fields)}"
            logger.error(f"❌ {error_msg}")
            emit('error', {'message': error_msg}, to=sid)
            return
            
        convo_id = data.get('convo_id')
        message_body = data.get('message')
        chat_id = data.get('chat_id')
        channel = data.get('channel')
        username = current_user.username
        
        logger.info(f"Agent '{username}' sending message via Socket.IO to convo_id {convo_id} (chat_id: {chat_id}, channel: {channel}): '{message_body[:50]}...'")
        
        # Log the agent's message to the database
        timestamp, message_id = log_message(convo_id, username, message_body, "agent")
        logger.info(f"✅ Agent message logged to DB for convo_id {convo_id}. Message ID: {message_id}")
        
        # Emit the message to the specific conversation room
        room = f"conversation_{convo_id}"
        message_data = {
            'convo_id': convo_id,
            'message': message_body,
            'sender': 'agent',
            'username': username,
            'timestamp': timestamp,
            'chat_id': chat_id,
            'channel': channel
        }
        
        emit('new_message', message_data, to=room)
        logger.info(f"✅ Emitted 'new_message' (agent) to Socket.IO room: {room}")
        
        # If the channel is WhatsApp, also send the message via Twilio using Celery task
        if channel == 'whatsapp':
            logger.info(f"Channel is WhatsApp for convo_id {convo_id}. Dispatching send_whatsapp_message_task.")
            tasks.send_whatsapp_message_task.delay(
                to_number=chat_id,
                message=message_body,
                convo_id=convo_id,
                username=username,
                chat_id=chat_id
            )
            logger.info(f"✅ Dispatched send_whatsapp_message_task for convo_id {convo_id} to chat_id {chat_id}")
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"'agent_message' for convo_id {convo_id} processed in {processing_time:.2f}ms.")
        
    except Exception as e:
        error_msg = f"Error processing 'agent_message': {str(e)}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        emit('error', {'message': f"Server error: {str(e)}"}, to=sid)

# Middleware for Flask route logging
@app.before_requestask route logging
def log_request_info():
    g.start_time = time.time() # Store start time in g
    logger.debug(f"Request: {request.method} {request.url} from {request.remote_addr}")
    logger.debug(f"Headers: {request.headers}")equest.url} from {request.remote_addr}")
    if request.data:eaders: {request.headers}")
        logger.debug(f"Body: {request.get_data(as_text=True)}")
        logger.debug(f"Body: {request.get_data(as_text=True)}")
@app.after_request
def log_response_info(response):
    processing_time = (time.time() - g.start_time) * 1000 # Calculate processing time in ms
    logger.info(ime = (time.time() - g.start_time) * 1000 # Calculate processing time in ms
        f"Response: {request.method} {request.url} - Status: {response.status_code} - Size: {response.content_length} bytes - Time: {processing_time:.2f}ms"
    )   f"Response: {request.method} {request.url} - Status: {response.status_code} - Size: {response.content_length} bytes - Time: {processing_time:.2f}ms"
    # Consider logging response data for errors or specific debug needs, be cautious with sensitive data
    # if response.status_code >= 400:for errors or specific debug needs, be cautious with sensitive data
    #     logger.error(f"Response Data for Error: {response.get_data(as_text=True)}")
    return responseror(f"Response Data for Error: {response.get_data(as_text=True)}")
    return response
@app.route('/live-messages')
@login_required
def live_messages():
    """Serve the live messages page for a specific conversation."""
    convo_id = request.args.get('id')
    if not convo_id:
        logger.warning(f"User '{current_user.username}' attempted to access live messages without conversation ID")
        return redirect('/dashboard')
    return render_template('live_messages.html', convo_id=convo_id)    logger.info(f"Starting Flask-SocketIO server on host 0.0.0.0, port {os.getenv('PORT', 5000)}")if __name__ == "__main__":    return render_template('live-messages.html', convo_id=convo_id)    logger.info(f"User '{current_user.username}' accessing live messages for conversation ID {convo_id}")            return redirect('/dashboard')        logger.warning(f"User '{current_user.username}' attempted to access live messages without conversation ID")    if not convo_id:    convo_id = request.args.get('id')    """Serve the live messages page for a specific conversation."""def live_messages():    # Pass the app logger to SocketIO if not done during initialization{os.getenv('PORT', 5000)}")
    # socketio.init_app(app, logger=logger, engineio_logger=logger) # Alternative way
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False, use_reloader=False)
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False, use_reloader=False)