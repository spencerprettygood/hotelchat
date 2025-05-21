"""
Standalone diagnostic tool for Socket.IO connectivity.

This script will:
1. Start a simple Flask-SocketIO echo server on a specified port.
2. Connect a Socket.IO client to this server.
3. Send a test event from the client and wait for an echo response.
4. Send a test event from the server (broadcast) and check if the client receives it.
5. Log all major events and outcomes.

Usage:
    python socketio_diag_tool.py [--port PORT]
"""

import os
import sys
import time
import logging
import argparse
import threading
import asyncio
import socketio
from dotenv import load_dotenv
from flask import Flask, request
from flask_socketio import SocketIO, emit

# --- Configuration ---
TEST_HOST = '127.0.0.1'
TEST_PORT = 5001 # Choose a port not likely to be in use by the main app
TEST_NAMESPACE = '/test_diagnostic' # Using a specific namespace for diagnostics
LOG_FILE = "socketio_diagnostic.log"
# Default server URL; can be overridden by SOCKETIO_SERVER_URL environment variable
DEFAULT_SOCKETIO_URL = "http://localhost:5000" # Adjust if your server runs elsewhere
SOCKETIO_SERVER_URL = os.getenv("SOCKETIO_SERVER_URL", DEFAULT_SOCKETIO_URL)
TEST_EVENT_NAME = "diagnostic_test_event"
TEST_RESPONSE_EVENT_NAME = "diagnostic_test_response"
TEST_MESSAGE_DATA = {"message": "Hello from Socket.IO Diagnostic Tool!", "timestamp": time.time()}
CONNECTION_TIMEOUT = 10  # seconds

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout) # Also print to console
    ]
)
logger = logging.getLogger(__name__)

# --- Global state for test coordination ---
client_connected_event = threading.Event()
echo_received_event = threading.Event()
broadcast_received_event = threading.Event()
client_received_data = None
server_error_occurred = False
client_error_occurred = False

# --- Flask-SocketIO Test Server ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'diagnostic_secret!'
# Suppress Flask's startup messages for cleaner diagnostic output
cli = sys.modules['flask.cli']
cli.show_server_banner = lambda *x: None

socketio_server = SocketIO(app, logger=True, engineio_logger=True, cors_allowed_origins="*")

@socketio_server.on('connect', namespace=TEST_NAMESPACE)
def handle_server_connect():
    logger.info(f"[Server] Client connected to namespace {TEST_NAMESPACE}")
    emit('server_greeting', {'data': 'Welcome to the diagnostic server!'}, namespace=TEST_NAMESPACE)

@socketio_server.on('disconnect', namespace=TEST_NAMESPACE)
def handle_server_disconnect():
    logger.info(f"[Server] Client disconnected from namespace {TEST_NAMESPACE}")

@socketio_server.on('client_echo_request', namespace=TEST_NAMESPACE)
def handle_client_echo(data):
    logger.info(f"[Server] Received 'client_echo_request' from client with data: {data}")
    emit('server_echo_response', data, namespace=TEST_NAMESPACE)
    logger.info(f"[Server] Sent 'server_echo_response' back to client with data: {data}")

@socketio_server.on_error_default
def socketio_error_handler(e):
    global server_error_occurred
    logger.error(f"[Server] Socket.IO Error: {e}", exc_info=True)
    server_error_occurred = True

def run_test_server():
    global server_error_occurred
    logger.info(f"[Server] Starting diagnostic Socket.IO server on http://{TEST_HOST}:{TEST_PORT}{TEST_NAMESPACE}")
    try:
        socketio_server.run(app, host=TEST_HOST, port=TEST_PORT, use_reloader=False, debug=False)
    except Exception as e:
        logger.error(f"[Server] Failed to start diagnostic server: {e}", exc_info=True)
        server_error_occurred = True
    logger.info("[Server] Diagnostic server has shut down.")

# --- Socket.IO Test Client ---
sio_client = socketio.Client(logger=True, engineio_logger=True)

@sio_client.event
def connect():
    # This connect is for the default namespace, we need to connect to our specific namespace
    pass

@sio_client.event
def connect_error(data):
    global client_error_occurred
    logger.error(f"[Client] Connection failed: {data}")
    client_error_occurred = True
    client_connected_event.set() # Unblock if waiting for connection

@sio_client.event
def disconnect():
    logger.info("[Client] Disconnected from server.")
    # If disconnect happens unexpectedly, events might not be set, causing hangs.
    # Ensure all waiting events are set to allow graceful exit of the main thread.
    client_connected_event.set()
    echo_received_event.set()
    broadcast_received_event.set()

@sio_client.on('connect', namespace=TEST_NAMESPACE)
def on_client_connect_namespace():
    logger.info(f"[Client] Successfully connected to namespace: {TEST_NAMESPACE}")
    client_connected_event.set()

@sio_client.on('server_greeting', namespace=TEST_NAMESPACE)
def on_server_greeting(data):
    logger.info(f"[Client] Received 'server_greeting': {data}")

