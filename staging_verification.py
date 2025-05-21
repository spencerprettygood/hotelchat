#!/usr/bin/env python3
"""
Staging Environment Verification Script

This script runs a series of tests against the staging environment to ensure
all components are working correctly before production deployment.

Usage:
    python staging_verification.py --url https://your-staging-url.onrender.com
"""

import os
import sys
import time
import json
import argparse
import requests
import socketio
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("staging_verification.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("staging_verification")

class StagingVerifier:
    def __init__(self, base_url):
        self.base_url = base_url
        self.session = requests.Session()
        self.sio = socketio.Client(logger=True, engineio_logger=True)
        self.test_results = {
            "http_connectivity": False,
            "login_functionality": False,
            "openai_integration": False,
            "socketio_connectivity": False,
            "database_functionality": False,
            "redis_functionality": False,
            "whatsapp_integration": False
        }
    
    def verify_http_connectivity(self):
        """Verify basic HTTP connectivity to the staging server"""
        logger.info(f"Testing HTTP connectivity to {self.base_url}")
        try:
            response = self.session.get(f"{self.base_url}/", timeout=10)
            if response.status_code == 200:
                logger.info("✅ HTTP connectivity test passed")
                self.test_results["http_connectivity"] = True
                return True
            else:
                logger.error(f"❌ HTTP connectivity test failed: Status code {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ HTTP connectivity test failed: {e}")
            return False

    def verify_login_functionality(self, username="admin"):
        """Test login functionality"""
        logger.info(f"Testing login functionality with username: {username}")
        try:
            response = self.session.post(
                f"{self.base_url}/login",
                json={"username": username},
                timeout=10
            )
            
            # Check redirect or successful response
            if response.status_code in (200, 302) and "/dashboard" in response.url:
                logger.info("✅ Login test passed")
                self.test_results["login_functionality"] = True
                return True
            else:
                logger.error(f"❌ Login test failed: Status code {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Login test failed: {e}")
            return False
    
    def verify_openai_integration(self):
        """Test OpenAI integration using diagnostic endpoint"""
        logger.info("Testing OpenAI integration")
        try:
            # First verify login if not already logged in
            if not self.test_results["login_functionality"]:
                self.verify_login_functionality()
            
            # Call our diagnostic endpoint
            response = self.session.post(
                f"{self.base_url}/openai_diag",
                json={"prompt": "Test prompt for staging verification"},
                timeout=30  # Longer timeout for API calls
            )
            
            if response.status_code == 200:
                data = response.json()
                if "response" in data and len(data["response"]) > 10:
                    logger.info("✅ OpenAI integration test passed")
                    self.test_results["openai_integration"] = True
                    logger.info(f"OpenAI response preview: {data['response'][:100]}...")
                    return True
                else:
                    logger.error(f"❌ OpenAI integration test failed: Invalid response format")
                    logger.error(f"Response: {data}")
                    return False
            else:
                logger.error(f"❌ OpenAI integration test failed: Status code {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ OpenAI integration test failed: {e}")
            return False

    def verify_socketio_connectivity(self):
        """Test Socket.IO connectivity and basic functionality"""
        logger.info(f"Testing Socket.IO connectivity to {self.base_url}")
        
        connected = False
        received_message = False
        
        @self.sio.event
        def connect():
            nonlocal connected
            logger.info("✅ Socket.IO connected successfully")
            connected = True
            
        @self.sio.event
        def connect_error(data):
            logger.error(f"❌ Socket.IO connection failed: {data}")
            
        @self.sio.event
        def disconnect():
            logger.info("Socket.IO disconnected")
            
        @self.sio.on('new_message')
        def on_new_message(data):
            nonlocal received_message
            logger.info(f"Received message via Socket.IO: {data}")
            received_message = True
        
        try:
            # Use the session cookies for authentication
            cookies = dict(self.session.cookies)
            self.sio.connect(
                self.base_url,
                headers={"Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])},
                wait_timeout=10
            )
            
            # Wait a bit for connection
            time.sleep(5)
            
            if connected:
                logger.info("✅ Socket.IO connectivity test passed")
                self.test_results["socketio_connectivity"] = True
                
                # Clean up
                self.sio.disconnect()
                return True
            else:
                logger.error("❌ Socket.IO connectivity test failed: Could not establish connection")
                return False
        except Exception as e:
            logger.error(f"❌ Socket.IO connectivity test failed: {e}")
            return False
    
    def verify_database_functionality(self):
        """Test database functionality by retrieving conversations"""
        logger.info("Testing database functionality by retrieving conversations")
        try:
            response = self.session.get(f"{self.base_url}/get_conversations", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"✅ Database functionality test passed: Retrieved {len(data)} conversations")
                    self.test_results["database_functionality"] = True
                    return True
                else:
                    logger.error(f"❌ Database functionality test failed: Invalid response format")
                    return False
            else:
                logger.error(f"❌ Database functionality test failed: Status code {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Database functionality test failed: {e}")
            return False
    
    def run_all_tests(self):
        """Run all verification tests and return results"""
        logger.info(f"Starting comprehensive verification of {self.base_url}")
        
        # Run tests in logical order
        self.verify_http_connectivity()
        if self.test_results["http_connectivity"]:
            self.verify_login_functionality()
            if self.test_results["login_functionality"]:
                self.verify_database_functionality()
                self.verify_openai_integration()
                self.verify_socketio_connectivity()
        
        # Log summary
        logger.info("\n" + "=" * 50)
        logger.info("VERIFICATION RESULTS SUMMARY")
        logger.info("=" * 50)
        
        for test, result in self.test_results.items():
            status = "✅ PASSED" if result else "❌ FAILED"
            logger.info(f"{test.replace('_', ' ').title()}: {status}")
        
        # Return True only if all critical tests passed
        critical_tests = ["http_connectivity", "login_functionality", "database_functionality", "openai_integration"]
        return all(self.test_results[test] for test in critical_tests)

def main():
    parser = argparse.ArgumentParser(description="Verify staging deployment")
    parser.add_argument("--url", required=True, help="Staging server URL (e.g., https://your-app-staging.onrender.com)")
    
    args = parser.parse_args()
    
    verifier = StagingVerifier(args.url.rstrip('/'))
    success = verifier.run_all_tests()
    
    if success:
        logger.info("🎉 Verification PASSED! The staging environment appears to be functioning correctly.")
        sys.exit(0)
    else:
        logger.error("❌ Verification FAILED! Please review the logs and fix any issues before proceeding to production.")
        sys.exit(1)

if __name__ == "__main__":
    main()
