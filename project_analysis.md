# Project Analysis: Hotel Chatbot

This document outlines the main components of the Hotel Chatbot application and their interactions.

## Main Components

### 1. Flask Application (`chat_server.py`)

* **Flask Routes:**
  * Handles HTTP requests for user authentication (login), serving the main dashboard, settings management, and potentially API endpoints for fetching conversation history or other data.
  * Key routes include `/`, `/login`, `/dashboard`, `/get_conversations`, `/get_messages`, `/settings`, `/whatsapp` (for Twilio webhooks).

* **Primary Responsibilities:**
  * Web request handling and routing.
  * User session management.
  * Serving HTML templates and static assets.
  * Orchestrating interactions between other components (Database, OpenAI, SocketIO, Redis).

### 2. OpenAI Integration (`chat_server.py`, `tasks.py`)

* **Location:**
  * OpenAI client initialization (`AsyncOpenAI`) and direct API call logic (e.g., `ai_respond`, `ai_respond_sync`) are primarily in `chat_server.py`.
  * Celery tasks in `tasks.py` (e.g., `process_whatsapp_message` calling `ai_respond_sync`) also interact with the OpenAI service, typically by calling functions defined in `chat_server.py`.

* **Functionality:**
  * Generating AI-powered responses to user queries.
  * Utilizes models like `gpt-4o-mini`.
  * Handles message history for context.

* **Interaction:**
  * Called by Flask routes or SocketIO event handlers in `chat_server.py` when a user message requires an AI response.
  * Called by Celery tasks in `tasks.py` for background AI processing, especially for WhatsApp messages.

### 3. SocketIO Implementation (`chat_server.py`, `static/dashboard.js`, `tasks.py`)

* **Server-Side (`chat_server.py`):**
  * Manages real-time, bidirectional communication between the server and connected clients (web dashboard).
  * Handles events like `connect`, `disconnect`, `join_conversation`, `leave_conversation`, `agent_message`, `user_message`.
  * Emits events like `new_message`, `conversation_update`, `agent_status_update`.

* **Client-Side (`static/dashboard.js`):**
  * Establishes SocketIO connection to the server.
  * Sends user messages and other client-initiated events to the server.
  * Listens for server-emitted events to update the UI in real-time (e.g., display new messages, update conversation list).

* **Celery-Side (`tasks.py`):**
  * A `CelerySocketIO` instance (`celery_socketio`) is used to allow Celery tasks to emit SocketIO events back to the client dashboard (e.g., after processing a WhatsApp message and generating an AI response). This typically works via the Redis message queue.

* **Interaction:**
  * Clients connect to the SocketIO server. Messages are exchanged in real-time.
  * Flask routes might trigger SocketIO events indirectly.
  * Celery tasks can push updates to clients via SocketIO.

### 4. Database (`chatbot.db`, `chat_server.py`, `tasks.py`)

* **Type:** The application uses `psycopg2` for database connections, indicating PostgreSQL. The `DATABASE_URL` environment variable would point to the PostgreSQL instance. The `chatbot.db` file might be a remnant of previous development or a fallback SQLite database if PostgreSQL is unavailable, but primary operations in `chat_server.py` and `tasks.py` use `psycopg2`.

* **Schema (Implicit):** Tables for `conversations`, `messages`, `users`, `settings`. Schema is defined by `CREATE TABLE` statements within `chat_server.py` (`init_db`, `create_tables`).

* **Functionality:**
  * Stores conversation history, user messages, AI responses, user credentials, application settings.

* **Interaction:**
  * Accessed by `chat_server.py` to fetch data for users, display messages, save new messages, and manage settings.
  * Accessed by `tasks.py` to log messages from background processes (e.g., WhatsApp interactions) and update conversation state.
  * Uses raw SQL queries executed via database cursors obtained from a `psycopg2` connection pool.

### 5. Twilio Integration (`tasks.py`, `chat_server.py`)

* **Location:**
  * Twilio client initialization and message sending logic (`send_whatsapp_message_task`) are in `tasks.py`.
  * Configuration (API keys, phone number) is loaded from environment variables, accessed in both `tasks.py` and `chat_server.py`.
  * An incoming webhook endpoint (`/whatsapp`) in `chat_server.py` receives messages from Twilio.

