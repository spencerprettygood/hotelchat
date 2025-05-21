"""
Test utility for OpenAI client to verify it's working correctly.

Usage:
    python openai_client_test.py
"""
import os
import sys
import logging
from dotenv import load_dotenv
import asyncio
import time
from openai import AsyncOpenAI, OpenAI
from openai.types.error import RateLimitError, APIError, AuthenticationError
from openai.types.timeout_error import APITimeoutError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("openai_test")

# Load environment variables if .env exists
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

async def test_async_client():
    """Test the AsyncOpenAI client with a simple chat completion."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("❌ OPENAI_API_KEY not found in environment variables")
        return False

    logger.info("Initializing AsyncOpenAI client...")
    client = AsyncOpenAI(
        api_key=api_key,
        timeout=30.0
    )

    try:
        logger.info("Making API call to OpenAI...")
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo", # Using a standard model for testing
            messages=[{"role": "user", "content": "Say hello world"}],
            max_tokens=10,
            temperature=0.7
        )
        logger.info(f"✅ Response received: {response.choices[0].message.content}")
        logger.info(f"✅ Usage: {response.usage.total_tokens} tokens")
        return True
    except RateLimitError as e:
        logger.error(f"❌ RateLimitError: {e}")
        return False
    except APITimeoutError as e:
        logger.error(f"❌ APITimeoutError: {e}")
        return False
    except APIError as e:
        logger.error(f"❌ APIError: {e}")
        return False
    except AuthenticationError as e:
        logger.error(f"❌ AuthenticationError: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False

def test_sync_client():
    """Test the synchronous OpenAI client with a simple chat completion."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("❌ OPENAI_API_KEY not found in environment variables")
        return False

    logger.info("Initializing OpenAI client...")
    client = OpenAI(
        api_key=api_key,
        timeout=30.0
    )

    try:
        logger.info("Making API call to OpenAI...")
        response = client.chat.completions.create(
            model="gpt-3.5-turbo", # Using a standard model for testing
            messages=[{"role": "user", "content": "Say hello world"}],
            max_tokens=10,
            temperature=0.7
        )
        logger.info(f"✅ Response received: {response.choices[0].message.content}")
        logger.info(f"✅ Usage: {response.usage.total_tokens} tokens")
        return True
    except RateLimitError as e:
        logger.error(f"❌ RateLimitError: {e}")
        return False
    except APITimeoutError as e:
        logger.error(f"❌ APITimeoutError: {e}")
        return False
    except APIError as e:
        logger.error(f"❌ APIError: {e}")
        return False
    except AuthenticationError as e:
        logger.error(f"❌ AuthenticationError: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False

def run_async_test():
    """Run the async test function."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(test_async_client())

def verify_import_paths():
    """Check that we're using OpenAI v1.x API."""
    import openai
    logger.info(f"OpenAI version: {openai.__version__}")
    
    # Check for v1.x specific modules and paths
    try:
        from openai.types.error import RateLimitError
        logger.info("✅ Successfully imported RateLimitError from openai.types.error")
        return True
    except ImportError:
        logger.error("❌ Failed to import RateLimitError from openai.types.error - incorrect OpenAI version?")
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Quick OpenAI client check")
    parser.add_argument("--offline", action="store_true",
                        help="Skip real API calls – always succeed")
    args = parser.parse_args()

    if args.offline or os.getenv("OFFLINE_MODE", "false").lower() == "true":
        logger.info("Running in OFFLINE mode – skipping OpenAI requests. ✅")
        sys.exit(0)
    
    logger.info("=" * 50)
    logger.info("OpenAI Client Test Utility")
    logger.info("=" * 50)
    
    # First verify import paths
    import_check = verify_import_paths()
    if not import_check:
        logger.error("Import path verification failed. You may need to upgrade the OpenAI package.")
        logger.info("Run: pip install -U 'openai>=1.3.0'")
        sys.exit(1)
    
    # Test sync client
    logger.info("\nTesting synchronous client:")
    sync_success = test_sync_client()
    
    # Test async client
    logger.info("\nTesting asynchronous client:")
    async_success = run_async_test()
    
    # Print results
    logger.info("\n" + "=" * 50)
    logger.info("Test Results:")
    logger.info(f"Synchronous client: {'✅ PASSED' if sync_success else '❌ FAILED'}")
    logger.info(f"Asynchronous client: {'✅ PASSED' if async_success else '❌ FAILED'}")
    
    if sync_success and async_success:
        logger.info("All tests passed! OpenAI client is working correctly.")
        sys.exit(0)
    else:
        logger.error("Some tests failed. See logs above for details.")
        sys.exit(1)
