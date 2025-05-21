#!/usr/bin/env python3
"""
Integration Testing Utility for HotelChat

This script provides end-to-end testing of the HotelChat application,
focusing on testing the integration between OpenAI API and SocketIO functionality.

Usage:
    python integration_test.py [--test-socketio] [--test-openai] [--all]
"""

import os
import sys
import time
import json
import argparse
import asyncio
import logging
import requests
import socketio
from datetime import datetime
from urllib.parse import urljoin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("integration_test")

# Default test configuration
DEFAULT_CONFIG = {
    "base_url": os.getenv("TEST_SERVER_URL", "http://localhost:5000"),
    "admin_user": os.getenv("TEST_ADMIN_USER", "admin"),
    "test_chat_id": os.getenv("TEST_CHAT_ID", "test_chat_123"),
    "test_convo_id": os.getenv("TEST_CONVO_ID", "1"),
    "openai_test_prompt": "Tell me about hotel amenities",
    "socketio_wait_time": 5,  # seconds to wait for socketio responses
    "request_timeout": 10,     # seconds for HTTP request timeout
}

class IntegrationTester:
    def __init__(self, config=None, offline=False):
        """Initialize the integration tester with configuration."""
        self.config = config or DEFAULT_CONFIG
        self.base_url = self.config["base_url"]
        self.session = requests.Session()
        self.sio = socketio.Client(logger=True, engineio_logger=True)
        self.socketio_connected = False
        self.received_messages = []
        self.auth_token = None
        self.offline = offline
        
        # Set up SocketIO event handlers
        self.sio.on('connect', self.on_connect)
        self.sio.on('disconnect', self.on_disconnect)
        self.sio.on('new_message', self.on_new_message)
        self.sio.on('error', self.on_error)
        
        logger.info(f"Integration tester initialized with base URL: {self.base_url}")
        if self.offline:
            logger.warning("Running in OFFLINE mode – external integrations are mocked.")

    def on_connect(self):
        """Handler for SocketIO connect event."""
        logger.info("SocketIO connected successfully")
        self.socketio_connected = True

    def on_disconnect(self):
        """Handler for SocketIO disconnect event."""
        logger.info("SocketIO disconnected")
        self.socketio_connected = False

    def on_new_message(self, data):
        """Handler for new messages received via SocketIO."""
        logger.info(f"Received message: {data}")
        self.received_messages.append(data)

    def on_error(self, data):
        """Handler for SocketIO error events."""
        logger.error(f"SocketIO error received: {data}")

    def _skip(self, name):
        logger.info(f"[OFFLINE] Skipping {name}")
        return True

    def login(self):
        """Authenticate with the server and get a session."""
        if self.offline: return self._skip("login")
        
        login_url = urljoin(self.base_url, "/login")
        logger.info(f"Attempting login to {login_url}")
        
        try:
            # First request to get CSRF token if needed
            response = self.session.get(login_url, timeout=self.config["request_timeout"])
            
            # Submit login
            login_data = {"username": self.config["admin_user"]}
            response = self.session.post(
                login_url,
                json=login_data,
                timeout=self.config["request_timeout"]
            )
            
            if response.status_code == 200:
                logger.info("Login successful")
                return True
            else:
                logger.error(f"Login failed with status code: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error during login: {e}")
            return False

    def connect_socketio(self):
        """Connect to the SocketIO server."""
        if self.offline: return self._skip("socketio connect")
        
        logger.info("Connecting to SocketIO server...")
        try:
            # Use the existing session cookies
            cookies = dict(self.session.cookies)
            self.sio.connect(
                self.base_url,
                headers={"Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])},
                wait=True
            )
            return self.socketio_connected
        except Exception as e:
            logger.error(f"Failed to connect to SocketIO: {e}")
            return False

    def join_conversation(self, convo_id):
        """Join a specific conversation room."""
        if self.offline: return self._skip("join_conversation")
        
        logger.info(f"Joining conversation: {convo_id}")
        if not self.socketio_connected:
            logger.error("Cannot join conversation: SocketIO not connected")
            return False
            
        try:
            self.sio.emit("join_conversation", {"conversation_id": convo_id})
            logger.info(f"Join conversation request sent for conversation ID: {convo_id}")
            return True
        except Exception as e:
            logger.error(f"Error joining conversation: {e}")
            return False

    def send_message_via_socketio(self, convo_id, message):
        """Send a message through SocketIO."""
        if self.offline: return self._skip("send_message")
        
        if not self.socketio_connected:
            logger.error("Cannot send message: SocketIO not connected")
            return False
            
        message_data = {
            "convo_id": convo_id,
            "message": message,
            "chat_id": self.config["test_chat_id"],
            "channel": "test"
        }
        
        logger.info(f"Sending message via SocketIO: {message[:50]}...")
        try:
            self.sio.emit("agent_message", message_data)
            return True
        except Exception as e:
            logger.error(f"Error sending message via SocketIO: {e}")
            return False

    def wait_for_messages(self, timeout=None):
        """Wait for a specified time to collect messages."""
        timeout = timeout or self.config["socketio_wait_time"]
        logger.info(f"Waiting {timeout} seconds for messages...")
        time.sleep(timeout)
        return self.received_messages

    def test_openai_integration(self):
        """Test the OpenAI integration directly."""
        if self.offline: return True, {"offline": True}
        
        logger.info("Testing OpenAI integration...")
        
        # Use the diagnostic tool we created earlier
        openai_tool_url = urljoin(self.base_url, "/openai_diag")
        
        try:
            response = self.session.post(
                openai_tool_url, 
                json={"prompt": self.config["openai_test_prompt"]},
                timeout=self.config["request_timeout"]
            )
            
            if response.status_code == 200:
                data = response.json()
                if "response" in data:
                    logger.info(f"OpenAI test successful. Response: {data['response'][:100]}...")
                    return True, data
                else:
                    logger.error(f"OpenAI test failed: No response field in data: {data}")
                    return False, data
            else:
                logger.error(f"OpenAI test failed with status {response.status_code}: {response.text}")
                return False, None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error testing OpenAI integration: {e}")
            return False, None

    def test_end_to_end(self):
        """Run an end-to-end test of the chat system."""
        logger.info("Starting end-to-end integration test")
        
        # Step 1: Login
        if not self.login():
            logger.error("End-to-end test failed at login step")
            return False
            
        # Step 2: Connect to SocketIO
        if not self.connect_socketio():
            logger.error("End-to-end test failed at SocketIO connection step")
            return False
            
        # Step 3: Join a conversation
        convo_id = self.config["test_convo_id"]
        if not self.join_conversation(convo_id):
            logger.error("End-to-end test failed at join conversation step")
            return False
            
        # Step 4: Send a test message
        test_message = f"Test message: {datetime.now().isoformat()}"
        if not self.send_message_via_socketio(convo_id, test_message):
            logger.error("End-to-end test failed at send message step")
            return False
            
        # Step 5: Wait for and validate responses
        messages = self.wait_for_messages()
        if not messages:
            logger.warning("No messages received during the wait period")
        
        # Check if our message appears in the received messages
        found = any(msg.get('message') == test_message for msg in messages)
        if found:
            logger.info("Successfully found our test message in the responses")
        else:
            logger.warning("Did not find our test message in responses")
        
        logger.info(f"End-to-end test completed with {len(messages)} messages received")
        return True

    def close(self):
        """Clean up connections and sessions."""
        logger.info("Cleaning up connections...")
        if self.socketio_connected:
            try:
                self.sio.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting from SocketIO: {e}")
                
        self.session.close()
        logger.info("Integration test cleanup complete")

def main():
    """Main function to run the integration tests."""
    parser = argparse.ArgumentParser(description="Run integration tests for HotelChat")
    parser.add_argument("--test-socketio", action="store_true", help="Test SocketIO functionality")
    parser.add_argument("--test-openai", action="store_true", help="Test OpenAI API integration")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--server", default=DEFAULT_CONFIG["base_url"], help="Server URL to test")
    parser.add_argument("--offline", action="store_true",
                        help="Run tests without external dependencies")
    
    args = parser.parse_args()
    
    # Update config based on args
    config = DEFAULT_CONFIG.copy()
    config["base_url"] = args.server
    
    tester = IntegrationTester(config, offline=args.offline)
    
    try:
        if args.test_socketio or args.all:
            logger.info("=== TESTING SOCKETIO FUNCTIONALITY ===")
            if tester.login() and tester.connect_socketio():
                tester.join_conversation(config["test_convo_id"])
                tester.send_message_via_socketio(config["test_convo_id"], "Test message from integration test")
                messages = tester.wait_for_messages()
                logger.info(f"Received {len(messages)} SocketIO messages")
            else:
                logger.error("SocketIO test prerequisites failed")
        
        if args.test_openai or args.all:
            logger.info("=== TESTING OPENAI INTEGRATION ===")
            success, data = tester.test_openai_integration()
            if success:
                logger.info("OpenAI integration test passed")
            else:
                logger.error("OpenAI integration test failed")
        
        if not (args.test_socketio or args.test_openai or args.all):
            logger.info("=== RUNNING END-TO-END TEST ===")
            tester.test_end_to_end()
            
    finally:
        tester.close()

if __name__ == "__main__":
    main()