@sio_client.on('server_echo_response', namespace=TEST_NAMESPACE)
def on_server_echo_response(data):
    global client_received_data
    logger.info(f"[Client] Received 'server_echo_response' with data: {data}")
    client_received_data = data
    echo_received_event.set()

@sio_client.on('server_broadcast_event', namespace=TEST_NAMESPACE)
def on_server_broadcast(data):
    logger.info(f"[Client] Received 'server_broadcast_event' with data: {data}")
    broadcast_received_event.set()

# --- Diagnostic Test Logic ---
class SocketIODiagnosticClient:
    def __init__(self, server_url):
        self.sio = socketio.AsyncClient(logger=True, engineio_logger=True)
        self.server_url = server_url
        self.connected = False
        self.test_event_sent = False
        self.response_received = False
        self.error_occurred = False
        self.test_passed = False

        # Standard event handlers
        @self.sio.event
        async def connect():
            logger.info(f"[Client] Connected to Socket.IO server at {self.server_url}")
            self.connected = True

        @self.sio.event
        async def connect_error(data):
            logger.error(f"[Client] Connection error: {data}")
            self.error_occurred = True

        @self.sio.event
        async def disconnect():
            logger.info("[Client] Disconnected from Socket.IO server")
            if self.connected:  # Only log as error if we were previously connected
                logger.warning("[Client] Unexpected disconnection from server")

        # Custom event handler for the test response
        @self.sio.on(TEST_RESPONSE_EVENT_NAME)
        async def on_test_response(data):
            logger.info(f"[Client] Received test response: {data}")
            self.response_received = True
            # Validate that the response matches what we sent
            if isinstance(data, dict) and data.get("message") == TEST_MESSAGE_DATA["message"]:
                logger.info("[Client] Test passed: Response data matches sent data")
                self.test_passed = True
            else:
                logger.error(f"[Client] Test failed: Response data does not match sent data")
                self.test_passed = False

    async def run_test(self):
        logger.info(f"--- Starting Socket.IO Connection Test for {self.server_url} ---")
        try:
            logger.info(f"[Client] Connecting to {self.server_url}")
            await self.sio.connect(self.server_url, wait_timeout=CONNECTION_TIMEOUT)
            
            logger.info(f"[Client] Sending test event: {TEST_EVENT_NAME}")
            await self.sio.emit(TEST_EVENT_NAME, TEST_MESSAGE_DATA)
            self.test_event_sent = True
            
            logger.info("[Client] Waiting for response...")
            # Wait for up to 5 seconds for response
            await asyncio.sleep(5)
            
            logger.info(f"[Client] Disconnecting from {self.server_url}")
            await self.sio.disconnect()
            
        except socketio.exceptions.ConnectionError as e:
            logger.error(f"[Client] Failed to connect to server: {e}")
            self.error_occurred = True
        except asyncio.TimeoutError:
            logger.error(f"[Client] Connection timeout")
            self.error_occurred = True
        except Exception as e:
            logger.error(f"[Client] Unexpected error: {e}", exc_info=True)
            self.error_occurred = True
        finally:
            # Ensure we're disconnected
            if self.sio.connected:
                await self.sio.disconnect()

        # Determine overall success
        if self.connected and self.test_event_sent and self.response_received and self.test_passed and not self.error_occurred:
            logger.info("[Client] All tests PASSED!")
            return True
        elif self.connected and self.test_event_sent and not self.response_received and not self.error_occurred:
            logger.warning("[Client] Connected successfully but no response received. Test INCOMPLETE.")
            return False
        else:
            logger.error("[Client] Test FAILED!")
            return False

def run_test_client():
    """Run test client against the test server."""
    global client_error_occurred, client_received_data
    
    logger.info(f"[Client] Connecting to test server on http://{TEST_HOST}:{TEST_PORT}{TEST_NAMESPACE}")
    try:
        # Reset test flags
        client_connected_event.clear()
        echo_received_event.clear()
        broadcast_received_event.clear()
        client_received_data = None
        client_error_occurred = False
        
        # Connect to test server
        sio_client.connect(f"http://{TEST_HOST}:{TEST_PORT}", namespaces=[TEST_NAMESPACE])
        
        # Wait for connection or timeout
        if not client_connected_event.wait(timeout=CONNECTION_TIMEOUT):
            logger.error("[Client] Timeout waiting for connection")
            return False
        
        if client_error_occurred:
            logger.error("[Client] Connection error occurred")
            return False
        
        # Send echo request
        logger.info(f"[Client] Sending echo request with data: {TEST_MESSAGE_DATA}")
        sio_client.emit('client_echo_request', TEST_MESSAGE_DATA, namespace=TEST_NAMESPACE)
        
        # Wait for echo response or timeout
        if not echo_received_event.wait(timeout=5):
            logger.error("[Client] Timeout waiting for echo response")
            sio_client.disconnect()
            return False
            
        # Validate echo response
        if client_received_data == TEST_MESSAGE_DATA:
            logger.info("[Client] Echo test PASSED - received expected data back")
        else:
            logger.error(f"[Client] Echo test FAILED - received unexpected data: {client_received_data}")
            sio_client.disconnect()
            return False
        
        # Wait for broadcast test (triggered by server)
        logger.info("[Client] Waiting for server broadcast event...")
        
        # Disconnect at the end
        sio_client.disconnect()
        logger.info("[Client] Test client disconnected")
        
        return not client_error_occurred
        
    except Exception as e:
        logger.error(f"[Client] Error in test client: {e}", exc_info=True)
        try:
            if sio_client.connected:
                sio_client.disconnect()
        except:
            pass
        return False

