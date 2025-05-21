#!/usr/bin/env python3
"""
Standalone diagnostic tool for OpenAI API connectivity and functionality.

This utility provides both command-line and HTTP endpoint functionality 
for testing the OpenAI integration in the HotelChat application.

Usage as CLI tool:
    python openai_diag_tool.py --prompt "Your test prompt here"

Usage as HTTP endpoint:
    - Import into your Flask app and register the route
    - Send POST requests to /openai_diag with JSON body {"prompt": "your prompt"}
"""

import os
import sys
import json
import logging
import argparse
import asyncio
import time
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI
from openai.types.error import RateLimitError, APIError, AuthenticationError
from openai.types.timeout_error import APITimeoutError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("openai_diag")

# Ensure the project root is in the Python path to allow importing project modules if necessary
# This might be needed if the diagnostic tool needs to access shared configurations or utilities
# For now, we assume it's standalone but prepared for future integration.
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Attempt to load environment variables from .env file if present (especially for local dev)
# In a deployed environment, these should be set directly.
dotenv_path = os.path.join(current_dir, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
    logger.info(f"Loaded environment variables from {dotenv_path}")

def timer_decorator(func):
    """Decorator to time function execution."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        elapsed = time.time() - start_time
        logger.info(f"Function {func.__name__} completed in {elapsed:.2f} seconds")
        return result, elapsed
    return wrapper

class OpenAIDiagnostic:
    """Class for running OpenAI diagnostic tests"""
    
    def __init__(self):
        """Initialize the diagnostic tool with API key from environment"""
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            logger.error("❌ OPENAI_API_KEY not found in environment variables")
            raise ValueError("OPENAI_API_KEY environment variable must be set")
            
        self.model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
        logger.info(f"OpenAI diagnostic initialized with model: {self.model}")
        
    async def test_async_client(self, prompt="Hello, world!"):
        """Test the AsyncOpenAI client with a prompt."""
        logger.info(f"Testing AsyncOpenAI client with prompt: '{prompt}'")
        
        start_time = time.time()
        client = AsyncOpenAI(api_key=self.api_key, timeout=30.0)
        
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.7
            )
            
            elapsed_time = time.time() - start_time
            result = {
                "success": True,
                "response": response.choices[0].message.content,
                "model": self.model,
                "elapsed_time": f"{elapsed_time:.2f}s",
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "timestamp": datetime.now().isoformat()
            }
            logger.info(f"✅ AsyncOpenAI test successful in {elapsed_time:.2f}s")
            return result
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            error_type = type(e).__name__
            error_msg = str(e)
            
            result = {
                "success": False,
                "error_type": error_type,
                "error_message": error_msg,
                "model": self.model,
                "elapsed_time": f"{elapsed_time:.2f}s",
                "timestamp": datetime.now().isoformat()
            }
            logger.error(f"❌ AsyncOpenAI test failed: {error_type}: {error_msg}")
            return result
    
    def test_sync_client(self, prompt="Hello, world!"):
        """Test the synchronous OpenAI client with a prompt."""
        logger.info(f"Testing synchronous OpenAI client with prompt: '{prompt}'")
        
        start_time = time.time()
        client = OpenAI(api_key=self.api_key, timeout=30.0)
        
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.7
            )
            
            elapsed_time = time.time() - start_time
            result = {
                "success": True,
                "response": response.choices[0].message.content,
                "model": self.model,
                "elapsed_time": f"{elapsed_time:.2f}s",
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "timestamp": datetime.now().isoformat()
            }
            logger.info(f"✅ Synchronous OpenAI test successful in {elapsed_time:.2f}s")
            return result
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            error_type = type(e).__name__
            error_msg = str(e)
            
            result = {
                "success": False,
                "error_type": error_type,
                "error_message": error_msg,
                "model": self.model,
                "elapsed_time": f"{elapsed_time:.2f}s",
                "timestamp": datetime.now().isoformat()
            }
            logger.error(f"❌ Synchronous OpenAI test failed: {error_type}: {error_msg}")
            return result
    
    def verify_import_paths(self):
        """Verify that we're using OpenAI v1.x API correctly."""
        try:
            import openai
            logger.info(f"OpenAI version: {openai.__version__}")
            
            # Verify the imports we need are available
            from openai.types.error import RateLimitError, APIError, AuthenticationError
            from openai.types.timeout_error import APITimeoutError
            
            # Verify the client constructors
            test_client = OpenAI(api_key=self.api_key)
            test_async_client = AsyncOpenAI(api_key=self.api_key)
            
            return {
                "success": True,
                "version": openai.__version__,
                "imports_verified": True,
                "timestamp": datetime.now().isoformat()
            }
            
        except ImportError as e:
            logger.error(f"❌ Failed to import OpenAI v1.x components: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "OpenAI v1.x imports failed. Make sure you have 'openai>=1.0.0' installed.",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ OpenAI verification error: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def run_complete_diagnostics(self, prompt="Tell me about Amapola Resort"):
        """Run all diagnostic tests and return results."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "import_verification": self.verify_import_paths()
        }
        
        # Run synchronous test
        results["sync_test"] = self.test_sync_client(prompt)
        
        # Run async test
        async_task = self.test_async_client(prompt)
        loop = asyncio.get_event_loop()
        results["async_test"] = loop.run_until_complete(async_task)
        
        # Overall success determination
        results["success"] = (
            results["import_verification"]["success"] and
            results["sync_test"]["success"] and
            results["async_test"]["success"]
        )
        
        return results


def create_flask_blueprint():
    """Create a Flask blueprint for the diagnostic tool."""
    try:
        from flask import Blueprint, request, jsonify, render_template
        
        bp = Blueprint('openai_diag', __name__, url_prefix='/openai_diag')
        
        @bp.route('/', methods=['GET'])
        def diag_ui():
            """Render diagnostic UI."""
            return render_template('openai_diag.html')
        
        @bp.route('/', methods=['POST'])
        def run_diag_api():
            """Run OpenAI diagnostics via API endpoint."""
            data = request.get_json()
            if not data:
                return jsonify({"error": "No JSON data received"}), 400
                
            prompt = data.get('prompt', 'Tell me about Amapola Resort')
            
            try:
                diag_tool = OpenAIDiagnostic()
                results = diag_tool.run_complete_diagnostics(prompt)
                return jsonify(results)
            except Exception as e:
                logger.exception("Error running diagnostics")
                return jsonify({"error": str(e)}), 500
                
        return bp
        
    except ImportError:
        logger.warning("Flask not available, blueprint creation skipped")
        return None


def main():
    """Run the diagnostic tool from the command line."""
    parser = argparse.ArgumentParser(description="OpenAI API diagnostic tool")
    parser.add_argument("--prompt", default="Tell me about Amapola Resort", help="Test prompt for OpenAI")
    parser.add_argument("--model", help="Override the model to use (default: from env or gpt-3.5-turbo)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()
    
    if args.model:
        os.environ["OPENAI_MODEL"] = args.model
        
    try:
        print("=" * 60)
        print("OpenAI API Diagnostic Tool")
        print("=" * 60)
        
        diag_tool = OpenAIDiagnostic()
        results = diag_tool.run_complete_diagnostics(args.prompt)
        
        if args.json:
            print(json.dumps(results, indent=2))
            sys.exit(0 if results["success"] else 1)
        
        # Pretty print results
        print(f"\nOpenAI Version: {results['import_verification']['version']}")
        print(f"Import Verification: {'✅ PASSED' if results['import_verification']['success'] else '❌ FAILED'}")
        
        print("\nSynchronous API Test:")
        if results["sync_test"]["success"]:
            print(f"  ✅ PASSED in {results['sync_test']['elapsed_time']}")
            print(f"  Response: {results['sync_test']['response'][:100]}...")
            print(f"  Tokens Used: {results['sync_test']['total_tokens']}")
        else:
            print(f"  ❌ FAILED: {results['sync_test']['error_type']}: {results['sync_test']['error_message']}")
        
        print("\nAsynchronous API Test:")
        if results["async_test"]["success"]:
            print(f"  ✅ PASSED in {results['async_test']['elapsed_time']}")
            print(f"  Response: {results['async_test']['response'][:100]}...")
            print(f"  Tokens Used: {results['async_test']['total_tokens']}")
        else:
            print(f"  ❌ FAILED: {results['async_test']['error_type']}: {results['async_test']['error_message']}")
        
        print("\nOverall Result:", "✅ ALL TESTS PASSED" if results["success"] else "❌ SOME TESTS FAILED")
        print("=" * 60)
        
        sys.exit(0 if results["success"] else 1)
        
    except Exception as e:
        print(f"❌ Error running diagnostics: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
