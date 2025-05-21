#!/usr/bin/env python3
"""
Production Environment Verification Script

This script runs a series of non-destructive tests against the production environment
to verify functionality after deployment.

Usage:
    python production_verification.py --url https://your-app.onrender.com
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
        logging.FileHandler("production_verification.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("production_verification")

class ProductionVerifier:
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
            "error_handling": False,
            "authentication": False
        }
        
    def verify_http_connectivity(self):
        """Verify basic HTTP connectivity to the production server"""
        logger.info(f"Testing HTTP connectivity to {self.base_url}")
        try:
            start_time = time.time()
            response = self.session.get(f"{self.base_url}/", timeout=10)
            latency = (time.time() - start_time) * 1000  # Convert to ms
            
            if response.status_code == 200:
                logger.info(f"✅ HTTP connectivity test passed (Latency: {latency:.2f}ms)")
                self.test_results["http_connectivity"] = True
                return True
            else:
                logger.error(f"❌ HTTP connectivity test failed: Status code {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ HTTP connectivity test failed: {e}")
            return False

    def verify_login_functionality(self, username="admin"):
        """Test login functionality without exposing credentials"""
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
        """Test OpenAI integration with minimal token usage"""
        logger.info("Testing OpenAI integration")
        try:
            # First verify login if not already logged in
            if not self.test_results["login_functionality"]:
                self.verify_login_functionality()
            
            # Call our diagnostic endpoint with minimal token usage
            response = self.session.post(
                f"{self.base_url}/openai_diag",
                json={"prompt": "Say hi", "max_tokens": 5},
                timeout=30  # Longer timeout for API calls
            )
            
            if response.status_code == 200:
                data = response.json()
                if "response" in data:
                    logger.info("✅ OpenAI integration test passed")
                    logger.info(f"OpenAI response: {data['response']}")
                    self.test_results["openai_integration"] = True
                    return True
                else:
                    logger.error(f"❌ OpenAI integration test failed: Invalid response format")
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
        """Test database read-only functionality"""
        logger.info("Testing database read functionality by retrieving conversations")
        try:
            response = self.session.get(f"{self.base_url}/get_conversations", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"✅ Database read test passed: Retrieved {len(data)} conversations")
                    self.test_results["database_functionality"] = True
                    return True
                else:
                    logger.error(f"❌ Database read test failed: Invalid response format")
                    return False
            else:
                logger.error(f"❌ Database read test failed: Status code {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Database read test failed: {e}")
            return False
    
    def verify_error_handling(self):
        """Test application error handling capabilities"""
        logger.info("Testing error handling")
        try:
            # Try to access a non-existent endpoint
            response = self.session.get(f"{self.base_url}/nonexistent-endpoint-12345", timeout=10)
            
            # We expect a 404, but the important thing is that it doesn't cause a 500 server error
            if response.status_code == 404:
                logger.info("✅ Error handling test passed: Received proper 404 for non-existent endpoint")
                self.test_results["error_handling"] = True
                return True
            elif response.status_code == 500:
                logger.error(f"❌ Error handling test failed: Server returned 500 instead of 404")
                return False
            else:
                logger.warning(f"⚠️ Error handling test: Unexpected status code {response.status_code}")
                self.test_results["error_handling"] = True
                return True
        except Exception as e:
            logger.error(f"❌ Error handling test failed: {e}")
            return False
    
    def verify_authentication(self):
        """Test that authentication is properly enforced"""
        logger.info("Testing authentication enforcement")
        try:
            # Create a fresh session (no cookies)
            test_session = requests.Session()
            
            # Try to access a protected endpoint
            response = test_session.get(f"{self.base_url}/get_conversations", timeout=10)
            
            # We expect to be redirected to login
            if response.status_code in (302, 401, 403) or "/login" in response.url:
                logger.info("✅ Authentication test passed: Protected endpoints require login")
                self.test_results["authentication"] = True
                return True
            else:
                logger.error(f"❌ Authentication test failed: Protected endpoint accessible without login (Status: {response.status_code})")
                return False
        except Exception as e:
            logger.error(f"❌ Authentication test failed: {e}")
            return False
    
    def run_all_tests(self):
        """Run all verification tests and return results"""
        logger.info(f"Starting comprehensive verification of {self.base_url}")
        
        # Run tests in logical order
        self.verify_http_connectivity()
        self.verify_authentication()
        
        if self.test_results["http_connectivity"]:
            self.verify_login_functionality()
            
            if self.test_results["login_functionality"]:
                self.verify_database_functionality()
                self.verify_socketio_connectivity()
                self.verify_openai_integration()
            
        self.verify_error_handling()
        
        # Log summary
        logger.info("\n" + "=" * 50)
        logger.info("PRODUCTION VERIFICATION RESULTS")
        logger.info("=" * 50)
        
        for test, result in self.test_results.items():
            status = "✅ PASSED" if result else "❌ FAILED"
            logger.info(f"{test.replace('_', ' ').title()}: {status}")
        
        # Overall assessment
        critical_tests = ["http_connectivity", "login_functionality", "database_functionality", "authentication"]
        if all(self.test_results[test] for test in critical_tests):
            logger.info("\n✅ OVERALL: Critical functionality is WORKING in production")
            return True
        else:
            logger.error("\n❌ OVERALL: Some critical functionality is NOT WORKING in production")
            return False

def main():
    parser = argparse.ArgumentParser(description="Verify production deployment")
    parser.add_argument("--url", required=True, help="Production server URL (e.g., https://your-app.onrender.com)")
    
    args = parser.parse_args()
    
    verifier = ProductionVerifier(args.url.rstrip('/'))
    success = verifier.run_all_tests()
    
    if success:
        logger.info("🎉 Production verification PASSED! The application appears to be functioning correctly.")
        sys.exit(0)
    else:
        logger.error("❌ Production verification FAILED! Please review the logs and investigate the issues.")
        sys.exit(1)

if __name__ == "__main__":
    main()