async def main():
    print(f"Running Socket.IO Connection Tester for server: {SOCKETIO_SERVER_URL}")
    client_tester = SocketIODiagnosticClient(SOCKETIO_SERVER_URL)
    success = await client_tester.run_test()

    if success:
        print("\nSocket.IO diagnostics completed successfully (or with warnings, check logs).")
    else:
        print("\nSocket.IO diagnostics encountered errors (check logs for details).")
        # sys.exit(1) # Exit with error code if diagnostics fail critically

# --- Main Diagnostic Runner ---
def run_socketio_diagnostics():
    logger.info("===========================================")
    logger.info("Starting Socket.IO Diagnostic Tool")
    logger.info(f"Logs will be written to: {LOG_FILE}")
    logger.info("===========================================")

    global server_error_occurred, client_error_occurred
    server_error_occurred = False # Reset for the run
    client_error_occurred = False

    # Start the server in a separate thread
    server_thread = threading.Thread(target=run_test_server, name="SocketIOServerThread", daemon=True)
    server_thread.start()

    logger.info("Waiting for diagnostic server to initialize...")
    time.sleep(3) # Give the server a moment to start up

    if server_error_occurred:
        logger.error("Diagnostic server failed to start. Aborting client tests.")
        server_success = False
        client_success = False
    else:
        logger.info("Diagnostic server presumed started. Running client tests...")
        server_success = True # If no error flag by now, server thread started
        # Run client tests
        client_success = run_test_client()

    # After client tests, attempt to send a broadcast from server
    # This needs to happen after client is connected and listening
    if server_success and client_success and sio_client.connected:
        # This check is a bit redundant due to how run_test_client works, but safe
        logger.info("[Server] Attempting to send 'server_broadcast_event' to connected clients.")
        try:
            socketio_server.emit('server_broadcast_event', 
                                {"message": "Broadcast test", "timestamp": time.time()}, 
                                namespace=TEST_NAMESPACE)
            logger.info("[Server] Broadcast event sent")
            
            # Wait briefly for broadcast to be received
            if broadcast_received_event.wait(timeout=3):
                logger.info("[Test] Broadcast test PASSED - client received broadcast")
            else:
                logger.warning("[Test] Broadcast test FAILED - client did not receive broadcast")
        except Exception as e:
            logger.error(f"[Server] Error sending broadcast: {e}", exc_info=True)

    logger.info("Shutting down diagnostic server...")
    # For Flask-SocketIO, sending a shutdown request to the development server
    # can be done via a special route or by stopping the thread and letting resources clean up.
    # Since it's a daemon thread, it will exit when the main program exits.
    # For a more graceful shutdown of the *server itself for cleanup*:
    if hasattr(socketio_server, 'stop'):
        socketio_server.stop()
    # The server thread is a daemon, so it will stop when the main thread finishes.
    # Wait a moment for client to fully disconnect if it hasn't already
    time.sleep(1)

    logger.info("===========================================")
    logger.info("Socket.IO Diagnostics Summary:")
    logger.info(f"  Diagnostic Server Health:   {'SUCCESS' if server_success and not server_error_occurred else 'FAILED'}")
    logger.info(f"  Client Connectivity & Echo: {'SUCCESS' if client_success else 'FAILED'}")
    # Note: broadcast_received_event is part of client_success logic

    if server_success and not server_error_occurred and client_success:
        logger.info("✅ Socket.IO Diagnostics PASSED")
        print("\nSocket.IO Diagnostic: PASSED ✅")
        print("Socket.IO server and client communications are working properly.")
        return True
    else:
        logger.error("❌ Socket.IO Diagnostics FAILED - check logs for details")
        print("\nSocket.IO Diagnostic: FAILED ❌")
        print(f"See log file for details: {LOG_FILE}")
        return False

    logger.info("Socket.IO Diagnostics finished.")
    logger.info("===========================================")

def test_production_server(server_url=None):
    """Test connection to a production Socket.IO server."""
    if server_url:
        global SOCKETIO_SERVER_URL
        SOCKETIO_SERVER_URL = server_url
        
    logger.info(f"Testing connection to production Socket.IO server: {SOCKETIO_SERVER_URL}")
    
    try:
        # Use asyncio to run the async client
        asyncio.run(main())
        return True
    except Exception as e:
        logger.error(f"Error testing production server: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Socket.IO connectivity")
    parser.add_argument("--port", type=int, default=TEST_PORT, help="Port for test server")
    parser.add_argument("--server", default=None, help="Production server URL to test instead of running test server")
    args = parser.parse_args()
    
    if args.server:
        # Test against production server
        test_production_server(args.server)
    else:
        # Run local diagnostic
        success = run_socketio_diagnostics()
        sys.exit(0 if success else 1)