* **Functionality:**
  * Sending and receiving WhatsApp messages.

* **Interaction:**
  * The `/whatsapp` route in `chat_server.py` receives incoming messages from Twilio and typically enqueues a Celery task (`process_whatsapp_message` in `tasks.py`) for processing.
  * The `send_whatsapp_message_task` in `tasks.py` sends outgoing messages via Twilio API, often triggered after AI processing or by an agent.

### 6. Redis Caching & Message Queue (`chat_server.py`, `tasks.py`)

* **Location:**
  * Redis client (`redis.Redis`) for caching is initialized and used in `chat_server.py` (e.g., `redis_get_sync`, `redis_setex_sync`).
  * Celery in `tasks.py` uses Redis as a message broker (to receive tasks) and a backend (to store results, though `task_ignore_result=True` is set).
  * The `CelerySocketIO` instance in `tasks.py` also uses Redis as a message queue to bridge Celery events to the main Flask-SocketIO server.

* **Functionality:**
  * **Caching:** Stores frequently accessed data (e.g., OpenAI responses, conversation lists) to reduce database load and API calls.
  * **Message Broker:** Manages the queue of tasks for Celery workers.
  * **SocketIO Bridge:** Facilitates SocketIO emissions from Celery tasks.

* **Interaction:**
  * `chat_server.py` uses Redis for caching API responses and potentially other data.
  * Celery relies on Redis for its core operation of distributing and managing tasks.

### 7. Celery Task Queue (`tasks.py`, `celery_worker.py`)

* **Location:**
  * Task definitions (e.g., `process_whatsapp_message`, `send_whatsapp_message_task`) are in `tasks.py`.
  * `celery_worker.py` is likely the script used to run the Celery worker processes.

* **Functionality:**
  * Handles background processing for long-running or asynchronous operations like processing incoming WhatsApp messages, calling the OpenAI API, and sending outgoing Twilio messages.

* **Interaction:**
  * Tasks are enqueued by `chat_server.py` (e.g., when a WhatsApp message arrives or an AI response needs to be sent via WhatsApp).
  * Workers (started by `celery_worker.py`) pick up tasks from the Redis queue and execute them.
  * Tasks can interact with the Database, OpenAI, Twilio, and emit SocketIO messages via `celery_socketio`.

## Component Interaction Flow (Example: Incoming WhatsApp Message)

1. **Twilio -> Flask (`/whatsapp` in `chat_server.py`):** User sends a WhatsApp message. Twilio forwards it to the `/whatsapp` webhook.
2. **Flask -> Celery (`chat_server.py` -> `tasks.py`):** The `/whatsapp` route in `chat_server.py` receives the message, performs initial validation, and enqueues a `process_whatsapp_message` task in Celery.
3. **Celery Worker (`tasks.py`):** A Celery worker picks up the `process_whatsapp_message` task.
4. **Task -> Database (`tasks.py`):** The task logs the incoming user message to the database, creating or updating the conversation.
5. **Task -> SocketIO (`tasks.py` -> `celery_socketio` -> `chat_server.py` -> Client):** The task emits a `new_message` event via `celery_socketio` (using Redis as a backbone) to update the agent dashboard in real-time.
6. **Task -> OpenAI (via `ai_respond_sync` in `chat_server.py`):** If AI is enabled, the task prepares context from message history (fetched from DB) and calls `ai_respond_sync` (which in turn calls the OpenAI API).
7. **OpenAI -> Task:** The AI response is returned to the task.
8. **Task -> Celery (`tasks.py`):** The task enqueues another task, `send_whatsapp_message_task`, to send the AI's reply.
9. **Celery Worker (`tasks.py`):** A Celery worker picks up `send_whatsapp_message_task`.
10. **Task -> Database & SocketIO (`tasks.py`):** This task logs the AI's message to the database and emits another `new_message` event via `celery_socketio` for the dashboard.
11. **Task -> Twilio (`tasks.py`):** The task sends the AI's message to the user via the Twilio API.

This flow illustrates the decoupled nature of the application, using Celery for asynchronous processing and Redis for communication and caching, allowing the main Flask application to remain responsive.
